"""目标评级路径：从「当前水平」到「A 级同行的披露水平」的差距与改善路线。

方法说明（重要）：
    评级机构（MSCI、华证等）的评分模型不公开，无法由公开指标直接反推。
    本模块采用**实证法**：取同行业已获得目标评级的公司所披露的绩效水平作为目标带，
    即"要看起来像 A 级公司，这些指标需要到什么水平"。
    因此输出是**改进参考**，不构成评级承诺。

目标带定义：
    - A 级：行业中位以上的领先档，取 P75（越高越好）/ P25（越低越好）
    - AA 级：在 A 级基础上再收紧 20%（样本不足时的经验设定，需注明）
"""
from __future__ import annotations

from .client_data import resolve_value
from .schemas import ClientAnalysis, PathStage, RatingGap, RatingPath

# 各指标的改善动作库：具体、可执行、不含行业外假设
ACTIONS: dict[str, dict] = {
    "E15": {
        "effort": "高",
        "months": 18,
        "quick": "先做能耗拆分，找出排放占比最高的 20% 设备或门店",
        "actions": [
            "按门店/产线拆分电、气、油用量，建立月度台账（1 个月内）",
            "对占比最高的设备做节能改造，优先回收期 3 年以内的项目",
            "签订绿电交易或安装分布式光伏，降低范围二排放",
            "把范围三的采购与运输纳入核算，识别上游减排空间",
        ],
    },
    "E07": {
        "effort": "中",
        "months": 12,
        "quick": "先统计现有绿电与自发电量，把比例算出来",
        "actions": [
            "与电力公司签订绿电交易协议，锁定 3 年价格",
            "评估屋顶光伏可行性（面积、承重、并网条件）",
            "新增用电需求优先使用绿电，避免占比被业务增长稀释",
            "对外披露绿电合同与绿证编号，提升可核验性",
        ],
    },
    "E21": {
        "effort": "低",
        "months": 6,
        "quick": "先做一次废弃物称重，把可回收物从填埋里分出来",
        "actions": [
            "建立固废分类台账，与回收商签订协议并保留转移联单",
            "设定回收率月度目标，纳入门店或车间考核",
            "对包装、纸箱、金属边角料等可回收物单独统计",
            "推动供应商使用可回收包装，减少源头产生量",
        ],
    },
    "S07": {
        "effort": "中",
        "months": 12,
        "quick": "做一次离职原因分析，区分主动与被动",
        "actions": [
            "统计入职 1 年内流失占比，定位招聘与带教问题",
            "建立关键岗位留任面谈与晋升通道",
            "把排班、工时与加班情况纳入流失归因",
            "对高流失门店做管理复盘，形成可复制的改进清单",
        ],
    },
    "S09": {
        "effort": "高",
        "months": 24,
        "quick": "先统计管理者总人数，把当前比例算准",
        "actions": [
            "在招聘与晋升环节设定女性候选人比例要求",
            "建立女性管理者储备梯队与导师制",
            "设定 3 年内管理者性别结构目标并纳入高管考核",
            "披露性别薪酬差距与晋升比例，形成可核验的进展",
        ],
    },
    "S15": {
        "effort": "低",
        "months": 6,
        "quick": "先建培训学时台账，把现有培训记录下来",
        "actions": [
            "制定年度培训计划，明确必修（合规、安全）与选修（技能）",
            "按岗位序列设计课程，学时纳入管理者考核",
            "用线上课程降低一线员工的参训门槛",
            "量化培训对质量、损耗或客户投诉的影响",
        ],
    },
    "S14": {
        "effort": "中",
        "months": 12,
        "quick": "先排查高风险工序，把隐患整改闭环",
        "actions": [
            "完善事故上报流程，避免漏报导致数据失真",
            "落实班前安全交底与隐患上报奖励",
            "把承包商与临时用工的事故纳入统计",
            "建立领先指标（隐患上报率、演练完成率）",
        ],
    },
    "G09": {
        "effort": "低",
        "months": 3,
        "quick": "先给采购、销售岗做一轮反腐败培训并留签到",
        "actions": [
            "把反腐败培训纳入入职与年度必修，保留签到与考核记录",
            "覆盖采购、销售、招投标等高风险岗位",
            "要求供应商与代理商签署廉洁承诺书",
            "建立举报渠道并公布处理结果",
        ],
    },
    "S19_COV": {
        "effort": "中",
        "months": 12,
        "quick": "先按采购金额筛选出前 20 家关键供应商",
        "actions": [
            "建立供应商 ESG 风险分级，高风险优先发放问卷",
            "把 ESG 条款写入采购合同与准入流程",
            "对关键供应商开展现场审核或第三方审核",
            "把评估结果与订单分配挂钩，形成约束力",
        ],
    },
}

# 指标对评级的影响权重（经验值，用于区分"关键"与"一般"）
WEIGHTS = {
    "E15": 3, "E07": 2, "E21": 1,
    "S14": 2, "S07": 1, "S09": 1, "S15": 1, "S19_COV": 1,
    "G09": 1,
}

SCHEME_NOTE = {
    "msci": "MSCI ESG Ratings",
    "huazheng": "华证 ESG 评级",
    "csi": "中证指数 ESG 评价",
    "generic": "通用参照",
}


def _target_value(band, target: str) -> float | None:
    """目标带：A 取领先档阈值，AA 在此基础上收紧 20%。"""
    if band.median is None or band.p75 is None:
        return None
    if target == "AA":
        return round(band.p75 * 0.8, 2) if band.direction == "lower_better" else round(band.p75 * 1.2, 2)
    return band.p75


def _severity(code: str, shortfall_ratio: float, effort: str) -> str:
    """综合权重与差距幅度判定严重程度。"""
    w = WEIGHTS.get(code, 1)
    if w >= 3 and shortfall_ratio >= 0.3:
        return "关键"
    if shortfall_ratio >= 0.5 and effort != "低":
        return "关键"
    if shortfall_ratio >= 0.2 or w >= 2:
        return "重要"
    return "一般"


def build_rating_path(
    analysis: ClientAnalysis,
    target_rating: str = "A",
    scheme: str = "generic",
) -> RatingPath:
    """生成目标评级的差距与改善路径。"""
    target_rating = (target_rating or "A").upper()
    items: list[RatingGap] = []

    for band in analysis.bands:
        current = resolve_value(analysis.client, band.code, analysis.derived)
        target = _target_value(band, target_rating)
        if current is None or target is None:
            continue

        # 只在"劣于目标"时计入差距
        short = (current - target) if band.direction == "lower_better" else (target - current)
        if short <= 0:
            continue

        base = abs(target) or 1
        gap_pct = round(short / base * 100, 1)
        shortfall_ratio = short / base
        meta = ACTIONS.get(band.code, {})
        severity = _severity(band.code, shortfall_ratio, meta.get("effort", "中"))
        items.append(
            RatingGap(
                code=band.code,
                name=next((g.name for g in analysis.gaps if g.code == band.code), band.code),
                unit=band.unit,
                current=round(current, 2),
                target_value=round(target, 2),
                gap_abs=round(short, 2),
                gap_pct=gap_pct,
                severity=severity,
                effort=meta.get("effort", "中"),
                months=meta.get("months", 12),
                actions=meta.get("actions", []),
                quick_win=meta.get("quick", ""),
            )
        )

    order = {"关键": 0, "重要": 1, "一般": 2}
    items.sort(key=lambda x: (order[x.severity], -x.gap_pct))

    critical = sum(1 for i in items if i.severity == "关键")
    if not items:
        verdict = f"按当前披露水平，主要对标指标已达到 {target_rating} 级同行区间；建议把重点转向披露完整性与可核验性。"
    elif critical <= 2:
        verdict = f"存在 {critical} 项关键差距，按 12–18 个月分步推进可接近 {target_rating} 级同行水平。"
    elif critical <= 4:
        verdict = f"存在 {critical} 项关键差距，整体达到 {target_rating} 级需要 18–24 个月，建议先集中改 2 项。"
    else:
        verdict = f"存在 {critical} 项关键差距，短期内整体达到 {target_rating} 级不现实；建议按议题分阶段，先争取单项改善。"

    # 三阶段路径
    stage1 = [f"{i.name}：{i.quick_win}" for i in items if i.quick_win and i.months <= 6][:4]
    stage2 = [
        f"{i.name}（{i.effort}投入，约 {i.months} 个月）：{i.actions[0]}"
        for i in items if 6 < i.months <= 12 and i.actions
    ][:5]
    stage3 = [
        f"{i.name}（约 {i.months} 个月）：{i.actions[0]}"
        for i in items if i.months > 12 and i.actions
    ][:4]

    stages = [
        PathStage(horizon="1–3 个月", goal="把数据补齐、把最便宜的动作做掉", items=stage1 or ["补齐缺失的必填指标"]),
        PathStage(horizon="3–12 个月", goal="改造与制度落地，形成可核验记录", items=stage2 or ["对关键指标设定年度目标"]),
        PathStage(horizon="12 个月以上", goal="结构性改善，进入同行领先区间", items=stage3 or ["对高投入项做可行性研究"]),
    ]

    return RatingPath(
        target_rating=target_rating,
        scheme=SCHEME_NOTE.get(scheme, SCHEME_NOTE["generic"]),
        baseline_note=(
            f"目标带取同行业已获 {target_rating} 级评级公司的披露水平"
            f"（越高越好取 P75、越低越好取 P25{'' if target_rating == 'A' else '，AA 再收紧 20%'}）。"
        ),
        items=items,
        critical_count=critical,
        verdict=verdict,
        stages=stages,
        disclaimer=(
            "评级机构评分模型不公开，本路径基于同行已披露绩效的实证对照，"
            "不构成评级承诺；实际评级还取决于治理、争议事件、行业调整等因素。"
        ),
    )
