"""LLM 抽取层：从 HKEX ESG PDF 中抽取结构化 KPI。

流程：
1. 用 pypdf 提取全文，按页缓存
2. 关键词筛选 ESG 相关页面（控制 token 成本）
3. 把相关页拼成 context 喂给 LLM
4. LLM 以 JSON schema 输出，pydantic 校验后落盘

LLM 协议：OpenAI Chat Completions 兼容（OpenAI / DeepSeek / Ollama / vLLM 都可）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

from openai import OpenAI
from pypdf import PdfReader
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import settings
from .schemas import (
    ESGKPIs,
    GHGEmissions,
    Energy,
    Governance,
    WaterAndWaste,
    Workforce,
)

logger = logging.getLogger(__name__)

# 关键词组：用于过滤"ESG 相关页"
# 用 lower-case 匹配；命中任一关键词的页面进入 context
ESG_KEYWORDS = (
    "scope 1", "scope 2", "scope 3", "scope1", "scope2", "scope3",
    "ghg", "emission", "carbon", "energy consumption",
    "renewable", "water withdrawal", "waste",
    "employee", "turnover", "training", "diversity",
    "board of director", "independent director", "esg committee",
    "lost time injury", "fatality",
    "tcfd", "issb", "sasb", "gri",
    # 金融业特有
    "financed emissions", "financed emission", "net zero", "decarbonisation",
    "carbon intensity", "climate target", "sectoral target",
    "board composition", "risk committee", "audit committee",
    "gender pay", "female representation", "women in leadership",
    "non-financial", "sustainability report",
)

# 每页字符上限（防止某些页全是表格数字爆炸）
PAGE_CHAR_LIMIT = 6000
# 总 context 字符上限（DeepSeek 上下文 64K，留出 prompt 空间）
CONTEXT_CHAR_LIMIT = 50000


def extract_pages(pdf_path: Path | str) -> list[str]:
    """用 pypdf 提取所有页文本，返回 list[str]，长度 = 页数。"""
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception as e:
            logger.warning(f"页面解析失败: {e}")
            pages.append("")
    return pages


def page_relevance_score(page: str) -> int:
    """页面相关度打分：关键词命中数 + 单位命中 + 数字密度（KPI 表格特征）。"""
    low = page.lower()
    score = sum(1 for k in ESG_KEYWORDS if k in low)
    for unit in ("tco2e", "co2e", "kwh", "mwh", "m3", "千立方米", "scope 3"):
        if unit in low:
            score += 2
    if page:
        digit_ratio = sum(ch.isdigit() for ch in page) / len(page)
        if digit_ratio > 0.12:  # 数字密集，通常是 KPI 汇总表
            score += 3
    return score


def filter_relevant_pages(pages: Iterable[str]) -> list[tuple[int, int, str]]:
    """保留命中 ESG 关键词的页面，返回 [(页序, 得分, 文本)]。"""
    scored: list[tuple[int, int, str]] = []
    for idx, page in enumerate(pages):
        low = page.lower()
        if any(k in low for k in ESG_KEYWORDS):
            scored.append((idx, page_relevance_score(page), page[:PAGE_CHAR_LIMIT]))
    return scored


DIMENSION_KEYWORDS = {
    "env": (
        "scope 1", "scope 2", "scope 3", "ghg", "emission", "carbon",
        "energy", "renewable", "water", "waste", "tco2e", "climate", "intensity",
    ),
    "social": (
        "employee", "turnover", "training", "diversity", "female", "gender",
        "injury", "fatality", "safety", "community", "labour", "labor",
    ),
    "gov": (
        "board of director", "independent director", "governance", "committee",
        "remuneration", "audit committee", "risk committee",
    ),
}


def page_dimension(page: str) -> str:
    """判断页面主要覆盖的 ESG 维度（env / social / gov / other）。"""
    low = page.lower()
    for dim, kws in DIMENSION_KEYWORDS.items():
        if any(k in low for k in kws):
            return dim
    return "other"


def build_context(relevant_pages: list[tuple[int, int, str]]) -> str:
    """按相关度优先选取页面，并保证 E/S/G 三维各至少一页。

    原实现按文档顺序截断，KPI 附录位于文末会被预算裁掉；
    只按得分排序又会挤掉治理等低数字密度章节，故两阶段选取。
    """
    if not relevant_pages:
        return ""
    ordered = sorted(relevant_pages, key=lambda t: t[1], reverse=True)
    chosen: list[tuple[int, str]] = []
    seen: set[int] = set()
    total = 0

    # 阶段一：每个维度保底 2 页
    for dim in ("env", "social", "gov"):
        picked = 0
        for idx, _score, page in ordered:
            if picked >= 2:
                break
            if idx in seen or page_dimension(page) != dim:
                continue
            if total + len(page) > CONTEXT_CHAR_LIMIT:
                continue
            chosen.append((idx, page))
            seen.add(idx)
            total += len(page)
            picked += 1

    # 阶段二：剩余预算按相关度补满
    for idx, _score, page in ordered:
        if idx in seen:
            continue
        if total + len(page) > CONTEXT_CHAR_LIMIT:
            continue
        chosen.append((idx, page))
        seen.add(idx)
        total += len(page)

    chosen.sort(key=lambda t: t[0])
    return "\n\n----PAGE----\n\n".join(
        f"=== PAGE {idx + 1} ===\n{page}" for idx, page in chosen
    )


SYSTEM_PROMPT = """你是一个 ESG 报告数据抽取助手。给定 HKEX 上市公司 ESG / 年报 PDF 的相关片段，
请提取结构化 KPI，并以 JSON 格式输出。

约束：
1. 单位换算：
   - 温室气体排放：最终以 tCO2e（吨 CO2 当量）为单位。若原文披露为"千吨"则 ×1000，
     为"百万吨"则 ×1,000,000，为"千公吨"同"千吨"处理。
   - 能源：MWh。
   - 水：千立方米。
2. 仅填写文本中明确出现的数据；不要编造或外推。
3. 字段找不到时填 null；对原文表格/段落要在对应 raw_text 字段贴一小段证据文本（≤200 字）。
4. scope3_categories 是 dict，键为 1-15 类别编号字符串，值为 tCO2e；只填文本中出现的类别。
5. extraction_confidence 是你对本次抽取结果整体质量的信心（0-1）。
6. notes 里写明单位换算依据、缺失字段原因等。
7. operations_total 严格定义为 Scope 1 + Scope 2 的合计（自身运营排放）。
   若原文只给出含 Scope 3 的"总排放"，不要填进 operations_total（填 null），
   只在 notes 里说明原文披露了含范围三的总量。

金融业特别说明：
- 银行/保险公司通常披露"operations emissions"（自身运营，含 Scope 1 + Scope 2，
  有时含 Scope 3 类别 6/7 商务差旅/员工通勤）和"financed emissions"（投融资排放，
  属 Scope 3 类别 15 投资排放）。
- 如果原文只给一个"absolute greenhouse gas emissions in our operations"总量数字
  （如 293,333 tCO2e）而没有 Scope 1 / 2 细分，请把这个总量填到 operations_total
  字段，scope1 / scope2_market_based / scope2_location_based 仍填 null。
- 把"financed emissions"放在 scope3_categories["15"] 里。
- 如果原文同时给绝对值和"vs 基线年减排率"，绝对值填到 operations_total，
  百分比填到 reduction_vs_baseline_pct，基线年份填到 baseline_year。
- 在 notes 里说明该公司是否区分了 operations / financed 两种披露口径，以及
  operations_total 是否=Scope1+Scope2 合计（部分公司含 Scope3 Cat6/7）。

输出必须严格是符合以下 JSON Schema 的对象（不要 Markdown、不要解释）：
{
  "company_name": "string",
  "stock_code": "string",
  "report_year": 0,
  "source_pdf": "string",
  "ghg_emissions": {
    "scope1": null, "scope2_market_based": null, "scope2_location_based": null,
    "scope3_total": null, "scope3_categories": {}, "intensity_revenue": null,
    "operations_total": null, "reduction_vs_baseline_pct": null,
    "baseline_year": null, "unit": "tCO2e", "raw_text": ""
  },
  "energy": {"total_consumption": null, "renewable_ratio": null,
             "intensity_revenue": null, "raw_text": ""},
  "water_and_waste": {"water_withdrawal": null, "water_intensity": null,
                      "hazardous_waste": null, "non_hazardous_waste": null,
                      "raw_text": ""},
  "workforce": {"total_employees": null, "female_ratio": null, "turnover_rate": null,
                "training_hours_avg": null, "fatal_incidents": null,
                "lost_time_injury_rate": null, "raw_text": ""},
  "governance": {"board_size": null, "independent_directors_ratio": null,
                 "female_directors_ratio": null, "esg_committee_exists": null,
                 "linked_to_remuneration": null, "raw_text": ""},
  "extraction_confidence": 0.0, "notes": []
}
"""

USER_PROMPT_TEMPLATE = """公司名称: {company_name}
股票代码: {stock_code}
报告年份: {report_year}
PDF 文件: {source_pdf}

PDF 相关片段（已按 ESG 关键词过滤，可能不完整）：
---
{context}
---

请按 System 给出的 JSON Schema 输出。"""


def _get_client() -> OpenAI:
    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY 未配置，请在 .env 中设置（OpenAI 兼容协议）"
        )
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type((Exception,)),
    reraise=True,
)
def _call_llm_extraction(system_prompt: str, user_prompt: str) -> str:
    """带重试的 LLM 抽取调用。瞬时网络/限流错误会自动重试 3 次。"""
    client = _get_client()
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _strip_code_fence(text: str) -> str:
    """LLM 偶尔会把 JSON 包在 ```json ... ``` 里，剥掉。"""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return m.group(1) if m else text


def extract_kpis_from_pdf(
    pdf_path: Path | str,
    company_name: str,
    stock_code: str,
    report_year: int,
) -> ESGKPIs:
    """主入口：PDF → ESGKPIs。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")

    logger.info(f"提取 PDF 文本: {pdf_path}")
    pages = extract_pages(pdf_path)
    relevant = filter_relevant_pages(pages)
    logger.info(f"总页 {len(pages)}，ESG 相关页 {len(relevant)}")
    context = build_context(relevant)
    if not context.strip():
        logger.warning("PDF 未提取到任何 ESG 相关文本，返回空 KPI")
        return ESGKPIs(
            company_name=company_name,
            stock_code=stock_code,
            report_year=report_year,
            source_pdf=str(pdf_path),
            notes=["no ESG-related text extracted"],
        )

    client = _get_client()  # 提前校验 key 是否配置，未配置时快速失败
    user_prompt = USER_PROMPT_TEMPLATE.format(
        company_name=company_name,
        stock_code=stock_code,
        report_year=report_year,
        source_pdf=str(pdf_path),
        context=context,
    )
    logger.info(
        f"调用 LLM: model={settings.llm_model} context_chars={len(context)}"
    )
    try:
        raw = _call_llm_extraction(SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.error(f"LLM 抽取调用失败（已重试 3 次）: {e}")
        raise RuntimeError(f"LLM 抽取失败: {e}") from e
    raw = _strip_code_fence(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"LLM 输出非合法 JSON: {e}; 原始: {raw[:300]}")
        raise

    # 强制填入已知字段，避免 LLM 漏回填
    data.setdefault("company_name", company_name)
    data.setdefault("stock_code", stock_code)
    data.setdefault("report_year", report_year)
    data.setdefault("source_pdf", str(pdf_path))

    kpis = ESGKPIs.model_validate(data)
    normalize_ratios(kpis)
    return kpis


def normalize_ratios(kpis: ESGKPIs) -> None:
    """把比例字段统一为百分数（0-100）。

    LLM 常把"28.93%"输出成 0.2893，而 schema 约定这些字段是百分数，
    直接展示会变成 0.29%。此处对 0-1 区间的小数做 ×100 归一。
    """

    def to_pct(v: float | None) -> float | None:
        if v is None:
            return None
        if 0 <= v <= 1:
            return round(v * 100, 2)
        return v

    wf = kpis.workforce
    wf.female_ratio = to_pct(wf.female_ratio)
    wf.turnover_rate = to_pct(wf.turnover_rate)
    g = kpis.governance
    g.independent_directors_ratio = to_pct(g.independent_directors_ratio)
    g.female_directors_ratio = to_pct(g.female_directors_ratio)
    kpis.energy.renewable_ratio = to_pct(kpis.energy.renewable_ratio)
    kpis.ghg_emissions.reduction_vs_baseline_pct = to_pct(
        kpis.ghg_emissions.reduction_vs_baseline_pct
    )


def save_kpis(kpis: ESGKPIs, out_dir: Path | None = None) -> Path:
    """抽取结果落盘 JSON，便于离线复用。"""
    out_dir = out_dir or (settings.cache_dir / "kpis")
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{kpis.stock_code}_{kpis.report_year}.json"
    target = out_dir / fname
    target.write_text(
        kpis.model_dump_json(indent=2), encoding="utf-8"
    )
    return target
