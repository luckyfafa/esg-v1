"""客户填报数据层：解析《非上市企业 ESG 数据采集表》Excel，并做派生指标计算。

设计要点：
1. 只读单元格值，不做任何网络请求，可在内网离线运行。
2. 表格结构固定（见 docs/非上市企业ESG数据采集表.xlsx），按列位读取，不依赖中文表头文本。
3. 缺失与「不适用」严格区分：缺失进缺失清单，「不适用」不计入完整度。
4. 派生指标只在客户未直接填报时计算，避免覆盖客户口径。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook

from .schemas import ClientData, ClientMetric, ClientProfile

logger = logging.getLogger(__name__)

# 工作表 → 指标类别
SHEETS: dict[str, str] = {
    "E-环境": "E",
    "S-社会": "S",
    "G-治理": "G",
    "行业特有": "H",
}

START_ROW = 5
END_ROW = 60

# 「基础信息」页：行标签 → Profile 字段
PROFILE_LABELS: dict[str, str] = {
    "公司全称": "company_name",
    "所属行业": "industry",
    "报告年度": "report_year",
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

# 文本型指标（不参与数值计算）
TEXT_VALUES = {"有", "无", "是", "否"}


def _to_num(v: Any) -> Optional[float | str]:
    """单元格值 → float 或文本；空值返回 None。"""
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


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _parse_profile(ws) -> dict[str, Any]:
    """扫描「基础信息」页，按行标签匹配字段。"""
    found: dict[str, Any] = {}
    for row in range(3, 40):
        label = _text(ws.cell(row, 2).value)
        if not label:
            continue
        for key, field in PROFILE_LABELS.items():
            if label.startswith(key):
                found[field] = ws.cell(row, 3).value
                break
    return found


def _build_profile(raw: dict[str, Any], fallback_year: int) -> ClientProfile:
    def num(v) -> Optional[float]:
        if v is None or str(v).strip() == "":
            return None
        try:
            return float(str(v).replace(",", "").strip())
        except ValueError:
            return None

    year = num(raw.get("report_year"))
    revenue_cny = num(raw.get("revenue_million"))
    return ClientProfile(
        company_name=_text(raw.get("company_name")) or "未命名企业",
        industry=_text(raw.get("industry")) or "",
        report_year=int(year) if year else fallback_year,
        baseline_year=int(num(raw["baseline_year"])) if num(raw.get("baseline_year")) else None,
        boundary=_text(raw.get("boundary")),
        boundary_entities=_text(raw.get("boundary_entities")),
        # 表内单位为万元，统一换算为百万元
        revenue_million=round(revenue_cny / 100, 4) if revenue_cny else None,
        currency=_text(raw.get("currency")) or "CNY",
        employees=int(num(raw["employees"])) if num(raw.get("employees")) else None,
        main_business=_text(raw.get("main_business")),
        report_purpose=_text(raw.get("report_purpose")),
        reporting_basis=_text(raw.get("reporting_basis")),
        contact=_text(raw.get("contact")),
    )


def _parse_metrics(wb) -> list[ClientMetric]:
    metrics: list[ClientMetric] = []
    for sheet_name, category in SHEETS.items():
        if sheet_name not in wb.sheetnames:
            logger.warning("采集表缺少工作表: %s", sheet_name)
            continue
        ws = wb[sheet_name]
        for row in range(START_ROW, END_ROW + 1):
            code = _text(ws.cell(row, 1).value)
            if not code:
                break
            metrics.append(
                ClientMetric(
                    code=code,
                    name=_text(ws.cell(row, 2).value) or code,
                    category=category,
                    unit=_text(ws.cell(row, 3).value) or "",
                    level=_text(ws.cell(row, 5).value) or "",
                    department=_text(ws.cell(row, 6).value) or "",
                    value=_to_num(ws.cell(row, 7).value),
                    prev_value=_to_num(ws.cell(row, 8).value),
                    source=_text(ws.cell(row, 10).value) or "",
                    evidence=_text(ws.cell(row, 11).value) or "",
                    status=_text(ws.cell(row, 12).value) or "",
                    note=_text(ws.cell(row, 13).value) or "",
                )
            )
    return metrics


def load_client_excel(path: Path | str) -> ClientData:
    """读取客户填报的采集表。

    Args:
        path: xlsx 路径（须为《非上市企业 ESG 数据采集表》结构）

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 结构不匹配（缺「基础信息」页或指标页全空）
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"采集表不存在: {p}")
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError(f"仅支持 .xlsx/.xlsm 格式的采集表，收到: {p.suffix or '无扩展名'}")

    try:
        wb = load_workbook(p, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"无法读取采集表（文件可能损坏或非 Excel 文件）: {e}") from e
    if "基础信息" not in wb.sheetnames:
        raise ValueError("采集表缺少「基础信息」工作表，请使用项目提供的模板")

    profile = _build_profile(_parse_profile(wb["基础信息"]), fallback_year=0)
    if not profile.report_year:
        import datetime as _dt
        profile.report_year = _dt.date.today().year

    metrics = _parse_metrics(wb)
    if not any(m.value is not None for m in metrics):
        raise ValueError("采集表中没有任何已填指标，请确认数值填在「本期数值」列")

    return ClientData(profile=profile, metrics=metrics, source_file=str(p))


# =========================================================
# 派生指标
# =========================================================

def _val(client: ClientData, code: str) -> Optional[float]:
    """取数值型指标（文本值返回 None）。"""
    m = client.metric(code)
    if not m or not isinstance(m.value, (int, float)):
        return None
    return float(m.value)


def derive_metrics(client: ClientData) -> dict[str, float]:
    """在客户未直接填报时计算派生指标。

    仅使用客户已填数据，不引入任何外部假设；无法计算则跳过。
    """
    d: dict[str, float] = {}
    v = lambda c: _val(client, c)

    energy = v("E01")
    clean = v("E06")
    if energy and clean and clean <= energy:
        d["E07"] = round(clean / energy * 100, 2)

    s1, s2 = v("E08"), v("E09")
    revenue = client.profile.revenue_million
    if s1 is not None and s2 is not None:
        d["OPS_TOTAL"] = round(s1 + s2, 2)
        if revenue:
            d["E15"] = round((s1 + s2) / revenue, 2)

    total, female = v("S01"), v("S02")
    if total and female is not None and female <= total:
        d["S03"] = round(female / total * 100, 2)

    turnover, left, prev_total = v("S07"), v("S06"), None
    m_s01 = client.metric("S01")
    if m_s01 and isinstance(m_s01.prev_value, (int, float)):
        prev_total = float(m_s01.prev_value)
    if turnover is None and left is not None and total and prev_total:
        avg = (total + prev_total) / 2
        if avg:
            d["S07"] = round(left / avg * 100, 2)

    board, indep = v("G01"), v("G02")
    if board and indep is not None and indep <= board:
        d["G03"] = round(indep / board * 100, 2)

    suppliers, assessed = v("S18"), v("S19")
    if suppliers and assessed is not None and assessed <= suppliers:
        d["S19_COV"] = round(assessed / suppliers * 100, 2)

    return d


def resolve_value(client: ClientData, code: str, derived: dict[str, float]) -> Optional[float]:
    """取值优先级：客户填报 > 派生计算。"""
    v = _val(client, code)
    if v is not None:
        return v
    return derived.get(code)


def completeness(client: ClientData) -> tuple[float, list[str]]:
    """必填项完整度。

    分母只统计「必填」且未标「不适用」的指标；「暂无数据」计入分母但不算已填。
    """
    required = [m for m in client.metrics if m.level == "必填" and m.status != "不适用"]
    if not required:
        return 0.0, []
    filled = [m for m in required if m.status == "已填" and m.value is not None]
    missing = [f"{m.code} {m.name}" for m in required if m not in filled]
    return round(len(filled) / len(required), 4), missing
