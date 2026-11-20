"""行业基线层：为客户指标提供分位带，并做差距分析。

两种基线来源：
1. 实测基线（推荐）：用同行业 5–10 家上市公司的 ESGKPIs 计算 P25/中位/P75，
   结果缓存到 data/cache/benchmarks/{industry}.json。
2. 参考基线：未做实测时的兜底区间，来源标注为「参考区间」，
   报告中必须注明其局限，不可当作统计结论。

指标口径统一为强度或比率，避免规模差异导致的不可比。
"""
from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Iterable, Optional

from .config import settings
from .client_data import resolve_value
from .schemas import BenchmarkBand, ClientAnalysis, ClientData, GapItem

logger = logging.getLogger(__name__)

# 用于对标的核心指标（全是比率或强度，可跨规模比较）
CORE_CODES = ["E15", "E07", "E21", "S07", "S09", "S15", "S14", "G09", "S19_COV"]

METRIC_NAMES = {
    "E15": ("温室气体排放强度", "tCO2e/百万元营收", "lower_better"),
    "E07": ("清洁能源占比", "%", "higher_better"),
    "E21": ("废弃物回收利用率", "%", "higher_better"),
    "S07": ("员工流失率", "%", "lower_better"),
    "S09": ("女性管理者占比", "%", "higher_better"),
    "S15": ("人均培训时长", "小时", "higher_better"),
    "S14": ("百万工时损工事故率", "LTIR", "lower_better"),
    "G09": ("反腐败培训覆盖率", "%", "higher_better"),
    "S19_COV": ("供应商 ESG 评估覆盖率", "%", "higher_better"),
}

# 参考基线：港股同行业常见区间，仅作兜底，报告需注明
REFERENCE_BANDS: dict[str, dict[str, tuple[float, float, float]]] = {
    "E15": {"p25": 20.5, "median": 12.6, "p75": 6.8},
    "E07": {"p25": 8.0, "median": 18.0, "p75": 42.0},
    "E21": {"p25": 68.0, "median": 82.0, "p75": 93.0},
    "S07": {"p25": 18.5, "median": 15.2, "p75": 9.5},
    "S09": {"p25": 16.0, "median": 24.0, "p75": 35.0},
    "S15": {"p25": 18.0, "median": 32.0, "p75": 48.0},
    "S14": {"p25": 1.20, "median": 0.85, "p75": 0.30},
    "G09": {"p25": 75.0, "median": 88.0, "p75": 100.0},
    "S19_COV": {"p25": 15.0, "median": 35.0, "p75": 68.0},
}

# 客户采集表行业名 → 标杆库行业 key
INDUSTRY_MAP = {
    "制造业": "manufacturing",
    "电子科技": "technology",
    "纺织消费品": "retail",
    "物流运输": "logistics",
    "建筑地产": "real_estate",
    "金融": "banking",
    "一般服务业": "services",
}


def normalize_industry(raw: str) -> str:
    """把客户表的行业名映射为内部 key；无法识别返回 original 小写。"""
    from .industry_peers import resolve_industry

    key = (raw or "").strip()
    if key in INDUSTRY_MAP:
        return INDUSTRY_MAP[key]
    std = resolve_industry(key)
    return std or key.lower() or "unknown"


def _band(source: str, peer_count: int = 0) -> list[BenchmarkBand]:
    bands: list[BenchmarkBand] = []
    for code in CORE_CODES:
        name, unit, direction = METRIC_NAMES[code]
        ref = REFERENCE_BANDS[code]
        bands.append(
            BenchmarkBand(
                code=code,
                unit=unit,
                p25=ref["p25"],
                median=ref["median"],
                p75=ref["p75"],
                direction=direction,
                peer_count=peer_count,
                source=source,
            )
        )
    return bands


def get_bands(industry_key: str) -> list[BenchmarkBand]:
    """读取行业基线：优先实测缓存，否则参考区间。"""
    cache_file = settings.cache_dir / "benchmarks" / f"{industry_key}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            bands = [BenchmarkBand.model_validate(b) for b in data.get("bands", [])]
            if bands:
                return bands
        except Exception as e:  # noqa: BLE001
            logger.warning("行业基线缓存损坏，改用参考区间: %s", e)
    return _band("参考区间（同行业公开披露的常见区间，非实时统计）")


def compute_bands_from_kpis(kpis_list: Iterable) -> dict[str, BenchmarkBand]:
    """用同行业上市公司已抽取的 KPI 计算分位带。

    仅当某指标有效样本 ≥ 3 时才输出，避免 1–2 个样本得出伪结论。
    """
    buckets: dict[str, list[float]] = {c: [] for c in CORE_CODES}
    for k in kpis_list:
        ghg, energy = k.ghg_emissions, k.energy
        wf = k.workforce
        pairs = {
            "E15": ghg.intensity_revenue,
            "E07": energy.renewable_ratio,
            "S07": wf.turnover_rate,
            "S15": wf.training_hours_avg,
            "S14": wf.lost_time_injury_rate,
            "G09": None,
        }
        for code, val in pairs.items():
            if isinstance(val, (int, float)) and val > 0:
                buckets[code].append(float(val))

    out: dict[str, BenchmarkBand] = {}
    for code, vals in buckets.items():
        if len(vals) < 3:
            continue
        vals.sort()
        name, unit, direction = METRIC_NAMES[code]
        out[code] = BenchmarkBand(
            code=code,
            unit=unit,
            p25=round(statistics.quantiles(vals, n=4)[0], 2),
            median=round(statistics.median(vals), 2),
            p75=round(statistics.quantiles(vals, n=4)[2], 2),
            direction=direction,
            peer_count=len(vals),
            source=f"实测基线（{len(vals)} 家同行业上市公司）",
        )
    return out


def save_bands(industry_key: str, bands: list[BenchmarkBand]) -> Path:
    out_dir = settings.cache_dir / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{industry_key}.json"
    payload = {
        "industry": industry_key,
        "bands": [b.model_dump() for b in bands],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# =========================================================
# 差距分析
# =========================================================

SUGGESTIONS: dict[str, dict[str, str]] = {
    "E15": {
        "落后": "先做能耗与排放的结构拆分：找出占比最高的工序与能源品种，制定 2–3 项节能改造，并考虑绿电采购以降低范围二。",
        "接近": "把范围三的采购与运输纳入核算，识别上游减排空间，同时锁定绿电比例目标。",
        "领先": "将强度优势转化为披露亮点，并向供应链提出减排要求，形成上下游协同。",
    },
    "E07": {
        "落后": "优先签订绿电交易或安装分布式光伏，三年内把清洁能源占比提升到 20% 以上。",
        "接近": "在新增用电需求上优先采用绿电，避免占比被业务增长稀释。",
        "领先": "探索绿证与自发自用结合，评估范围二接近零排放的可行性。",
    },
    "E21": {
        "落后": "建立固废分类台账，与回收商签订协议并保留转移联单，先把可回收物从填埋中分离。",
        "接近": "针对金属边角料、包装材料设置回收目标，并统计回收率月度数据。",
        "领先": "推动供应商包装回收与产品回收设计（可回收性评估）。",
    },
    "S07": {
        "落后": "做离职原因分析，区分主动与被动离职，重点看入职 1 年内流失与新员工带教机制。",
        "接近": "关注关键岗位与高绩效人群的保留，建立留任面谈机制。",
        "领先": "把稳定用工转化为雇主品牌素材，参与外部最佳雇主评选。",
    },
    "S09": {
        "落后": "在招聘与晋升环节设定女性人才池与面试比例要求，明确 3 年内管理者性别目标。",
        "接近": "补齐女性管理者储备梯队，建立导师制与晋升通道。",
        "领先": "公布性别薪酬差距与晋升比例，形成可核验的披露。",
    },
    "S15": {
        "落后": "建立年度培训计划与学时台账，先保证全员合规与安全培训覆盖，再补技能类课程。",
        "接近": "按岗位序列设计培训体系，把学时纳入管理者考核。",
        "领先": "引入外部认证与技能等级体系，量化培训对产能或质量的影响。",
    },
    "S14": {
        "落后": "立即排查高风险工序，落实隐患整改闭环与班前安全交底，完善事故上报流程。",
        "接近": "强化承包商与临时用工的安全管理，把其事故纳入统计。",
        "领先": "建立领先指标（隐患上报率、演练完成率），从结果管理转向过程管理。",
    },
    "G09": {
        "落后": "把反腐败培训纳入入职与年度必修，重点覆盖采购、销售、招投标岗位并留签到记录。",
        "接近": "扩展到供应商与代理商，签署廉洁承诺书。",
        "领先": "开展合规审计与举报渠道有效性评估，公开处理结果。",
    },
    "S19_COV": {
        "落后": "先建立供应商 ESG 风险分级，对高风险供应商发放问卷，覆盖率按年提升。",
        "接近": "把 ESG 条款写入采购合同，对关键供应商开展现场审核。",
        "领先": "建立供应商 ESG 绩效看板，与订单分配挂钩。",
    },
}


def _position(value: float, band: BenchmarkBand) -> str:
    median, p75 = band.median, band.p75
    if median is None or p75 is None:
        return "接近"
    if band.direction == "lower_better":
        if value <= p75:
            return "领先"
        return "接近" if value <= median * 1.1 else "落后"
    if value >= p75:
        return "领先"
    return "接近" if value >= median * 0.9 else "落后"


def analyze(client: ClientData, industry_key: str, derived: dict[str, float]) -> ClientAnalysis:
    """生成差距分析：客户值 vs 行业分位带。"""
    bands = get_bands(industry_key)
    gaps: list[GapItem] = []
    for band in bands:
        value = resolve_value(client, band.code, derived)
        if value is None or band.median in (None, 0):
            continue
        name, unit, direction = METRIC_NAMES[band.code]
        pos = _position(value, band)
        gap = round((value - band.median) / band.median * 100, 1) if direction else None
        gaps.append(
            GapItem(
                code=band.code,
                name=name,
                unit=unit,
                client_value=round(value, 2),
                median=band.median,
                p75=band.p75,
                gap_pct=gap,
                position=pos,
                direction=direction,
                suggestion=SUGGESTIONS.get(band.code, {}).get(pos, ""),
            )
        )

    from .client_data import completeness

    ratio, missing = completeness(client)
    notes: list[str] = []
    if ratio < 0.6:
        notes.append("必填项完整度低于 60%，环境与员工核心数据缺口较大，结论仅供参考。")
    elif ratio < 0.8:
        notes.append("必填项完整度介于 60%–80%，主要结论可用，建议补齐缺失项后再对外使用。")
    if any(b.source.startswith("参考区间") for b in bands):
        notes.append("本次对标使用参考区间而非实测基线，分位值仅用于定位量级，不作为统计结论。")
    if not client.profile.boundary:
        notes.append("未填写组织边界（运营控制权法/股权比例法），排放数据可能与其他公司不可比。")

    return ClientAnalysis(
        client=client,
        industry_key=industry_key,
        bands=bands,
        gaps=gaps,
        derived=derived,
        completeness=ratio,
        missing_required=missing,
        data_quality_notes=notes,
    )
