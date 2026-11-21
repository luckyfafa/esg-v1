"""按评级机构重点议题生成改进优先级。

依据只有三项公开信息：机构公开方法中的重点议题、企业在该机构的 E/S/G 分项、
本次抽取或采集中缺失的数据。因此输出是“议题优先级建议”，不是评级预测。
"""
from __future__ import annotations

from typing import Any

# 各机构公开方法中最看重的议题（按维度归组）
AGENCY_FOCUS: dict[str, dict[str, list[str]]] = {
    "MSCI": {
        "E": ["碳排放与能源管理", "水资源压力", "废弃物与污染物", "气候风险披露"],
        "S": ["职业健康与安全", "人力资本发展", "供应链劳工标准", "产品安全与质量"],
        "G": ["董事会结构与独立性", "商业道德与反贪污", "风险管理框架", "信息披露透明度"],
    },
    "标普": {
        "E": ["气候策略与情景分析", "排放与能源效率", "环境政策与管理体系", "生物多样性"],
        "S": ["员工健康与安全", "人才吸引与留任", "客户关系与隐私", "社区影响"],
        "G": ["董事会效能", "商业道德", "风险与危机管理", "税收透明度"],
    },
    "商道融绿": {
        "E": ["环境管理与认证", "排放与资源效率", "环境负面事件", "绿色产品与收入"],
        "S": ["员工权益与安全", "供应链责任", "产品责任", "社区投入"],
        "G": ["治理结构与独立性", "信息披露", "合规与反腐败", "风险管理"],
    },
    "华证": {
        "E": ["碳排放强度", "能源结构", "污染物排放", "环境处罚记录"],
        "S": ["员工规模与结构", "安全生产", "研发与培训投入", "社会责任披露"],
        "G": ["股权与董事会结构", "关联交易", "违规与诉讼", "信息披露质量"],
    },
    "中诚信": {
        "E": ["环境管理与排放物", "资源使用效率", "可持续发展战略", "环保处罚"],
        "S": ["员工权益", "供应链管理", "产品质量", "社区与公益"],
        "G": ["董事会治理", "商业道德", "风险管理", "信息披露"],
    },
}

PHASE_BY_PRIORITY = {"高": "30 天内", "中": "6 个月内", "低": "12 个月内"}
PILLAR_ZH = {"E": "环境", "S": "社会", "G": "治理"}


def build(ratings: list[dict], components: dict[str, float] | None, missing: list[str] | None = None) -> dict[str, Any]:
    """ratings: [{agency, rating, date}]；components: {E,S,G} 分项（同一机构口径）。"""
    missing = missing or []
    rated = [r for r in ratings if r.get("rating")]
    if not rated:
        return {"available": False, "reason": "本次未取得任何机构评级，无法生成议题优先级。"}

    agency = next((r["agency"] for r in rated if r["agency"] in AGENCY_FOCUS), None)
    if not agency:
        return {"available": False, "reason": "已取得评级，但该机构尚未纳入议题库。"}

    focus = AGENCY_FOCUS[agency]
    comps = components or {}
    weakest = None
    if all(k in comps for k in ("E", "S", "G")):
        weakest = min(comps, key=lambda k: comps[k])

    actions: list[dict[str, Any]] = []
    for pillar in ("E", "S", "G"):
        issues = focus.get(pillar, [])
        if not issues:
            continue
        for i, topic in enumerate(issues[:3]):
            priority = "中"
            basis = f"{agency} 公开方法将该议题列为核心议题"
            if weakest == pillar and i == 0:
                priority = "高"
                basis += f"；且本次 {PILLAR_ZH[pillar]} 分项在三者中最低（{comps.get(pillar)}）"
            if i == 1 and weakest == pillar:
                priority = "高"
            actions.append({
                "pillar": PILLAR_ZH[pillar],
                "topic": topic,
                "priority": priority,
                "phase": PHASE_BY_PRIORITY[priority],
                "basis": basis,
                "action": _action_for(topic),
            })

    if missing:
        actions.append({
            "pillar": "数据",
            "topic": "补齐未取得的关键数据",
            "priority": "高",
            "phase": "30 天内",
            "basis": "本次未取得：" + "、".join(missing[:6]),
            "action": "先补齐数据再对外披露，避免问卷或审阅时无法说明。",
        })

    order = {"高": 0, "中": 1, "低": 2}
    actions.sort(key=lambda a: (order[a["priority"]], a["pillar"]))
    return {
        "available": True,
        "agency": agency,
        "rating": next(r["rating"] for r in rated if r["agency"] == agency),
        "weakest_pillar": PILLAR_ZH.get(weakest, "") if weakest else "",
        "actions": actions,
        "note": (
            f"优先级依据：{agency} 公开方法列示的重点议题"
            + (f"、本次 {PILLAR_ZH[weakest]} 分项最低" if weakest else "")
            + "。本表不预测评级变化，也不保证改进某项后评级一定提升。"
        ),
    }


def _action_for(topic: str) -> str:
    if "碳" in topic or "排放" in topic:
        return "先按范围一／二建立年度台账，明确排放因子来源与核算边界。"
    if "能源" in topic:
        return "拆分能源结构与用量，记录节能量与可再生能源占比。"
    if "水" in topic:
        return "建立取水与排水台账，注明水源类型与循环用水比例。"
    if "废弃" in topic or "污染" in topic:
        return "按有害／无害分类统计产生量与处置方式，保留处置凭证。"
    if "安全" in topic or "工伤" in topic:
        return "统计工伤事故、损工日数与死亡人数，补齐安全培训记录。"
    if "培训" in topic or "人力" in topic or "人才" in topic:
        return "按性别与职级统计培训时数与受训比例。"
    if "供应链" in topic:
        return "梳理一级供应商名单，加入环境与社会条款并留存评估记录。"
    if "董事" in topic or "治理" in topic or "股权" in topic:
        return "说明董事会构成、独立性、专门委员会与开会次数。"
    if "贪污" in topic or "道德" in topic or "合规" in topic:
        return "建立反贪污制度与举报渠道，统计相关培训与诉讼案件。"
    if "披露" in topic or "透明度" in topic:
        return "按港交所守则逐条对照，补齐缺失的定量指标与政策说明。"
    if "气候" in topic:
        return "识别实体与转型风险，说明情景假设及对财务的影响。"
    if "产品" in topic or "客户" in topic or "隐私" in topic:
        return "统计质量投诉、召回与数据安全事件，说明处理机制。"
    return "明确责任部门、数据口径与年度目标，形成可核查的记录。"
