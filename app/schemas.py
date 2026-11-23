"""Pydantic 数据模型：统一各源异构数据。"""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CompanyInfo(BaseModel):
    ticker: str  # Yahoo 格式，如 "9988.HK"
    name: str
    hkex_code: Optional[str] = None  # 港交所原始代码，如 "09988"
    industry: Optional[str] = None
    sector: Optional[str] = None


class ESGScore(BaseModel):
    """ESG 评分（多源：Yahoo/Sustainalytics / CDP / 中证 ESG 等）。"""
    source: Literal["yahoo", "cdp", "csi", "sustainalytics", "manual"] = "yahoo"
    environmental_score: Optional[float] = None
    social_score: Optional[float] = None
    governance_score: Optional[float] = None
    total_score: Optional[float] = None
    rating: Optional[str] = None  # 部分来源给字母评级（如 CDP A-D）
    year: int
    raw: dict = Field(default_factory=dict)


class EmissionRecord(BaseModel):
    """温室气体排放记录（NZDPU / CDP / ClimateWatch 来源，单位 tCO2e）。"""
    source: Literal["nzdpu", "cdp", "climatewatch", "self_reported"] = "nzdpu"
    scope1: Optional[float] = None
    scope2: Optional[float] = None
    scope2_location_based: Optional[float] = None
    scope3: Optional[float] = None
    intensity: Optional[float] = None  # tCO2e / 百万营收
    year: int
    raw: dict = Field(default_factory=dict)


class SBTiTarget(BaseModel):
    """科学碳目标倡议（SBTi）目标记录。

    SBTi 是 ESG 报告中判断公司是否有可信减排路径的关键依据。
    """
    company_name: str
    targets_committed: bool = False  # 是否已提交目标
    near_term_target_set: bool = False  # 是否设定近期（2030 前）目标
    net_zero_target_set: bool = False  # 是否设定净零目标
    target_year_near_term: Optional[int] = None  # 近期目标年份
    target_year_net_zero: Optional[int] = None  # 净零目标年份
    reduction_pct_near_term: Optional[float] = None  # 近期减排百分比
    scope: Optional[str] = None  # 覆盖范围：Scope1/2/3
    source: Literal["sbti"] = "sbti"
    raw: dict = Field(default_factory=dict)


class CDPScore(BaseModel):
    """CDP 披露评分（A/B/C/D 等级，含分项）。

    注：CDP 无免费公开查询 API，本模型保留以兼容历史数据结构，
    实际采集已切换至新浪财经 ESG 评级中心（MultiAgencyESG）。
    """
    company_name: str
    climate_score: Optional[str] = None  # Climate change score (A-D)
    water_score: Optional[str] = None
    forests_score: Optional[str] = None
    year: int
    source: Literal["cdp"] = "cdp"
    raw: dict = Field(default_factory=dict)


class AgencyESGRating(BaseModel):
    """单一机构的 ESG 评级（来自新浪财经 ESG 评级中心）。"""
    agency_name: str  # MSCI / 中证指数 / 标普 / 国证指数 / 商道融绿 等
    rating: Optional[str] = None  # 总评级，如 AAA / BBB / A / 60+
    esg_score: Optional[str] = None  # 原始评分（字符串，保留机构原始格式）
    environmental_score: Optional[float] = None  # 环境(E)分项
    social_score: Optional[float] = None  # 社会(S)分项
    governance_score: Optional[float] = None  # 治理(G)分项
    rating_date: Optional[str] = None  # 评级日期 YYYY-MM-DD
    raw: dict = Field(default_factory=dict)


class MultiAgencyESG(BaseModel):
    """多机构 ESG 评级聚合（新浪财经 ESG 评级中心）。

    免费、无需 token，支持港股(hkXXXXXX)与 A 股(sh/szXXXXXX)。
    聚合 MSCI、标普、中证指数、国证指数、中国国新、中债、商道融绿、
    华证指数、秩鼎、妙盈、盟浪、晨星等多家机构评级。
    """
    company_name: str
    stock_symbol: str  # 新浪格式，如 hk09988 / sh600519
    ratings: list[AgencyESGRating] = Field(default_factory=list)
    source: Literal["sina_esg"] = "sina_esg"
    raw: dict = Field(default_factory=dict)

    @property
    def msci_rating(self) -> Optional[str]:
        """便捷获取 MSCI 评级。"""
        for r in self.ratings:
            if r.agency_name == "MSCI" and r.rating and r.rating != "-":
                return r.rating
        return None

    @property
    def csi_rating(self) -> Optional[str]:
        """便捷获取中证指数 ESG 评级。"""
        for r in self.ratings:
            if r.agency_name == "中证指数" and r.rating and r.rating != "-":
                return r.rating
        return None


class CountryEmission(BaseModel):
    """国家级温室气体排放（Climate Watch / CAIT 数据）。

    作为公司所在国的宏观排放基准。
    """
    country: str
    year: int
    total_ghg: Optional[float] = None  # MtCO2e（World Bank CC.GHG.EMSE.IL）
    co2: Optional[float] = None
    methane: Optional[float] = None
    per_capita: Optional[float] = None  # tCO2e/人
    source: Literal["worldbank", "climatewatch"] = "worldbank"
    raw: dict = Field(default_factory=dict)


class HKEXReportMeta(BaseModel):
    """港交所 ESG 报告元数据。"""
    stock_code: str
    stock_name: str = ""
    report_title: str = ""
    release_date: date
    file_url: str
    file_path: Optional[str] = None  # 下载到本地后的路径


class HKGovIndicator(BaseModel):
    """香港政府开放数据 - 行业基准指标。"""
    dataset: str
    indicator: str
    year: int
    value: float
    unit: str
    raw: dict = Field(default_factory=dict)


class ESGAggregate(BaseModel):
    """多源合并视图：用于报告生成层。"""
    company: CompanyInfo
    yahoo_esg: Optional[ESGScore] = None
    nzdpu_emissions: Optional[EmissionRecord] = None
    hkex_reports: list[HKEXReportMeta] = Field(default_factory=list)
    hk_benchmark: Optional[HKGovIndicator] = None
    # 新增 ESG 数据源
    sbti_target: Optional[SBTiTarget] = None
    sina_esg: Optional[MultiAgencyESG] = None  # 新浪财经多机构ESG评级（替代CDP）
    country_emission: Optional[CountryEmission] = None


# =========================================================
# LLM 抽取层：从 HKEX ESG PDF 中提取的结构化 KPI
# 对齐 HKEX ESG Reporting Code + ISSB S2 气候披露
# =========================================================


class GHGEmissions(BaseModel):
    """温室气体排放（吨 CO2e）。

    单位说明：HKEX 报告常以"千吨"或"百万吨"披露，抽取时需统一换算到 tCO2e。
    """
    scope1: Optional[float] = None
    scope2_market_based: Optional[float] = None
    scope2_location_based: Optional[float] = None
    scope3_total: Optional[float] = None
    scope3_categories: dict[str, float] = Field(default_factory=dict)
    intensity_revenue: Optional[float] = None  # tCO2e / 百万营收
    # 部分 HKEX 报告只披露"自身运营总排放"（Scope 1 + Scope 2 合计）而无细分
    operations_total: Optional[float] = None
    # 减排目标进度（相对于基线年的减排百分比）
    reduction_vs_baseline_pct: Optional[float] = None
    baseline_year: Optional[int] = None
    unit: str = "tCO2e"
    raw_text: str = ""


class Energy(BaseModel):
    """能源消耗。"""
    total_consumption: Optional[float] = None  # MWh
    renewable_ratio: Optional[float] = None     # %
    intensity_revenue: Optional[float] = None
    raw_text: str = ""


class WaterAndWaste(BaseModel):
    water_withdrawal: Optional[float] = None  # 千立方米
    water_intensity: Optional[float] = None
    hazardous_waste: Optional[float] = None
    non_hazardous_waste: Optional[float] = None
    raw_text: str = ""


class Workforce(BaseModel):
    total_employees: Optional[int] = None
    female_ratio: Optional[float] = None  # %
    turnover_rate: Optional[float] = None
    training_hours_avg: Optional[float] = None
    fatal_incidents: Optional[int] = None
    lost_time_injury_rate: Optional[float] = None
    raw_text: str = ""


class Governance(BaseModel):
    board_size: Optional[int] = None
    independent_directors_ratio: Optional[float] = None
    female_directors_ratio: Optional[float] = None
    esg_committee_exists: Optional[bool] = None
    linked_to_remuneration: Optional[bool] = None
    raw_text: str = ""


class ESGKPIs(BaseModel):
    """从 ESG 报告 PDF 中抽取的全部 KPI。"""
    company_name: str
    stock_code: str
    report_year: int
    source_pdf: str
    ghg_emissions: GHGEmissions = Field(default_factory=GHGEmissions)
    energy: Energy = Field(default_factory=Energy)
    water_and_waste: WaterAndWaste = Field(default_factory=WaterAndWaste)
    workforce: Workforce = Field(default_factory=Workforce)
    governance: Governance = Field(default_factory=Governance)
    extraction_confidence: float = 0.0  # 0-1，LLM 自评信心
    notes: list[str] = Field(default_factory=list)


# =========================================================
# 报告生成层：把采集 + 抽取结果整合成 Markdown 报告
# =========================================================


class ReportSection(BaseModel):
    """报告章节。"""
    title: str
    narrative: str  # LLM 生成的叙述文字
    kpi_table: Optional[str] = None  # Markdown 表格，可选
    data_quality_notes: list[str] = Field(default_factory=list)


class ESGReport(BaseModel):
    """完整 ESG 报告。"""
    title: str
    company_name: str
    stock_code: str
    ticker: str
    report_year: int
    generated_at: date
    data_sources: list[str]  # 数据来源清单
    sections: list[ReportSection]
    overall_data_quality: float = 0.0  # 0-1，综合数据完整度
    markdown: str  # 完整 Markdown 文本
    rating_path: Optional["RatingPath"] = None  # 目标评级提升路径（可选）


# =========================================================
# 非上市企业层：客户填报数据 → 行业对标 → 定制报告
# =========================================================


class ClientProfile(BaseModel):
    """客户基础信息（来自采集表「基础信息」页）。"""
    company_name: str
    industry: str = ""
    report_year: int
    baseline_year: Optional[int] = None
    boundary: Optional[str] = None            # 运营控制权法 / 股权比例法
    boundary_entities: Optional[str] = None   # 纳入范围的实体
    revenue_million: Optional[float] = None   # 营业收入（百万元）
    currency: str = "CNY"
    employees: Optional[int] = None
    main_business: Optional[str] = None
    report_purpose: Optional[str] = None
    reporting_basis: Optional[str] = None
    contact: Optional[str] = None
    stakeholders: Optional[str] = None          # 主要利益相关方（投资者/员工/客户/供应商/社区/监管等）
    stakeholder_communication: Optional[str] = None  # 利益相关方沟通方式与频次
    esg_strategy: Optional[str] = None         # ESG 战略与愿景（可持续发展战略表述）


class ClientMetric(BaseModel):
    """客户填报的单项指标。"""
    code: str
    name: str
    category: Literal["E", "S", "G", "H"] = "E"
    unit: str = ""
    level: str = ""            # 必填 / 建议 / 加分 / 按行业选用
    department: str = ""
    value: Optional[float | str] = None
    prev_value: Optional[float | str] = None
    status: str = ""           # 已填 / 暂无数据 / 不适用
    source: str = ""
    evidence: str = ""
    note: str = ""


class ClientData(BaseModel):
    """客户填报的全部数据。"""
    profile: ClientProfile
    metrics: list[ClientMetric] = Field(default_factory=list)
    source_file: Optional[str] = None

    def metric(self, code: str) -> Optional[ClientMetric]:
        for m in self.metrics:
            if m.code == code:
                return m
        return None


class BenchmarkBand(BaseModel):
    """行业基线分位带。"""
    code: str
    unit: str
    p25: Optional[float] = None
    median: Optional[float] = None
    p75: Optional[float] = None
    direction: Literal["lower_better", "higher_better"] = "lower_better"
    peer_count: int = 0
    source: str = ""


class GapItem(BaseModel):
    """单项指标的差距分析结果。"""
    code: str
    name: str
    unit: str
    client_value: float
    median: Optional[float] = None
    p75: Optional[float] = None
    gap_pct: Optional[float] = None   # 相对行业中位的百分比差异
    position: Literal["领先", "接近", "落后"] = "接近"
    direction: Literal["lower_better", "higher_better"] = "lower_better"
    suggestion: str = ""


class ClientAnalysis(BaseModel):
    """客户数据 + 行业基线 → 差距分析结果。"""
    client: ClientData
    industry_key: str = ""
    bands: list[BenchmarkBand] = Field(default_factory=list)
    gaps: list[GapItem] = Field(default_factory=list)
    derived: dict[str, float] = Field(default_factory=dict)
    completeness: float = 0.0             # 必填项完整度 0-1
    missing_required: list[str] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)


class RatingGap(BaseModel):
    """距目标评级还差的单项指标。"""
    code: str
    name: str
    unit: str
    current: float
    target_value: float
    gap_abs: float
    gap_pct: float                       # 距目标带的百分比差距
    severity: Literal["关键", "重要", "一般"] = "重要"
    effort: Literal["低", "中", "高"] = "中"
    months: int = 12                     # 预计见效周期（月）
    actions: list[str] = Field(default_factory=list)
    quick_win: str = ""                  # 可立即做的一步


class PathStage(BaseModel):
    """分阶段路径。"""
    horizon: str                         # 1–3 个月 / 3–12 个月 / 12 个月以上
    goal: str
    items: list[str] = Field(default_factory=list)


class RatingPath(BaseModel):
    """目标评级的差距与改善路径。"""
    target_rating: str
    scheme: str = "通用"
    baseline_note: str = ""
    items: list[RatingGap] = Field(default_factory=list)
    critical_count: int = 0
    verdict: str = ""                    # 可达性判断
    stages: list[PathStage] = Field(default_factory=list)
    disclaimer: str = ""


ESGReport.model_rebuild()


