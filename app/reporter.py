"""报告生成层：把多源采集 + LLM 抽取的 KPI 整合成 Markdown ESG 报告。

流程：
1. 收集 ESGAggregate（多源采集结果） + ESGKPIs（PDF 抽取结果） 作为输入
2. 计算数据完整度（按字段填率）
3. 用模板生成各章节 KPI 表格（确定性逻辑，无 LLM）
4. 用 LLM 生成叙述章节（基于 KPI + benchmark 上下文）
5. 拼接成完整 Markdown

LLM 协议：OpenAI Chat Completions 兼容（OpenAI / DeepSeek / Ollama / vLLM 都可）。
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from . import standards

from openai import OpenAI

from .config import settings
from .schemas import (
    ESGAggregate,
    ESGKPIs,
    ESGReport,
    GHGEmissions,
    HKGovIndicator,
    ReportSection,
)

logger = logging.getLogger(__name__)


def split_markdown_sections(md: str) -> list[str]:
    """按二级标题切分报告 Markdown。

    第一段包含一级标题与元信息，其后每段以 `## ` 开头。用于试读版只放出前几章。
    """
    parts: list[str] = []
    buf: list[str] = []
    for line in md.splitlines():
        if line.startswith("## ") and buf:
            parts.append("\n".join(buf).strip())
            buf = [line]
        else:
            buf.append(line)
    if buf:
        parts.append("\n".join(buf).strip())
    return [p for p in parts if p]



# =========================================================
# 数据完整度评估
# =========================================================

def _count_filled(obj) -> tuple[int, int]:
    """返回 (已填字段数, 总字段数)，递归遍历 BaseModel 字段。
    None / 空 dict / 空字符串视为未填；raw_text 不计入。
    """
    filled = 0
    total = 0
    fields = obj.__class__.model_fields
    for name in fields:
        if name in ("raw_text", "unit"):
            continue
        val = getattr(obj, name, None)
        if name == "raw":
            continue
        total += 1
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        if isinstance(val, dict) and not val:
            continue
        if isinstance(val, list) and not val:
            continue
        filled += 1
    return filled, total


def _overall_data_quality(
    aggregate: ESGAggregate, kpis: Optional[ESGKPIs]
) -> float:
    """综合数据完整度：采集层权重 0.4 + 抽取层权重 0.6。"""
    # 采集层
    agg_filled, agg_total = 0, 0
    f, t = _count_filled(aggregate.company)
    agg_filled += f; agg_total += t
    if aggregate.yahoo_esg:
        f, t = _count_filled(aggregate.yahoo_esg)
        agg_filled += f; agg_total += t
    if aggregate.nzdpu_emissions:
        f, t = _count_filled(aggregate.nzdpu_emissions)
        agg_filled += f; agg_total += t
    if aggregate.hk_benchmark:
        f, t = _count_filled(aggregate.hk_benchmark)
        agg_filled += f; agg_total += t
    if aggregate.hkex_reports:
        agg_filled += 1; agg_total += 1
    agg_ratio = agg_filled / agg_total if agg_total else 0.0

    # 抽取层
    kpi_ratio = 0.0
    if kpis:
        kpi_filled, kpi_total = 0, 0
        for sub in (kpis.ghg_emissions, kpis.energy, kpis.water_and_waste,
                    kpis.workforce, kpis.governance):
            f, t = _count_filled(sub)
            kpi_filled += f; kpi_total += t
        kpi_ratio = kpi_filled / kpi_total if kpi_total else 0.0

    return round(0.4 * agg_ratio + 0.6 * kpi_ratio, 3)


# =========================================================
# 确定性 KPI 表格（不调 LLM）
# =========================================================

def _fmt_num(v, unit: str = "") -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        s = f"{v:,.2f}".rstrip("0").rstrip(".")
    else:
        s = f"{v:,}"
    return f"{s} {unit}".strip()


def _ghg_table(ghg: GHGEmissions, benchmark: Optional[HKGovIndicator]) -> str:
    rows = [
        ("Scope 1", _fmt_num(ghg.scope1, "tCO2e")),
        ("Scope 2 (market-based)", _fmt_num(ghg.scope2_market_based, "tCO2e")),
        ("Scope 2 (location-based)", _fmt_num(ghg.scope2_location_based, "tCO2e")),
        ("Scope 3 (total)", _fmt_num(ghg.scope3_total, "tCO2e")),
        ("Scope 3 categories", ", ".join(
            f"Cat{k}={v:,.0f}" for k, v in ghg.scope3_categories.items()
        ) or "N/A"),
        ("Operations total (Scope 1+2)", _fmt_num(ghg.operations_total, "tCO2e")),
        ("Intensity (tCO2e / 百万营收，币种按披露)", _fmt_num(ghg.intensity_revenue)),
        ("Reduction vs baseline", _fmt_num(ghg.reduction_vs_baseline_pct, "%")),
        ("Baseline year", str(ghg.baseline_year) if ghg.baseline_year else "N/A"),
    ]
    if benchmark:
        rows.append((
            f"Hong Kong {benchmark.year} total GHG",
            _fmt_num(benchmark.value, benchmark.unit)
        ))
    md = "| Metric | Value |\n|---|---|\n"
    for k, v in rows:
        md += f"| {k} | {v} |\n"
    return md


def _workforce_table(wf) -> str:
    md = "| Metric | Value |\n|---|---|\n"
    md += f"| Total employees | {_fmt_num(wf.total_employees)} |\n"
    md += f"| Female ratio | {_fmt_num(wf.female_ratio, '%')} |\n"
    md += f"| Turnover rate | {_fmt_num(wf.turnover_rate, '%')} |\n"
    md += f"| Training hours (avg) | {_fmt_num(wf.training_hours_avg, 'h')} |\n"
    md += f"| Fatal incidents | {_fmt_num(wf.fatal_incidents)} |\n"
    md += f"| Lost-time injury rate | {_fmt_num(wf.lost_time_injury_rate)} |\n"
    return md


def _governance_table(g) -> str:
    md = "| Metric | Value |\n|---|---|\n"
    md += f"| Board size | {_fmt_num(g.board_size)} |\n"
    md += f"| Independent directors ratio | {_fmt_num(g.independent_directors_ratio, '%')} |\n"
    md += f"| Female directors ratio | {_fmt_num(g.female_directors_ratio, '%')} |\n"
    esg_committee = "Yes" if g.esg_committee_exists else ("No" if g.esg_committee_exists is False else "N/A")
    linked = "Yes" if g.linked_to_remuneration else ("No" if g.linked_to_remuneration is False else "N/A")
    md += f"| ESG committee exists | {esg_committee} |\n"
    md += f"| ESG linked to remuneration | {linked} |\n"
    return md


# =========================================================
# LLM 生成叙述章节
# =========================================================

NARRATIVE_SYSTEM_PROMPT = """你是一个 ESG 报告写作助手，根据给定的结构化 KPI 数据和行业基准，
为指定章节生成 200-400 字的叙述性分析文字（中文）。

要求：
1. 只引用给定数据，禁止编造。
2. 缺失字段如实说明"未披露"或"数据缺失"，不回避。
3. 语气客观、第三方视角，类似 ISSB / TCFD 报告的撰写风格。
4. 末尾用 1-2 句给出该维度的简评（领先 / 平稳 / 落后）或风险提示。
5. 输出纯文本，不要 Markdown 标题、列表符号或代码块。"""

NARRATIVE_USER_TEMPLATE = """公司：{company_name}（{stock_code}），{report_year} 年 ESG 报告
行业/板块：{sector}

本章节（{section_name}）KPI 数据（JSON）：
{kpi_json}

香港行业基准（如适用）：
{benchmark_json}

抽取层 notes（LLM 在 PDF 抽取时记录的注意事项）：
{notes}

请生成 {section_name} 章节叙述文字。"""


def _get_client() -> OpenAI:
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY 未配置")
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_api_base)


def _generate_narrative(
    section_name: str,
    company_name: str,
    stock_code: str,
    report_year: int,
    sector: str,
    kpi_json: str,
    benchmark_json: str,
    notes: list[str],
) -> str:
    """调 LLM 生成单章节叙述。"""
    client = _get_client()
    prompt = NARRATIVE_USER_TEMPLATE.format(
        company_name=company_name,
        stock_code=stock_code,
        report_year=report_year,
        sector=sector or "未披露",
        section_name=section_name,
        kpi_json=kpi_json,
        benchmark_json=benchmark_json,
        notes="\n".join(f"- {n}" for n in notes) or "(无)",
    )
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()


# =========================================================
# 主入口：生成完整报告
# =========================================================

def generate_report(
    aggregate: ESGAggregate,
    kpis: Optional[ESGKPIs] = None,
    use_llm_narrative: bool = True,
) -> ESGReport:
    """整合 ESGAggregate + ESGKPIs → ESGReport（Markdown）。"""
    company = aggregate.company
    company_name = company.name or (kpis.company_name if kpis else "Unknown")
    stock_code = company.hkex_code or (kpis.stock_code if kpis else "")
    ticker = company.ticker
    report_year = kpis.report_year if kpis else date.today().year
    sector = company.sector or company.industry or "未披露"

    # 数据源"是否真有值"的判定：对象存在不代表有数据
    yahoo_ok = bool(
        aggregate.yahoo_esg and aggregate.yahoo_esg.total_score is not None
    )
    nzdpu_ok = bool(
        aggregate.nzdpu_emissions
        and any(
            v is not None
            for v in (
                aggregate.nzdpu_emissions.scope1,
                aggregate.nzdpu_emissions.scope2,
                aggregate.nzdpu_emissions.scope3,
            )
        )
    )

    sources = ["Yahoo Finance (yfinance)"]
    if yahoo_ok:
        sources.append("Yahoo ESG scores")
    if nzdpu_ok:
        sources.append("NZDPU")
    if aggregate.hkex_reports:
        sources.append(f"HKEX reports ({len(aggregate.hkex_reports)} 份)")
    if aggregate.hk_benchmark:
        sources.append("HK Gov Open Data")
    if aggregate.sbti_target and aggregate.sbti_target.targets_committed:
        sources.append("SBTi（科学碳目标）")
    if aggregate.sina_esg and aggregate.sina_esg.ratings:
        sources.append(f"新浪财经ESG评级（查询 {len(aggregate.sina_esg.ratings)} 家机构）")
    if aggregate.country_emission:
        sources.append("World Bank 国家排放基准")
    if kpis and kpis.source_pdf:
        sources.append(f"LLM 抽取自 {Path(kpis.source_pdf).name}")

    sections: list[ReportSection] = []
    benchmark = aggregate.hk_benchmark
    benchmark_json = (
        benchmark.model_dump_json(indent=2) if benchmark else "{}"
    )

    # ---- ESG 评分概述 ----
    esg_score_text = "Yahoo/Sustainalytics 风险评分未获取"
    if aggregate.yahoo_esg and aggregate.yahoo_esg.total_score is not None:
        esg_score_text = (
            f"Yahoo/Sustainalytics ESG 风险总分 {aggregate.yahoo_esg.total_score:.1f}"
            "（分数越低，未管理 ESG 风险通常越小），"
            f"E {aggregate.yahoo_esg.environmental_score}, "
            f"S {aggregate.yahoo_esg.social_score}, "
            f"G {aggregate.yahoo_esg.governance_score}"
        )
    # SBTi 目标信息
    sbti_text = ""
    if aggregate.sbti_target and aggregate.sbti_target.targets_committed:
        parts = []
        if aggregate.sbti_target.near_term_target_set:
            y = aggregate.sbti_target.target_year_near_term
            parts.append(f"近期目标（{y} 年）")
        if aggregate.sbti_target.net_zero_target_set:
            y = aggregate.sbti_target.target_year_net_zero
            parts.append(f"净零目标（{y} 年）")
        sbti_text = f"；已加入 SBTi，设定{'、'.join(parts) or '碳目标'}"
    # 新浪多机构 ESG 评级信息
    esg_ratings_text = ""
    if aggregate.sina_esg and aggregate.sina_esg.ratings:
        msci = aggregate.sina_esg.msci_rating
        csi = aggregate.sina_esg.csi_rating
        rated = [r for r in aggregate.sina_esg.ratings if r.rating]
        parts = []
        if msci:
            parts.append(f"MSCI {msci}")
        if csi:
            parts.append(f"中证指数 {csi}")
        if parts:
            esg_ratings_text = (
                f"；多机构评级：{'、'.join(parts)}"
                f"（共查询 {len(aggregate.sina_esg.ratings)} 家机构，{len(rated)} 家有公开评级）"
            )
    rating_guide = (
        "\n\n**评级阅读说明**：各机构评价范围、行业权重、数据年份和评分尺度不同，"
        "原始评级不可直接横向比较或简单平均；Yahoo/Sustainalytics 是风险评分，通常分数越低、"
        "未管理风险越小；SBTi 是科学碳目标状态，不是综合 ESG 评级。"
        "“暂无公开评级”表示未覆盖、未公开或本次未取到，不等于 0 分。"
        "评级结果不构成审计、鉴证或投资意见。"
    ) if esg_ratings_text else ""
    overview_narr = (
        f"{company_name}（港交所代码 {stock_code}，Yahoo ticker {ticker}）"
        f"在 {report_year} 年的 ESG 表现综述。行业/板块：{sector}。\n\n"
        f"ESG 评分：{esg_score_text}{sbti_text}{esg_ratings_text}。\n"
        f"数据来源：{', '.join(sources)}。\n"
        f"本报告综合采集层（{len(aggregate.hkex_reports or [])} 份 HKEX 报告 + "
        f"{'有' if benchmark else '无'}香港行业基准）"
        f"与 LLM 抽取层（信心 {kpis.extraction_confidence:.2f}）的数据。"
        f"{rating_guide}"
    )
    sections.append(ReportSection(
        title="一、公司概况与 ESG 评分",
        narrative=overview_narr,
    ))

    # ---- 披露覆盖与标准映射 ----
    if kpis:
        gh = kpis.ghg_emissions
        available: list[str] = []
        if gh.scope1 is not None:
            available.append("scope1")
        if gh.scope2_market_based is not None or gh.scope2_location_based is not None:
            available.append("scope2")
        if getattr(gh, "scope3_total", None) is not None:
            available.append("scope3")
        if kpis.energy.total_consumption is not None:
            available.append("energy")
        if kpis.water_and_waste.water_withdrawal is not None:
            available.append("water")
        if (kpis.water_and_waste.hazardous_waste is not None
                or kpis.water_and_waste.non_hazardous_waste is not None):
            available.append("waste")
        wf = kpis.workforce
        if wf.total_employees is not None:
            available.append("employees")
        if wf.female_ratio is not None:
            available.append("female_ratio")
        if wf.turnover_rate is not None:
            available.append("turnover")
        if wf.fatal_incidents is not None:
            available.append("fatalities")
        if wf.lost_time_injury_rate is not None:
            available.append("injury_rate")
        if wf.training_hours_avg is not None:
            available.append("training_hours")
        coverage = standards.check(available)
        sections.append(ReportSection(
            title="二、披露覆盖与标准映射",
            narrative=(
                "下表把本次取得的数据对照港交所《ESG 报告守则》与 IFRS S2 的披露要求，"
                "用于快速看出哪些条款已有数据支撑、哪些仍需补充。"
                "本表只核对数据是否存在，不评价披露质量，也不构成合规意见。"
            ),
            kpi_table=standards.to_markdown(coverage),
        ))

    # ---- 温室气体排放 ----
    if kpis:
        ghg = kpis.ghg_emissions
        kpi_table = _ghg_table(ghg, benchmark)
        section_notes = [n for n in kpis.notes if any(
            k in n.lower() for k in ("ghg", "emission", "scope", "financed", "operations")
        )]
        if use_llm_narrative:
            try:
                narrative = _generate_narrative(
                    section_name="温室气体排放",
                    company_name=company_name,
                    stock_code=stock_code,
                    report_year=report_year,
                    sector=sector,
                    kpi_json=ghg.model_dump_json(indent=2),
                    benchmark_json=benchmark_json,
                    notes=section_notes,
                )
            except Exception as e:
                logger.warning(f"GHG 叙述生成失败: {e}")
                narrative = f"温室气体排放章节叙述生成失败：{e}"
        else:
            narrative = ghg.raw_text or "(无原文摘录)"
        sections.append(ReportSection(
            title="三、温室气体排放",
            narrative=narrative,
            kpi_table=kpi_table,
            data_quality_notes=section_notes,
        ))

    # ---- 员工与社会 ----
    if kpis:
        wf = kpis.workforce
        kpi_table = _workforce_table(wf)
        section_notes = [n for n in kpis.notes if any(
            k in n.lower() for k in ("workforce", "employee", "gender", "female", "training", "turnover")
        )]
        if use_llm_narrative:
            try:
                narrative = _generate_narrative(
                    section_name="员工与社会",
                    company_name=company_name,
                    stock_code=stock_code,
                    report_year=report_year,
                    sector=sector,
                    kpi_json=wf.model_dump_json(indent=2),
                    benchmark_json="{}",
                    notes=section_notes,
                )
            except Exception as e:
                logger.warning(f"员工与社会叙述生成失败: {e}")
                narrative = f"员工与社会章节叙述生成失败：{e}"
        else:
            narrative = wf.raw_text or "(无原文摘录)"
        sections.append(ReportSection(
            title="四、员工与社会",
            narrative=narrative,
            kpi_table=kpi_table,
            data_quality_notes=section_notes,
        ))

    # ---- 治理 ----
    if kpis:
        g = kpis.governance
        kpi_table = _governance_table(g)
        section_notes = [n for n in kpis.notes if any(
            k in n.lower() for k in ("governance", "board", "director", "committee")
        )]
        if use_llm_narrative:
            try:
                narrative = _generate_narrative(
                    section_name="公司治理",
                    company_name=company_name,
                    stock_code=stock_code,
                    report_year=report_year,
                    sector=sector,
                    kpi_json=g.model_dump_json(indent=2),
                    benchmark_json="{}",
                    notes=section_notes,
                )
            except Exception as e:
                logger.warning(f"治理叙述生成失败: {e}")
                narrative = f"治理章节叙述生成失败：{e}"
        else:
            narrative = g.raw_text or "(无原文摘录)"
        sections.append(ReportSection(
            title="五、公司治理",
            narrative=narrative,
            kpi_table=kpi_table,
            data_quality_notes=section_notes,
        ))

    # ---- 数据质量与方法学 ----
    quality = _overall_data_quality(aggregate, kpis)
    quality_notes: list[str] = []
    if not yahoo_ok:
        quality_notes.append("Yahoo ESG 评分未获取（yfinance 限流或该标的无评分）")
    if not nzdpu_ok:
        quality_notes.append("NZDPU 排放数据未获取（缺少 API token 或公司未匹配）")
    if not aggregate.hk_benchmark:
        quality_notes.append("香港行业基准数据未获取")
    if aggregate.sbti_target and not aggregate.sbti_target.targets_committed:
        quality_notes.append("SBTi：该公司未设定公开科学碳目标（或未匹配到 SBTi 记录）")
    if not aggregate.sina_esg or not aggregate.sina_esg.ratings:
        quality_notes.append("新浪财经多机构 ESG 评级未获取")
    if not aggregate.country_emission:
        quality_notes.append("World Bank 国家排放基准未获取")
    if kpis and kpis.extraction_confidence < 0.6:
        quality_notes.append(f"LLM 抽取信心较低（{kpis.extraction_confidence:.2f}）")
    if kpis:
        quality_notes.extend(kpis.notes[:5])

    quality_narr = (
        f"本报告综合数据完整度为 {quality:.1%}。\n"
        f"采集层：{len(aggregate.hkex_reports or [])} 份 HKEX 报告；"
        f"{'已获取' if benchmark else '未获取'}香港行业基准；"
        f"{'已获取' if yahoo_ok else '未获取'} Yahoo ESG 评分；"
        f"{'已获取' if nzdpu_ok else '未获取'} NZDPU 排放数据。\n"
        f"抽取层：LLM 自评信心 {kpis.extraction_confidence:.2f}（0-1）。"
    )
    sections.append(ReportSection(
        title="六、数据质量与方法学说明",
        narrative=quality_narr,
        data_quality_notes=quality_notes,
    ))

    # ---- 拼接 Markdown ----
    md = f"# {company_name} ESG 报告 {report_year}\n\n"
    md += f"> 港交所代码: **{stock_code}**  \n"
    md += f"> Yahoo ticker: **{ticker}**  \n"
    md += f"> 行业/板块: **{sector}**  \n"
    md += f"> 生成日期: {date.today().isoformat()}  \n"
    md += f"> 综合数据完整度: **{quality:.1%}**\n\n"
    md += f"**数据来源**: {', '.join(sources)}\n\n"
    md += "---\n\n"
    for sec in sections:
        md += f"## {sec.title}\n\n"
        md += f"{sec.narrative}\n\n"
        if sec.kpi_table:
            md += sec.kpi_table + "\n"
        if sec.data_quality_notes:
            md += "**数据质量备注**:\n\n"
            for n in sec.data_quality_notes:
                md += f"- {n}\n"
            md += "\n"
        md += "---\n\n"

    return ESGReport(
        title=f"{company_name} ESG 报告 {report_year}",
        company_name=company_name,
        stock_code=stock_code,
        ticker=ticker,
        report_year=report_year,
        generated_at=date.today(),
        data_sources=sources,
        sections=sections,
        overall_data_quality=quality,
        markdown=md,
    )


def save_report(report: ESGReport, out_dir=None) -> "Path":
    """报告落盘 Markdown + JSON。"""
    from pathlib import Path
    out_dir = out_dir or (settings.cache_dir / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = report.company_name.replace(" ", "_").replace("/", "_")
    base = out_dir / f"{report.stock_code}_{report.report_year}"
    base.with_suffix(".md").write_text(report.markdown, encoding="utf-8")
    base.with_suffix(".json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    return base.with_suffix(".md")


# =========================================================
# Peer-based 报告：为非上市公司基于行业标杆生成
# =========================================================

PEER_NARRATIVE_SYSTEM_PROMPT = """你是一个 ESG 顾问，正在为一家未上市/数据缺失的公司撰写 ESG 报告。
已提供该公司所处行业的标杆上市公司 ESG 数据（peer benchmark），
以及该公司自报的少量 KPI（可能为空）。

任务：
1. 用标杆数据作为"行业基线"，在叙述中给出"行业典型水平"参考
2. 对每个 KPI，说明该公司是否已披露，未披露时基于行业典型水平给出"预估范围"
3. 明确区分"已披露数据" vs "行业预估值"，禁止把预估值说成该公司实际值
4. 在每个章节末尾给出"差距分析"：该公司相对行业标杆的位置（领先/平稳/落后/待评估）
5. 语气客观第三方，类似 ISSB 报告风格
6. 输出纯文本，不要 Markdown 标题或列表符号"""

PEER_NARRATIVE_USER_TEMPLATE = """公司：{company_name}（未上市/数据缺失），行业：{industry}
报告年份：{report_year}

行业标杆公司（peer benchmark）信息：
  标杆公司：{peer_name}（{peer_stock_code}）
  标杆披露说明：{peer_notes}
  标杆 KPI 数据（JSON）：
{peer_kpi_json}

香港行业基准（如适用）：
{benchmark_json}

本公司自报 KPI（用户输入，可能为空，JSON）：
{self_kpi_json}

请生成 {section_name} 章节叙述文字（200-400 字），重点说明：
- 该 KPI 在行业典型水平是多少（基于标杆）
- 该公司是否披露该 KPI
- 如果未披露，给出基于行业水平的合理预估范围
- 差距分析（领先/平稳/落后/待评估）
"""


def _generate_peer_narrative(
    section_name: str,
    company_name: str,
    industry: str,
    report_year: int,
    peer_name: str,
    peer_stock_code: str,
    peer_notes: str,
    peer_kpi_json: str,
    benchmark_json: str,
    self_kpi_json: str,
) -> str:
    """调 LLM 生成 peer-based 单章节叙述。"""
    client = _get_client()
    prompt = PEER_NARRATIVE_USER_TEMPLATE.format(
        company_name=company_name,
        industry=industry,
        report_year=report_year,
        peer_name=peer_name,
        peer_stock_code=peer_stock_code,
        peer_notes=peer_notes,
        peer_kpi_json=peer_kpi_json,
        benchmark_json=benchmark_json,
        self_kpi_json=self_kpi_json,
        section_name=section_name,
    )
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": PEER_NARRATIVE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()


def generate_peer_report(
    company_name: str,
    industry: str,
    report_year: int,
    peer_kpis: ESGKPIs,
    peer_company_name: str,
    peer_stock_code: str,
    peer_notes: str = "",
    self_kpis: Optional[ESGKPIs] = None,
    hk_benchmark: Optional[HKGovIndicator] = None,
    use_llm_narrative: bool = True,
) -> ESGReport:
    """为非上市公司基于行业标杆生成 ESG 报告。

    Args:
        company_name: 目标公司名
        industry: 行业中文名（如"地产"、"银行"）
        report_year: 报告年份
        peer_kpis: 行业标杆公司已抽取的 ESGKPIs
        peer_company_name: 标杆公司名
        peer_stock_code: 标杆公司代码
        peer_notes: 标杆公司披露质量备注
        self_kpis: 目标公司自报 KPI（可选，若用户提供）
        hk_benchmark: 香港行业基准
        use_llm_narrative: 是否调 LLM 生成叙述
    """
    sources = [
        f"行业标杆：{peer_company_name}（{peer_stock_code}）",
        f"标杆 PDF：{Path(peer_kpis.source_pdf).name}",
    ]
    if hk_benchmark:
        sources.append("HK Gov Open Data")
    if self_kpis:
        sources.append("本公司自报 KPI")

    sections: list[ReportSection] = []
    benchmark_json = (
        hk_benchmark.model_dump_json(indent=2) if hk_benchmark else "{}"
    )
    self_kpi_json = (
        self_kpis.model_dump_json(indent=2) if self_kpis else "{}"
    )

    # ---- 概述章节 ----
    overview_narr = (
        f"{company_name}（非上市/数据缺失公司）的 {report_year} 年 ESG 报告。\n\n"
        f"行业：{industry}。\n"
        f"参考标杆：{peer_company_name}（港交所代码 {peer_stock_code}）。"
        f"标杆披露质量备注：{peer_notes or '无'}。\n\n"
        f"本报告基于行业标杆公司的披露数据作为'行业典型水平'基线，"
        f"对该公司各 ESG 维度做差距分析。"
        f"{'本公司自报 KPI 部分字段，' if self_kpis else '本公司未提供任何 KPI，'}"
        f"未披露字段基于行业水平给出预估范围（明确标注为预估）。"
    )
    sections.append(ReportSection(
        title="一、公司概况与 ESG 评分",
        narrative=overview_narr,
    ))

    # ---- 温室气体排放 ----
    peer_ghg = peer_kpis.ghg_emissions
    peer_ghg_table = _ghg_table(peer_ghg, hk_benchmark)
    section_notes = [
        f"标杆 {peer_company_name} 披露说明：{peer_notes or '无'}",
        *peer_kpis.notes,
    ]
    if use_llm_narrative:
        try:
            narrative = _generate_peer_narrative(
                section_name="温室气体排放",
                company_name=company_name,
                industry=industry,
                report_year=report_year,
                peer_name=peer_company_name,
                peer_stock_code=peer_stock_code,
                peer_notes=peer_notes,
                peer_kpi_json=peer_ghg.model_dump_json(indent=2),
                benchmark_json=benchmark_json,
                self_kpi_json=self_kpis.ghg_emissions.model_dump_json(indent=2) if self_kpis else "{}",
            )
        except Exception as e:
            logger.warning(f"peer GHG 叙述生成失败: {e}")
            narrative = f"温室气体排放章节叙述生成失败：{e}"
    else:
        narrative = peer_ghg.raw_text or "(无原文摘录)"
    sections.append(ReportSection(
        title="二、温室气体排放（参考行业标杆）",
        narrative=narrative,
        kpi_table=peer_ghg_table,
        data_quality_notes=section_notes,
    ))

    # ---- 员工与社会 ----
    peer_wf = peer_kpis.workforce
    peer_wf_table = _workforce_table(peer_wf)
    if use_llm_narrative:
        try:
            narrative = _generate_peer_narrative(
                section_name="员工与社会",
                company_name=company_name,
                industry=industry,
                report_year=report_year,
                peer_name=peer_company_name,
                peer_stock_code=peer_stock_code,
                peer_notes=peer_notes,
                peer_kpi_json=peer_wf.model_dump_json(indent=2),
                benchmark_json="{}",
                self_kpi_json=self_kpis.workforce.model_dump_json(indent=2) if self_kpis else "{}",
            )
        except Exception as e:
            logger.warning(f"peer 员工叙述生成失败: {e}")
            narrative = f"员工与社会章节叙述生成失败：{e}"
    else:
        narrative = peer_wf.raw_text or "(无原文摘录)"
    sections.append(ReportSection(
        title="三、员工与社会（参考行业标杆）",
        narrative=narrative,
        kpi_table=peer_wf_table,
    ))

    # ---- 治理 ----
    peer_g = peer_kpis.governance
    peer_g_table = _governance_table(peer_g)
    if use_llm_narrative:
        try:
            narrative = _generate_peer_narrative(
                section_name="公司治理",
                company_name=company_name,
                industry=industry,
                report_year=report_year,
                peer_name=peer_company_name,
                peer_stock_code=peer_stock_code,
                peer_notes=peer_notes,
                peer_kpi_json=peer_g.model_dump_json(indent=2),
                benchmark_json="{}",
                self_kpi_json=self_kpis.governance.model_dump_json(indent=2) if self_kpis else "{}",
            )
        except Exception as e:
            logger.warning(f"peer 治理叙述生成失败: {e}")
            narrative = f"治理章节叙述生成失败：{e}"
    else:
        narrative = peer_g.raw_text or "(无原文摘录)"
    sections.append(ReportSection(
        title="四、公司治理（参考行业标杆）",
        narrative=narrative,
        kpi_table=peer_g_table,
    ))

    # ---- 数据质量与方法学 ----
    quality_notes: list[str] = [
        f"本报告基于行业标杆 {peer_company_name}（{peer_stock_code}）的披露数据生成",
        f"标杆 LLM 抽取信心：{peer_kpis.extraction_confidence:.2f}",
        "未披露字段基于行业水平预估，预估数据已在叙述中明确标注",
    ]
    if not self_kpis:
        quality_notes.append("本公司未提供任何自报 KPI，全部参考行业标杆")
    if hk_benchmark:
        quality_notes.append(f"已获取香港 {hk_benchmark.year} 行业基准数据")
    quality_notes.extend(peer_kpis.notes[:3])

    quality_narr = (
        f"本报告为非上市/数据缺失公司基于行业标杆生成的参考性 ESG 报告。\n"
        f"标杆公司：{peer_company_name}（{peer_stock_code}），行业：{industry}。\n"
        f"标杆 LLM 抽取信心：{peer_kpis.extraction_confidence:.2f}。\n"
        f"{'已获取' if hk_benchmark else '未获取'}香港行业基准数据。\n"
        f"{'本公司提供部分自报 KPI。' if self_kpis else '本公司未提供自报 KPI，全部参考行业标杆。'}\n\n"
        f"重要提示：本报告中所有 KPI 数据均为行业标杆公司的实际披露值，"
        f"用于参考对比目的，不代表本公司的实际绩效。"
        f"建议本公司后续开展 ESG 数据收集与披露，以便进行精确评估。"
    )
    sections.append(ReportSection(
        title="五、数据质量与方法学说明",
        narrative=quality_narr,
        data_quality_notes=quality_notes,
    ))

    # ---- 拼接 Markdown ----
    md = f"# {company_name} ESG 报告 {report_year}（基于行业标杆）\n\n"
    md += f"> 公司类型: **非上市/数据缺失**  \n"
    md += f"> 行业: **{industry}**  \n"
    md += f"> 参考标杆: **{peer_company_name}（{peer_stock_code}）**  \n"
    md += f"> 生成日期: {date.today().isoformat()}  \n\n"
    md += (
        f"**⚠️ 重要提示**：本报告基于行业标杆公司的披露数据生成，"
        f"所有 KPI 数值均为标杆公司实际值，仅作参考对比用途，"
        f"不代表本公司的实际绩效。建议后续开展自有数据收集与披露。\n\n"
    )
    md += f"**数据来源**: {', '.join(sources)}\n\n"
    md += "---\n\n"
    for sec in sections:
        md += f"## {sec.title}\n\n"
        md += f"{sec.narrative}\n\n"
        if sec.kpi_table:
            md += sec.kpi_table + "\n"
        if sec.data_quality_notes:
            md += "**数据质量备注**:\n\n"
            for n in sec.data_quality_notes:
                md += f"- {n}\n"
            md += "\n"
        md += "---\n\n"

    # 综合数据完整度：标杆抽取信心 + 是否有自报数据 + 是否有行业基准
    quality = 0.0
    quality += 0.5 * peer_kpis.extraction_confidence
    quality += 0.2 if self_kpis else 0.0
    quality += 0.2 if hk_benchmark else 0.0
    quality += 0.1  # 行业匹配本身的价值
    quality = min(quality, 1.0)

    return ESGReport(
        title=f"{company_name} ESG 报告 {report_year}（基于行业标杆）",
        company_name=company_name,
        stock_code=f"PEER:{peer_stock_code}",  # 非上市公司无自身代码
        ticker="N/A",
        report_year=report_year,
        generated_at=date.today(),
        data_sources=sources,
        sections=sections,
        overall_data_quality=round(quality, 3),
        markdown=md,
    )
