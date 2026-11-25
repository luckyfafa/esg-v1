"""V2 采集表读取：Excel → V2Dataset。

设计：
- 按表头名称定位列，不依赖固定列位，便于后续加列；
- 缺失与「不适用」严格区分；
- 规则编号会与规则库校验，不存在的编号只告警、不静默丢弃；
- 只读文件，不做网络请求，可离线运行。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook

from .rules import load_ruleset
from .schemas import (
    CarbonActivity,
    ClimateItem,
    DisclosureEntry,
    EmissionFactor,
    EvidenceItem,
    TargetItem,
    V2Dataset,
    V2Metric,
    V2Profile,
)

logger = logging.getLogger(__name__)

METRIC_SHEETS = {"E-环境": "E", "S-社会": "S", "G-治理": "G", "行业特有": "H"}
HEADER_ROW = 4
DATA_ROW = 5

PROFILE_LABELS = {
    "公司全称": "company_name",
    "所属行业": "industry",
    "报告年度": "report_year",
    "财政年度起始年": "fy_start_year",
    "发行人类型": "issuer_type",
    "是否恒生综合大型股": "is_largecap",
    "基准年": "baseline_year",
    "组织边界": "boundary",
    "纳入范围的实体": "boundary_entities",
    "营业收入": "revenue_million",
    "币种": "currency",
    "员工总数": "employees",
    "主要产品": "main_business",
    "报告用途": "report_purpose",
    "编制依据": "reporting_basis",
    "数据联系人": "contact",
}

ISSUER_MAP = {"主板": "main_board", "gem": "gem", "GEM": "gem", "非上市": "non_listed"}
TEXT_VALUES = {"有", "无", "是", "否"}


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v: Any) -> Optional[float | str]:
    if v is None:
        return None
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s == "-":
        return None
    if s in TEXT_VALUES:
        return s
    cleaned = s.replace(",", "").replace("，", "").rstrip("%").strip()
    try:
        return float(cleaned)
    except ValueError:
        return s


def _is_true(v: Any) -> bool:
    return str(v).strip().lower() in ("是", "true", "1", "yes", "y")


def _split_ids(v: Any) -> list[str]:
    s = _text(v)
    if not s:
        return []
    for sep in ("；", ";", "、", ",", "，", " "):
        s = s.replace(sep, "|")
    return [p.strip() for p in s.split("|") if p.strip()]


def _header_map(ws) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for c in range(1, ws.max_column + 1):
        name = _text(ws.cell(HEADER_ROW, c).value)
        if name:
            mapping[name] = c
    return mapping


def _cell(ws, headers: dict[str, int], row: int, name: str) -> Any:
    col = headers.get(name)
    return ws.cell(row, col).value if col else None


def _parse_profile(ws) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for r in range(4, 40):
        label = _text(ws.cell(r, 2).value)
        if not label:
            continue
        for key, field in PROFILE_LABELS.items():
            if label.startswith(key):
                found[field] = ws.cell(r, 3).value
                break
    return found


def _build_profile(raw: dict[str, Any]) -> V2Profile:
    year = _num(raw.get("report_year"))
    revenue_wan = _num(raw.get("revenue_million"))
    fy = _num(raw.get("fy_start_year"))
    issuer = _text(raw.get("issuer_type")) or "非上市"
    employees = _num(raw.get("employees"))
    baseline = _num(raw.get("baseline_year"))

    return V2Profile(
        company_name=_text(raw.get("company_name")) or "未命名企业",
        industry=_text(raw.get("industry")) or "",
        report_year=int(year) if isinstance(year, float) else 0,
        fy_start_year=int(fy) if isinstance(fy, float) else (int(year) if isinstance(year, float) else None),
        issuer_type=ISSUER_MAP.get(issuer, "non_listed"),  # type: ignore[arg-type]
        is_largecap=_is_true(raw.get("is_largecap")),
        baseline_year=int(baseline) if isinstance(baseline, float) else None,
        boundary=_text(raw.get("boundary")),
        boundary_entities=_text(raw.get("boundary_entities")),
        revenue_million=round(revenue_wan / 100, 4) if isinstance(revenue_wan, float) else None,
        currency=_text(raw.get("currency")) or "CNY",
        employees=int(employees) if isinstance(employees, float) else None,
        main_business=_text(raw.get("main_business")),
        report_purpose=_text(raw.get("report_purpose")),
        reporting_basis=_text(raw.get("reporting_basis")),
        contact=_text(raw.get("contact")),
    )


def _parse_metrics(wb, warnings: list[str]) -> list[V2Metric]:
    metrics: list[V2Metric] = []
    for sheet, category in METRIC_SHEETS.items():
        if sheet not in wb.sheetnames:
            warnings.append(f"缺少工作表「{sheet}」，该类别指标未读取")
            continue
        ws = wb[sheet]
        headers = _header_map(ws)
        if "编号" not in headers or "指标名称" not in headers:
            warnings.append(f"「{sheet}」表头不符合模板，已跳过")
            continue
        for r in range(DATA_ROW, ws.max_row + 1):
            code = _text(_cell(ws, headers, r, "编号"))
            if not code:
                break
            metrics.append(
                V2Metric(
                    code=code,
                    name=_text(_cell(ws, headers, r, "指标名称")) or code,
                    category=category,  # type: ignore[arg-type]
                    unit=_text(_cell(ws, headers, r, "单位")) or "",
                    level=_text(_cell(ws, headers, r, "优先级")) or "",
                    department=_text(_cell(ws, headers, r, "责任部门")) or "",
                    value=_num(_cell(ws, headers, r, "本期数值")),
                    prev_value=_num(_cell(ws, headers, r, "上期数值")),
                    status=_text(_cell(ws, headers, r, "填报状态")) or "",
                    source=_text(_cell(ws, headers, r, "数据来源")) or "",
                    evidence=_text(_cell(ws, headers, r, "证据文件")) or "",
                    note=_text(_cell(ws, headers, r, "备注")) or "",
                    rule_ids=_split_ids(_cell(ws, headers, r, "关联规则")),
                )
            )
    return metrics


def _parse_disclosures(wb, warnings: list[str]) -> list[DisclosureEntry]:
    if "披露清单" not in wb.sheetnames:
        warnings.append("缺少工作表「披露清单」，无法做披露检查")
        return []
    ws = wb["披露清单"]
    headers = _header_map(ws)
    known = {r.id for r in load_ruleset().rules}
    entries: list[DisclosureEntry] = []
    for r in range(DATA_ROW, ws.max_row + 1):
        rule_id = _text(_cell(ws, headers, r, "规则编号"))
        if not rule_id:
            continue
        if rule_id not in known:
            warnings.append(f"披露清单中的规则编号不在规则库中: {rule_id}")
        entries.append(
            DisclosureEntry(
                rule_id=rule_id,
                part=(_text(_cell(ws, headers, r, "部分")) or "C"),  # type: ignore[arg-type]
                paragraph=_text(_cell(ws, headers, r, "段落")) or "",
                title=_text(_cell(ws, headers, r, "披露项")) or "",
                status=(_text(_cell(ws, headers, r, "披露状态")) or ""),  # type: ignore[arg-type]
                report_location=_text(_cell(ws, headers, r, "报告位置")) or "",
                explanation=_text(_cell(ws, headers, r, "不适用理由／解释")) or "",
                owner=_text(_cell(ws, headers, r, "责任人")) or "",
                target_date=_text(_cell(ws, headers, r, "目标完成日期")) or "",
            )
        )
    return entries


def _parse_climate(wb) -> list[ClimateItem]:
    if "气候风险与机遇" not in wb.sheetnames:
        return []
    ws = wb["气候风险与机遇"]
    headers = _header_map(ws)
    items: list[ClimateItem] = []
    for r in range(DATA_ROW, ws.max_row + 1):
        name = _text(_cell(ws, headers, r, "名称"))
        if not name:
            continue
        items.append(
            ClimateItem(
                id=_text(_cell(ws, headers, r, "编号")) or f"C{r - DATA_ROW + 1:02d}",
                name=name,
                kind=(_text(_cell(ws, headers, r, "类型")) or ""),  # type: ignore[arg-type]
                horizon=_text(_cell(ws, headers, r, "时间范围")) or "",
                impact=_text(_cell(ws, headers, r, "对业务与财务的影响")) or "",
                response=_text(_cell(ws, headers, r, "应对措施")) or "",
                rule_ids=_split_ids(_cell(ws, headers, r, "关联规则")),
            )
        )
    return items


def _parse_targets(wb) -> list[TargetItem]:
    if "目标与行动" not in wb.sheetnames:
        return []
    ws = wb["目标与行动"]
    headers = _header_map(ws)
    items: list[TargetItem] = []
    for r in range(DATA_ROW, ws.max_row + 1):
        name = _text(_cell(ws, headers, r, "目标名称"))
        if not name:
            continue
        base_year = _num(_cell(ws, headers, r, "基准年"))
        base_val = _num(_cell(ws, headers, r, "基准值"))
        tgt_year = _num(_cell(ws, headers, r, "目标年"))
        tgt_val = _num(_cell(ws, headers, r, "目标值"))
        items.append(
            TargetItem(
                id=_text(_cell(ws, headers, r, "编号")) or f"T{r - DATA_ROW + 1:02d}",
                name=name,
                metric_code=_text(_cell(ws, headers, r, "关联指标")) or "",
                baseline_year=int(base_year) if isinstance(base_year, float) else None,
                baseline_value=base_val if isinstance(base_val, float) else None,
                target_year=int(tgt_year) if isinstance(tgt_year, float) else None,
                target_value=tgt_val if isinstance(tgt_val, float) else None,
                progress=_text(_cell(ws, headers, r, "本期进展")) or "",
                actions=_text(_cell(ws, headers, r, "行动项")) or "",
                owner=_text(_cell(ws, headers, r, "负责人")) or "",
                rule_ids=_split_ids(_cell(ws, headers, r, "关联规则")),
            )
        )
    return items


def _parse_activities(wb, warnings: list[str]) -> list[CarbonActivity]:
    if "碳活动数据" not in wb.sheetnames:
        warnings.append("缺少工作表「碳活动数据」，无法进行碳核算")
        return []
    ws = wb["碳活动数据"]
    headers = _header_map(ws)
    items: list[CarbonActivity] = []
    for r in range(DATA_ROW, ws.max_row + 1):
        site = _text(_cell(ws, headers, r, "场所／门店"))
        activity = _text(_cell(ws, headers, r, "能源／活动类型"))
        if not site and not activity:
            continue
        amount = _num(_cell(ws, headers, r, "活动数据"))
        items.append(
            CarbonActivity(
                id=_text(_cell(ws, headers, r, "编号")) or f"A{r - DATA_ROW + 1:02d}",
                site=site or "",
                period=_text(_cell(ws, headers, r, "期间")) or "",
                activity_type=activity or "",
                amount=amount if isinstance(amount, float) else None,
                unit=_text(_cell(ws, headers, r, "单位")) or "",
                evidence_id=_text(_cell(ws, headers, r, "凭证编号")) or "",
                note=_text(_cell(ws, headers, r, "备注")) or "",
            )
        )
    return items


def _parse_factors(wb, warnings: list[str]) -> list[EmissionFactor]:
    if "排放因子" not in wb.sheetnames:
        warnings.append("缺少工作表「排放因子」，碳核算将全部返回缺失")
        return []
    ws = wb["排放因子"]
    headers = _header_map(ws)
    items: list[EmissionFactor] = []
    for r in range(DATA_ROW, ws.max_row + 1):
        activity = _text(_cell(ws, headers, r, "能源／活动类型"))
        if not activity:
            continue
        factor = _num(_cell(ws, headers, r, "因子值"))
        year = _num(_cell(ws, headers, r, "年份"))
        source = _text(_cell(ws, headers, r, "来源")) or ""
        version = _text(_cell(ws, headers, r, "版本")) or ""
        if isinstance(factor, float) and (not source or not version):
            warnings.append(f"排放因子「{activity}」缺少来源或版本，核算时不予采用")
        items.append(
            EmissionFactor(
                activity_type=activity,
                region=_text(_cell(ws, headers, r, "地区")) or "",
                year=int(year) if isinstance(year, float) else None,
                factor=factor if isinstance(factor, float) else None,
                unit=_text(_cell(ws, headers, r, "单位")) or "",
                source=source,
                version=version,
                note=_text(_cell(ws, headers, r, "备注")) or "",
            )
        )
    return items


def _parse_evidence(wb) -> list[EvidenceItem]:
    if "证据索引" not in wb.sheetnames:
        return []
    ws = wb["证据索引"]
    headers = _header_map(ws)
    items: list[EvidenceItem] = []
    for r in range(DATA_ROW, ws.max_row + 1):
        code = _text(_cell(ws, headers, r, "编号"))
        name = _text(_cell(ws, headers, r, "指标名称"))
        file_name = _text(_cell(ws, headers, r, "文件名称"))
        if not code and not file_name:
            continue
        items.append(
            EvidenceItem(
                id=code or f"EV{r - DATA_ROW + 1:02d}",
                rule_ids=_split_ids(_cell(ws, headers, r, "对应规则")),
                metric_codes=[code] if code else [],
                evidence_type=_text(_cell(ws, headers, r, "证据类型")) or "",
                file_name=file_name or "",
                location=_text(_cell(ws, headers, r, "存放位置")) or "",
                provider=_text(_cell(ws, headers, r, "提供人")) or "",
                provided_date=_text(_cell(ws, headers, r, "提供日期")) or "",
                verified=_text(_cell(ws, headers, r, "核验状态")) or "",
            )
        )
    return items


def load_v2_excel(path: Path | str) -> V2Dataset:
    """读取 V2 采集表。

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 扩展名不符、缺「基础信息」页或未读取到任何指标
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"采集表不存在: {p}")
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError(f"仅支持 .xlsx/.xlsm 格式，收到: {p.suffix or '无扩展名'}")

    try:
        wb = load_workbook(p, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"无法读取采集表（文件可能损坏或非 Excel 文件）: {e}") from e

    if "基础信息" not in wb.sheetnames:
        raise ValueError("采集表缺少「基础信息」工作表，请使用 V2 模板")

    warnings: list[str] = []
    profile = _build_profile(_parse_profile(wb["基础信息"]))
    metrics = _parse_metrics(wb, warnings)
    if not metrics:
        raise ValueError("采集表中没有任何指标行，请确认使用 V2 模板且未删除指标页")

    if not profile.report_year:
        from datetime import date
        profile.report_year = date.today().year
        warnings.append("未填写报告年度，已按当前年份处理")
    if profile.issuer_type == "non_listed":
        warnings.append("发行人为非上市企业：港交所披露检查仅供内部管理参考，不构成合规结论")

    dataset = V2Dataset(
        profile=profile,
        metrics=metrics,
        disclosures=_parse_disclosures(wb, warnings),
        climate=_parse_climate(wb),
        targets=_parse_targets(wb),
        activities=_parse_activities(wb, warnings),
        factors=_parse_factors(wb, warnings),
        evidence=_parse_evidence(wb),
        source_file=str(p),
        warnings=warnings,
    )
    return dataset
