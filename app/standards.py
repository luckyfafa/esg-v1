"""港交所《ESG 报告守则》与 IFRS S2 的披露覆盖检查。

系统只判断“本次采集/抽取是否拿到对应数据”，不判断披露质量，
也不代替合规审阅。定性条款统一标为“需企业补充”，避免误报为缺失。
"""
from __future__ import annotations

from typing import Iterable

# field: 系统内部可用字段；qual: 定性条款，需企业文字说明；target: 需设目标
REQUIREMENTS: list[dict] = [
    {"framework": "HKEX", "code": "A1.1", "name": "排放物种类及相关数据", "fields": ["waste"], "kind": "quant"},
    {"framework": "HKEX", "code": "A1.2", "name": "温室气体排放（范围一／二）及强度", "fields": ["scope1", "scope2"], "kind": "quant"},
    {"framework": "HKEX", "code": "A1.3", "name": "排放相关政策", "fields": [], "kind": "qual"},
    {"framework": "HKEX", "code": "A1.4", "name": "排放目标与进展", "fields": [], "kind": "target"},
    {"framework": "HKEX", "code": "A2.1", "name": "能源消耗及强度", "fields": ["energy"], "kind": "quant"},
    {"framework": "HKEX", "code": "A2.2", "name": "用水及水强度", "fields": ["water"], "kind": "quant"},
    {"framework": "HKEX", "code": "A2.3", "name": "资源使用效率政策", "fields": [], "kind": "qual"},
    {"framework": "HKEX", "code": "A2.4", "name": "资源使用目标与进展", "fields": [], "kind": "target"},
    {"framework": "HKEX", "code": "A3.1", "name": "对环境和天然资源的影响", "fields": [], "kind": "qual"},
    {"framework": "HKEX", "code": "A4.1", "name": "重大气候相关议题及应对", "fields": [], "kind": "qual"},
    {"framework": "HKEX", "code": "B1.1", "name": "员工总数及性别、年龄、地区构成", "fields": ["employees", "female_ratio"], "kind": "quant"},
    {"framework": "HKEX", "code": "B1.2", "name": "员工流失率", "fields": ["turnover"], "kind": "quant"},
    {"framework": "HKEX", "code": "B2.1", "name": "工伤及死亡数字与比率", "fields": ["fatalities", "injury_rate"], "kind": "quant"},
    {"framework": "HKEX", "code": "B2.2", "name": "因工伤损失工作日数", "fields": ["lost_days"], "kind": "quant"},
    {"framework": "HKEX", "code": "B3.1", "name": "受训员工比例", "fields": ["training_ratio"], "kind": "quant"},
    {"framework": "HKEX", "code": "B3.2", "name": "人均培训时数", "fields": ["training_hours"], "kind": "quant"},
    {"framework": "HKEX", "code": "B5.2", "name": "供应链环境与社会风险管理", "fields": [], "kind": "qual"},
    {"framework": "HKEX", "code": "B7.1", "name": "贪污诉讼案件", "fields": [], "kind": "qual"},
    {"framework": "HKEX", "code": "B8.2", "name": "社区投资资源", "fields": [], "kind": "qual"},
    {"framework": "IFRS S2", "code": "治理", "name": "气候相关治理与监督", "fields": [], "kind": "qual"},
    {"framework": "IFRS S2", "code": "策略", "name": "气候风险与机遇及财务影响", "fields": [], "kind": "qual"},
    {"framework": "IFRS S2", "code": "风险管理", "name": "气候风险识别与评估流程", "fields": [], "kind": "qual"},
    {"framework": "IFRS S2", "code": "指标-排放", "name": "范围一、二、三温室气体排放", "fields": ["scope1", "scope2", "scope3"], "kind": "quant"},
    {"framework": "IFRS S2", "code": "指标-目标", "name": "气候目标与进展", "fields": [], "kind": "target"},
]

FIELD_LABELS = {
    "scope1": "范围一排放",
    "scope2": "范围二排放",
    "scope3": "范围三排放",
    "energy": "能源消耗",
    "water": "用水",
    "waste": "废弃物",
    "employees": "员工总数",
    "female_ratio": "女性比例",
    "turnover": "流失率",
    "fatalities": "工伤死亡",
    "injury_rate": "损工事故率",
    "lost_days": "损失工作日",
    "training_ratio": "受训比例",
    "training_hours": "人均培训时数",
}


def check(available: Iterable[str]) -> dict:
    """available 为本次已有数据的字段名集合。"""
    have = set(available)
    rows = []
    for req in REQUIREMENTS:
        fields = req["fields"]
        if req["kind"] != "quant":
            status = "需企业补充"
        elif not fields:
            status = "需企业补充"
        else:
            got = [f for f in fields if f in have]
            if len(got) == len(fields):
                status = "已覆盖"
            elif got:
                status = "部分覆盖"
            else:
                status = "缺失"
        rows.append({
            "framework": req["framework"],
            "code": req["code"],
            "name": req["name"],
            "status": status,
            "evidence": "、".join(FIELD_LABELS.get(f, f) for f in fields if f in have),
        })
    counts = {"已覆盖": 0, "部分覆盖": 0, "缺失": 0, "需企业补充": 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"rows": rows, "counts": counts, "total": len(rows)}


def to_markdown(result: dict) -> str:
    """生成报告章节用的 Markdown 表格。"""
    c = result["counts"]
    md = (
        f"本次共核对 {result['total']} 项披露要求："
        f"已覆盖 {c.get('已覆盖', 0)} 项、部分覆盖 {c.get('部分覆盖', 0)} 项、"
        f"缺失 {c.get('缺失', 0)} 项、需企业补充 {c.get('需企业补充', 0)} 项。\n\n"
    )
    md += "| 框架 | 条款 | 要求 | 本次状态 | 已有数据 |\n|---|---|---|---|---|\n"
    for r in result["rows"]:
        md += (
            f"| {r['framework']} | {r['code']} | {r['name']} | {r['status']} "
            f"| {r['evidence'] or '—'} |\n"
        )
    md += (
        "\n说明：本表只核对本次采集或抽取是否取得对应数据，不评价披露质量，"
        "也不构成合规意见。定性条款与环境目标需企业补充文字说明后方可视为完成。\n"
    )
    return md
