"""V2 数据模型：采集表 ↔ 结构化数据。

与 V1 的区别：
- 每个指标可关联港交所规则编号，支撑逐条披露检查；
- 披露清单、气候风险与机遇、目标与行动单独建模；
- 碳活动数据与排放因子分离，排放量由计算得出而非直接填报。
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

IssuerType = Literal["main_board", "gem", "non_listed"]
DisclosureStatus = Literal["已披露", "不适用", "需解释", "未完成"]
MetricStatus = Literal["已填", "暂无数据", "不适用"]


class V2Profile(BaseModel):
    """企业基础信息（V2 新增发行人类型与财政年度，用于适用性判定）。"""

    company_name: str
    industry: str = ""
    report_year: int
    fy_start_year: Optional[int] = None   # 财政年度起始年；未填时按报告年度
    issuer_type: IssuerType = "non_listed"
    is_largecap: bool = False             # 恒生综合大型股指数成分股
    baseline_year: Optional[int] = None
    boundary: Optional[str] = None
    boundary_entities: Optional[str] = None
    revenue_million: Optional[float] = None
    currency: str = "CNY"
    employees: Optional[int] = None
    main_business: Optional[str] = None
    report_purpose: Optional[str] = None
    reporting_basis: Optional[str] = None
    contact: Optional[str] = None


class V2Metric(BaseModel):
    """单项指标填报值，可关联多条披露规则。"""

    code: str
    name: str
    category: Literal["E", "S", "G", "H"] = "E"
    unit: str = ""
    level: str = ""
    department: str = ""
    value: Optional[float | str] = None
    prev_value: Optional[float | str] = None
    status: str = ""
    source: str = ""
    evidence: str = ""
    note: str = ""
    rule_ids: list[str] = Field(default_factory=list)


class DisclosureEntry(BaseModel):
    """披露清单中的一行，对应一条规则。"""

    rule_id: str
    part: Literal["B", "C", "D"]
    paragraph: str
    title: str
    status: DisclosureStatus | Literal[""] = ""
    report_location: str = ""
    explanation: str = ""      # 不适用理由或「不遵守就解释」说明
    owner: str = ""
    target_date: str = ""


class ClimateItem(BaseModel):
    """气候相关风险或机遇。"""

    id: str
    name: str
    kind: Literal["物理风险", "转型风险", "机遇", ""] = ""
    horizon: str = ""           # 短期 / 中期 / 长期
    impact: str = ""            # 对业务模式与财务的影响
    response: str = ""
    rule_ids: list[str] = Field(default_factory=list)


class TargetItem(BaseModel):
    """减排或 ESG 目标及进展。"""

    id: str
    name: str
    metric_code: str = ""
    baseline_year: Optional[int] = None
    baseline_value: Optional[float] = None
    target_year: Optional[int] = None
    target_value: Optional[float] = None
    progress: str = ""
    actions: str = ""
    owner: str = ""
    rule_ids: list[str] = Field(default_factory=list)


class CarbonActivity(BaseModel):
    """碳活动数据：只记录用量与凭证，排放量由因子计算。"""

    id: str
    site: str
    period: str                  # YYYY-MM 或 YYYY
    activity_type: str           # 外购电力 / 管道燃气 / 柴油 / 汽油 / 制冷剂 …
    amount: Optional[float] = None
    unit: str = ""
    evidence_id: str = ""
    note: str = ""


class EmissionFactor(BaseModel):
    """排放因子：必须注明来源与版本，系统不内置默认值。"""

    activity_type: str
    region: str = ""
    year: Optional[int] = None
    factor: Optional[float] = None
    unit: str = ""
    source: str = ""
    version: str = ""
    note: str = ""


class EvidenceItem(BaseModel):
    """凭证索引。"""

    id: str
    rule_ids: list[str] = Field(default_factory=list)
    metric_codes: list[str] = Field(default_factory=list)
    evidence_type: str = ""
    file_name: str = ""
    location: str = ""
    provider: str = ""
    provided_date: str = ""
    verified: str = ""


class V2Dataset(BaseModel):
    """一份完整采集表的结构化结果。"""

    profile: V2Profile
    metrics: list[V2Metric] = Field(default_factory=list)
    disclosures: list[DisclosureEntry] = Field(default_factory=list)
    climate: list[ClimateItem] = Field(default_factory=list)
    targets: list[TargetItem] = Field(default_factory=list)
    activities: list[CarbonActivity] = Field(default_factory=list)
    factors: list[EmissionFactor] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    source_file: Optional[str] = None
    loaded_at: date = Field(default_factory=date.today)
    warnings: list[str] = Field(default_factory=list)

    def metric(self, code: str) -> Optional[V2Metric]:
        return next((m for m in self.metrics if m.code == code), None)

    def disclosure(self, rule_id: str) -> Optional[DisclosureEntry]:
        return next((d for d in self.disclosures if d.rule_id == rule_id), None)

    def completeness_required(self) -> float:
        """必填指标完整度：状态为「不适用」的不计入分母。"""
        required = [m for m in self.metrics if m.level == "必填" and m.status != "不适用"]
        if not required:
            return 0.0
        filled = sum(1 for m in required if m.value is not None and m.value != "")
        return round(filled / len(required), 4)

    def missing_required(self) -> list[str]:
        return [
            f"{m.code} {m.name}"
            for m in self.metrics
            if m.level == "必填" and m.status != "不适用" and (m.value is None or m.value == "")
        ]

    def disclosure_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.disclosures:
            key = d.status or "未填写"
            counts[key] = counts.get(key, 0) + 1
        return counts
