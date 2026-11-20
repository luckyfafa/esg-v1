"""非上市公司定制报告生成：客户填报数据 + 行业基线 → Markdown 报告。

与上市公司的报告（reporter.py）区别：
- 输入是客户填报值，不是 PDF 抽取，因此没有「抽取置信度」，改为「填报完整度」
- 章节重点是「现状 + 对标 + 差距 + 路线图」，而不是「披露合规梳理」
- 全部为确定性生成，不依赖 LLM，内网可离线运行
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from .client_data import derive_metrics, resolve_value
from .config import settings
from .rating_path import build_rating_path
from .schemas import (
    ClientAnalysis,
    ClientData,
    ClientMetric,
    ESGReport,
    ReportSection,
)

logger = logging.getLogger(__name__)

# 各行业的核心实质性议题（用于第 3 章）
MATERIAL_TOPICS: dict[str, list[str]] = {
    "manufacturing": ["温室气体排放", "能源管理", "废气与 VOCs", "危险废弃物", "安全生产", "供应链 ESG 管理", "员工培训", "反腐败"],
    "technology": ["数据中心能效", "电子废弃物", "数据安全与隐私", "供应链责任", "人才保留", "商业道德"],
    "retail": ["包装与塑料", "产品安全与质量", "供应链审核", "水资源", "客户满意度", "劳工权益"],
    "logistics": ["运输排放", "车队能效", "仓储安全", "包装回收", "承包商管理", "职业健康安全"],
    "real_estate": ["绿色建筑", "施工安全", "建筑能耗", "供应链管理", "社区关系", "土地使用与生态"],
    "banking": ["绿色信贷", "负责任投资", "气候风险", "客户数据保护", "合规与反洗钱", "普惠金融"],
    "services": ["能源与排放", "用工合规", "数据安全", "客户满意度", "商业道德", "员工发展"],
    "unknown": ["温室气体排放", "能源管理", "员工与安全", "公司治理", "供应链管理"],
}

# 报告中展示的环境 / 社会 / 治理指标顺序
E_TABLE = [
    ("E01", "综合能源消耗", "MWh"), ("E06", "清洁能源使用量", "MWh"),
    ("E07", "清洁能源占比", "%"), ("E08", "范围一排放", "tCO2e"),
    ("E09", "范围二排放（市场法）", "tCO2e"), ("E10", "范围二排放（地区法）", "tCO2e"),
    ("E15", "排放强度", "tCO2e/百万元营收"), ("E16", "总取水量", "m³"),
    ("E19", "一般废弃物", "吨"), ("E20", "危险废弃物", "吨"),
    ("E21", "废弃物回收利用率", "%"), ("E22", "环保投入", "万元"),
    ("E23", "环保行政处罚", "件"),
]
E_SCOPE3 = [("E11", "类别1 采购商品与服务"), ("E12", "类别4 上游运输配送"),
            ("E13", "类别6 商务差旅"), ("E14", "类别7 员工通勤")]

S_TABLE = [
    ("S01", "员工总数", "人"), ("S03", "女性员工占比", "%"),
    ("S04", "劳务派遣/外包", "人"), ("S07", "员工流失率", "%"),
    ("S09", "女性管理者占比", "%"), ("S12", "工伤死亡人数", "人"),
    ("S13", "损工事故起数", "起"), ("S14", "百万工时损工事故率", "LTIR"),
    ("S15", "人均培训时长", "小时"), ("S18", "供应商总数", "家"),
    ("S19_COV", "供应商 ESG 评估覆盖率", "%"),
]

G_TABLE = [
    ("G01", "董事会人数", "人"), ("G03", "独立董事占比", "%"),
    ("G04", "女性董事人数", "人"), ("G09", "反腐败培训覆盖率", "%"),
    ("G10", "腐败与贿赂事件", "件"), ("G13", "供应商行为准则覆盖率", "%"),
    ("G14", "ESG 制度文件", "份"),
]


def _fmt(v) -> str:
    if v is None or v == "":
        return "未获取"
    if isinstance(v, str):
        return v
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:,.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_本项无已填数据。_\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def _client_value(client: ClientData, derived: dict, code: str):
    return resolve_value(client, code, derived)


def _status_text(m: ClientMetric | None) -> str:
    if m is None:
        return "未在采集表中定义"
    if m.status == "不适用":
        return "不适用"
    if m.value is None:
        return "暂无数据"
    return m.status or "已填"


def _kpi_rows(client: ClientData, derived: dict, spec: list[tuple]) -> list[list[str]]:
    rows = []
    for item in spec:
        code, name, unit = item[0], item[1], item[2]
        m = client.metric(code)
        val = _client_value(client, derived, code)
        display_unit = m.unit if m and m.unit else unit
        if val is None:
            mark = _status_text(m)
        else:
            mark = "派生" if (m is None or m.value is None) else "已填"
        rows.append([name, display_unit, _fmt(val) if val is not None else "—", mark])
    return rows


def _gap_rows(gaps) -> list[list[str]]:
    rows = []
    for g in gaps:
        gap = "—" if g.gap_pct is None else f"{g.gap_pct:+.1f}%"
        rows.append([g.name, g.unit, _fmt(g.client_value), _fmt(g.median), _fmt(g.p75), gap, g.position])
    return rows


def _roadmap(gaps) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    """按优先级分三档：立刻可改 / 一年内 / 需要投入。"""
    quick, mid, long = [], [], []
    for g in gaps:
        if not g.suggestion:
            continue
        row = [g.name, g.position, g.suggestion]
        if g.code in ("G09", "S07", "S14") and g.position != "领先":
            quick.append(row)
        elif g.code in ("E07", "E21", "S09", "S19_COV") and g.position != "领先":
            mid.append(row)
        else:
            long.append(row)
    return quick, mid, long


# =========================================================
# LLM 叙述（可选）：只负责文字表达，不产生任何新数据
# =========================================================

NARRATIVE_SYSTEM_PROMPT = """你是 ESG 顾问，为一家企业撰写分析底稿中的叙述段落。

硬性规则：
1. 只能使用输入 JSON 中出现的数字与事实，禁止补充、推算或引用外部信息。
2. 禁止使用"行业领先""国际一流"等无法核验的形容词。
3. 数据缺失时如实说明，不得用估计值填充。
4. 输出必须是 JSON 对象，仅含两个字段：
   - "materiality"：120–180 字，说明为何这些实质性议题对该企业重要，结合其行业与业务。
   - "gap_summary"：150–220 字，点出 2–3 项最需改进的指标，说明差距方向与优先级理由。
5. 中文，专业、克制，不用营销语气。"""


def _narrative_context(analysis: ClientAnalysis) -> str:
    """构造给 LLM 的最小上下文：只给已核实的数字。"""
    p = analysis.client.profile
    gaps = [
        {
            "指标": g.name,
            "本企业": g.client_value,
            "行业中位": g.median,
            "行业领先P75": g.p75,
            "与中位差异%": g.gap_pct,
            "位置": g.position,
            "单位": g.unit,
        }
        for g in analysis.gaps
    ]
    payload = {
        "公司": p.company_name,
        "行业": p.industry or analysis.industry_key,
        "报告年度": p.report_year,
        "主要业务": p.main_business,
        "员工数": p.employees,
        "营收百万元": p.revenue_million,
        "组织边界": p.boundary,
        "必填完整度": analysis.completeness,
        "实质性议题": MATERIAL_TOPICS.get(analysis.industry_key, MATERIAL_TOPICS["unknown"]),
        "对标结果": gaps,
        "基线来源": next((b.source for b in analysis.bands if b.source), ""),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def generate_client_narrative(analysis: ClientAnalysis) -> dict[str, str]:
    """调用 LLM 生成两段叙述；失败则返回空 dict，由调用方回落为确定性文本。"""
    from .extractor import _call_llm_extraction, _strip_code_fence

    context = _narrative_context(analysis)
    try:
        raw = _call_llm_extraction(NARRATIVE_SYSTEM_PROMPT, context)
        data = json.loads(_strip_code_fence(raw))
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 叙述生成失败，使用确定性文本: %s", e)
        return {}

    out: dict[str, str] = {}
    for key in ("materiality", "gap_summary"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    if not out:
        logger.warning("LLM 返回内容缺少预期字段，使用确定性文本")
        return out

    # 数字一致性检查：叙述中出现的数字应能在上下文中找到，否则记录告警便于人工抽查
    known = set(re.findall(r"\d+(?:\.\d+)?", context))
    unknown = {
        n for text in out.values()
        for n in re.findall(r"\d+(?:\.\d+)?", text)
        if n not in known
    }
    if unknown:
        logger.warning("叙述中出现上下文未提供的数字（需人工核对）: %s", sorted(unknown)[:8])
    return out


def build_client_report(
    analysis: ClientAnalysis,
    use_llm_narrative: bool = False,
    target_rating: str | None = None,
) -> ESGReport:
    """生成定制化 Markdown 报告（不调用 LLM）。"""
    client = analysis.client
    p = client.profile
    derived = analysis.derived or derive_metrics(client)
    industry_key = analysis.industry_key
    topics = MATERIAL_TOPICS.get(industry_key, MATERIAL_TOPICS["unknown"])
    today = date.today()
    narr = generate_client_narrative(analysis) if use_llm_narrative else {}

    title = f"{p.company_name} ESG 报告 {p.report_year}"
    parts: list[str] = [f"# {title}\n"]
    parts.append(
        f"> 本报告基于企业填报数据（{client.source_file or '采集表'}）与行业公开披露基线生成，"
        f"为分析底稿，不构成审计意见或评级结论。生成日期：{today.isoformat()}。\n"
    )

    sections: list[ReportSection] = []

    # 1 报告说明
    basis_rows = [
        ["公司名称", p.company_name],
        ["所属行业", p.industry or "未填写"],
        ["报告年度", str(p.report_year)],
        ["基准年", str(p.baseline_year) if p.baseline_year else "未设定"],
        ["组织边界", p.boundary or "未填写"],
        ["纳入范围", p.boundary_entities or "未填写"],
        ["营业收入", f"{_fmt(p.revenue_million)} 百万元 {p.currency}" if p.revenue_million else "未填写"],
        ["员工总数", f"{p.employees:,} 人" if p.employees else "未填写"],
        ["报告用途", p.report_purpose or "未填写"],
        ["编制依据", p.reporting_basis or "未填写"],
        ["必填项完整度", f"{analysis.completeness * 100:.1f}%"],
    ]
    md = "## 一、报告说明\n\n" + _table(["项目", "内容"], basis_rows)
    md += (
        "\n数据来源：企业填报的 ESG 数据采集表；行业基线来自同行业公开披露数据的分位统计。"
        "报告中所有比率与强度指标由填报值计算得出，未做外推或估算。\n"
    )
    if analysis.data_quality_notes:
        md += "\n**数据质量提示**\n\n" + "\n".join(f"- {n}" for n in analysis.data_quality_notes) + "\n"
    if narr:
        md += ("\n第三、七章的叙述文字由大模型基于本报告数据生成，用于组织表达；"
               "所有数字仍以表格与填报值为准，可逐项核对。\n")
    parts.append(md)
    sections.append(ReportSection(title="报告说明", narrative="报告范围、边界与数据质量说明。"))

    # 2 公司概况与 ESG 治理
    gov_rows = []
    for code, label in [("G06", "ESG 归口部门"), ("G07", "ESG 负责人职务"),
                        ("G08", "是否向董事会汇报"), ("G15", "是否识别气候风险"),
                        ("G16", "是否设定减排目标")]:
        m = client.metric(code)
        gov_rows.append([label, _fmt(m.value) if m and m.value is not None else "未填写"])
    md = "## 二、公司概况与 ESG 治理\n\n"
    if p.main_business:
        md += f"{p.company_name} 主要从事{p.main_business}。\n\n"
    md += _table(["治理要素", "现状"], gov_rows)
    board_rows = _kpi_rows(client, derived, [("G01", "董事会人数", "人"), ("G02", "独立董事人数", "人"),
                                             ("G03", "独立董事占比", "%"), ("G04", "女性董事人数", "人"),
                                             ("G05", "董事会会议次数", "次")])
    md += "\n**董事会构成**\n\n" + _table(["指标", "单位", "数值", "状态"], board_rows)
    parts.append(md)
    sections.append(ReportSection(title="公司概况与 ESG 治理", narrative="治理架构与董事会构成。"))

    # 3 实质性议题
    md = "## 三、实质性议题\n\n"
    industry_label = p.industry or industry_key
    md += (f"结合{industry_label}行业特征与本次填报数据，确定以下 {len(topics)} 项重点议题：\n\n")
    md += "\n".join(f"{i}. {t}" for i, t in enumerate(topics, 1)) + "\n\n"
    md += (
        "> 议题清单依据行业常见披露重点生成。正式对外披露前，建议通过利益相关方访谈或问卷"
        "完成实质性评估（双重重要性），再据此调整议题与指标。\n"
    )
    if narr.get("materiality"):
        md += f"\n{narr['materiality']}\n"
    parts.append(md)
    sections.append(ReportSection(
        title="实质性议题",
        narrative=narr.get("materiality", "按行业特征确定的重点议题。"),
    ))

    # 4 环境绩效
    md = "## 四、环境绩效\n\n"
    md += _table(["指标", "单位", "本期", "状态"], _kpi_rows(client, derived, E_TABLE))
    scope3_rows = []
    for code, label in E_SCOPE3:
        m = client.metric(code)
        val = m.value if m else None
        scope3_rows.append([label, _fmt(val) if val is not None else _status_text(m), m.note if m else ""])
    md += "\n**范围三排放结构**\n\n" + _table(["类别", "排放量 (tCO2e)", "备注"], scope3_rows)
    ops = derived.get("OPS_TOTAL")
    if ops:
        md += f"\n范围一与范围二（市场法）合计运营排放 **{_fmt(ops)} tCO2e**。\n"
    intensity = _client_value(client, derived, "E15")
    if intensity is not None and p.revenue_million:
        md += (f"以 {_fmt(p.revenue_million)} 百万元营业收入计，排放强度为 "
               f"**{_fmt(intensity)} tCO2e/百万元营收**。\n")
    parts.append(md)
    sections.append(ReportSection(title="环境绩效", narrative="能耗、排放、水与废弃物。"))

    # 5 社会绩效
    md = "## 五、社会绩效\n\n"
    md += _table(["指标", "单位", "本期", "状态"], _kpi_rows(client, derived, S_TABLE))
    fatal = _client_value(client, derived, "S12")
    if fatal == 0:
        md += "\n报告期内未发生因工死亡事故。\n"
    parts.append(md)
    sections.append(ReportSection(title="社会绩效", narrative="员工、安全、培训与供应链。"))

    # 6 治理绩效
    md = "## 六、治理绩效\n\n"
    md += _table(["指标", "单位", "本期", "状态"], _kpi_rows(client, derived, G_TABLE))
    parts.append(md)
    sections.append(ReportSection(title="治理绩效", narrative="董事会、反腐败与制度。"))

    # 7 差距分析与路线图
    md = "## 七、差距分析与改进路线图\n\n"
    if analysis.gaps:
        md += "以下指标统一采用比率或强度口径，可与同行业中位水平直接比较。\n\n"
        md += _table(["指标", "单位", "本企业", "行业中位", "行业领先 (P75)", "与中位差异", "位置"],
                     _gap_rows(analysis.gaps))
        band_src = next((b.source for b in analysis.bands if b.source), "")
        md += f"\n> 基线来源：{band_src}\n"
        if narr.get("gap_summary"):
            md += f"\n{narr['gap_summary']}\n"
        quick, mid, long = _roadmap(analysis.gaps)
        if quick:
            md += "\n### 7.1 可立即推进\n\n" + _table(["议题", "现状", "建议"], quick)
        if mid:
            md += "\n### 7.2 一年内推进\n\n" + _table(["议题", "现状", "建议"], mid)
        if long:
            md += "\n### 7.3 需要投入或跨部门协同\n\n" + _table(["议题", "现状", "建议"], long)
    else:
        md += "_本次填报数据不足以完成对标，建议先补齐排放强度、清洁能源占比、流失率等核心指标。_\n"
    parts.append(md)
    sections.append(ReportSection(
        title="差距分析与改进路线图",
        narrative=narr.get("gap_summary", "对标结果与分档建议。"),
    ))

    # 7.5 目标评级路径（可选）
    path = build_rating_path(analysis, target_rating) if target_rating else None
    if path:
        md = f"## 八、评级提升路径（目标：{path.target_rating}）\n\n"
        md += f"> {path.baseline_note}{path.disclaimer}\n\n"
        md += f"**可达性判断**：{path.verdict}\n\n"

        if path.items:
            rows = [
                [i.name, i.unit, _fmt(i.current), _fmt(i.target_value), f"{i.gap_pct:+.1f}%",
                 i.severity, i.effort, f"{i.months} 个月"]
                for i in path.items
            ]
            md += _table(
                ["指标", "单位", "当前", f"{path.target_rating} 级参照", "差距", "严重度", "难度", "周期"],
                rows,
            )
            md += "\n**逐项动作**\n\n"
            for i in path.items:
                md += f"**{i.name}**（{i.severity} · {i.effort}难度 · 约 {i.months} 个月）\n\n"
                md += "\n".join(f"- {a}" for a in i.actions) + "\n\n"
        else:
            md += "_主要对标指标已达到目标评级区间，无需结构性改善。_\n\n"

        md += "### 分阶段推进\n\n"
        for idx, st in enumerate(path.stages, 1):
            md += f"**{idx}. {st.horizon}——{st.goal}**\n\n"
            md += "\n".join(f"- {it}" for it in st.items) + "\n\n"

        parts.append(md)
        sections.append(ReportSection(
            title=f"评级提升路径（目标：{path.target_rating}）",
            narrative=path.verdict,
        ))

    appendix_no = "九" if path else "八"

    # 8 数据附录
    md = f"## {appendix_no}、数据附录\n\n### {appendix_no}.1 缺失项清单\n\n"
    if analysis.missing_required:
        md += "\n".join(f"- {x}" for x in analysis.missing_required) + "\n"
    else:
        md += "无必填项缺失。\n"
    md += f"\n### {appendix_no}.2 指标明细\n\n"
    detail_rows = []
    for m in client.metrics:
        if m.status == "不适用":
            continue
        detail_rows.append([
            m.code, m.name, m.unit or "—",
            _fmt(m.value) if m.value is not None else "—",
            m.status or "—", m.department or "—", m.evidence or "—",
        ])
    md += _table(["编号", "指标", "单位", "本期", "状态", "责任部门", "证据"], detail_rows)
    md += (
        f"\n### {appendix_no}.3 编制说明\n\n"
        "- 比率与强度指标由填报的原始值计算得出，计算式见采集表口径说明列。\n"
        "- 「未获取」表示未填报或客户确认无法提供，本报告不做估算填充。\n"
        "- 组织边界、基准年一经确定，跨年度应保持一致；如调整需在备注中说明。\n"
    )
    parts.append(md)
    sections.append(ReportSection(title="数据附录", narrative="缺失清单与指标明细。"))

    markdown = "\n".join(parts)

    return ESGReport(
        title=title,
        company_name=p.company_name,
        stock_code="N/A",
        ticker="N/A",
        report_year=p.report_year,
        generated_at=today,
        data_sources=[
            "企业填报的 ESG 数据采集表",
            *sorted({b.source for b in analysis.bands if b.source}),
            *(["大模型生成的叙述文字（第3、7章）"] if narr else []),
        ],
        sections=sections,
        overall_data_quality=analysis.completeness,
        markdown=markdown,
        rating_path=path,
    )


def save_client_report(report: ESGReport, analysis: ClientAnalysis) -> Path:
    """落盘：Markdown + 报告 JSON（含评级路径）+ 分析 JSON。"""
    out_dir = settings.cache_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in report.company_name if ch not in '\\/:*?"<>|').replace(" ", "_")
    base = out_dir / f"client_{safe}_{report.report_year}"
    base.with_suffix(".md").write_text(report.markdown, encoding="utf-8")
    base.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    out_dir.joinpath(base.name + ".analysis.json").write_text(
        analysis.model_dump_json(indent=2), encoding="utf-8"
    )
    return base.with_suffix(".md")
