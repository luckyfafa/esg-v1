"""双重重要性（double materiality）评估：议题池、双轴评分与矩阵输出。

影响重要性 = 规模、范围、不可补救性的均值（1–5）。
财务重要性 = 发生可能性与财务影响量级的均值（1–5）。
阈值 3.0：两条轴均达标为“双重重大”，单轴达标为单维重大，否则列入观察。

评分由使用者给出，系统只负责计算、排序与留痕，不替企业判断重要性。
"""
from __future__ import annotations

from datetime import date
from typing import Any

THRESHOLD = 3.0

# 通用议题池：(id, 名称, 维度, 映射的采集指标, 默认影响分, 默认财务分)
COMMON_TOPICS: list[tuple] = [
    ("E_GHG", "温室气体排放", "E", ["E08", "E09", "E10"], 4, 4),
    ("E_ENERGY", "能源使用与效率", "E", ["E01", "E06", "E07"], 4, 4),
    ("E_WATER", "水资源管理", "E", ["E16"], 3, 2),
    ("E_WASTE", "废弃物管理", "E", ["E19", "E20", "E21"], 3, 2),
    ("E_COMPLIANCE", "污染物排放与环保合规", "E", ["E23"], 3, 4),
    ("E_CLIMATE", "气候风险与机遇", "E", ["G15", "G16"], 4, 4),
    ("E_BIODIVERSITY", "生物多样性与土地利用", "E", [], 2, 2),
    ("E_GREEN", "绿色产品与服务", "E", [], 2, 3),
    ("S_SAFETY", "职业健康与安全", "S", ["S12", "S13", "S14"], 4, 3),
    ("S_LABOUR", "员工权益与福利", "S", ["S01"], 4, 3),
    ("S_DEI", "多元与共融", "S", ["S03", "S09"], 3, 2),
    ("S_TRAINING", "培训与发展", "S", ["S15"], 3, 2),
    ("S_RETENTION", "人才保留与流失", "S", ["S07"], 3, 3),
    ("S_SUPPLY", "供应链环境与社会责任", "S", ["S18", "S19_COV"], 3, 3),
    ("S_PRODUCT", "产品质量与安全", "S", [], 3, 4),
    ("S_PRIVACY", "客户隐私与数据安全", "S", [], 3, 4),
    ("S_COMMUNITY", "社区投资与参与", "S", [], 2, 2),
    ("G_BOARD", "董事会结构与治理", "G", ["G01", "G03", "G04"], 3, 4),
    ("G_ETHICS", "商业道德与反贪污", "G", ["G09", "G10"], 4, 4),
    ("G_DISCLOSURE", "信息披露与透明度", "G", ["G06", "G07", "G08"], 3, 4),
    ("G_RISK", "风险管理与内部控制", "G", [], 3, 4),
]

INDUSTRY_TOPICS: dict[str, list[tuple]] = {
    "manufacturing": [
        ("I_VOC", "废气与 VOCs 治理", "E", ["E23"], 4, 3),
        ("I_HAZWASTE", "危险废弃物处置", "E", ["E20"], 4, 3),
        ("I_PROCESS_SAFETY", "生产工艺安全", "S", ["S12", "S14"], 5, 4),
    ],
    "technology": [
        ("I_DATACENTER", "数据中心能效", "E", ["E01"], 3, 4),
        ("I_EWASTE", "电子废弃物", "E", ["E19"], 3, 2),
        ("I_IP", "知识产权与研发伦理", "G", [], 2, 3),
    ],
    "retail": [
        ("I_PACKAGING", "包装与塑料减量", "E", ["E19"], 3, 3),
        ("I_FOOD_SAFETY", "产品／食品安全", "S", [], 4, 5),
        ("I_CONSUMER", "客户满意度与投诉处理", "S", [], 3, 4),
    ],
    "logistics": [
        ("I_FLEET", "运输排放与车队能效", "E", ["E08", "E01"], 4, 4),
        ("I_WAREHOUSE", "仓储安全", "S", ["S12"], 4, 3),
        ("I_CONTRACTOR", "承包商与外包管理", "S", ["S18"], 3, 3),
    ],
    "real_estate": [
        ("I_GREENBUILD", "绿色建筑与认证", "E", [], 3, 4),
        ("I_CONSTRUCTION", "施工安全", "S", ["S12"], 5, 4),
        ("I_BUILDING_ENERGY", "建筑运行能耗", "E", ["E01"], 4, 4),
    ],
    "banking": [
        ("I_GREENCREDIT", "绿色信贷与可持续金融", "E", [], 3, 4),
        ("I_INVEST", "负责任投资", "G", [], 3, 4),
        ("I_AML", "合规与反洗钱", "G", ["G10"], 4, 5),
    ],
    "services": [
        ("I_WORKCOMPLIANCE", "用工合规", "S", ["S01"], 4, 4),
        ("I_PROFESSIONAL", "专业责任与服务品质", "S", [], 3, 4),
        ("I_KNOWLEDGE", "知识型员工发展", "S", ["S15"], 3, 3),
    ],
    "unknown": [],
}


def topics(industry: str | None) -> dict[str, Any]:
    """返回某行业的议题池与建议初值，供前端评分。"""
    key = industry if industry in INDUSTRY_TOPICS else "unknown"
    items = list(COMMON_TOPICS) + INDUSTRY_TOPICS.get(key, [])
    return {
        "industry": key,
        "threshold": THRESHOLD,
        "count": len(items),
        "topics": [
            {
                "id": t[0],
                "name": t[1],
                "pillar": t[2],
                "metrics": t[3],
                "scale": t[4],
                "scope": t[4],
                "irremediability": max(1, t[4] - 1),
                "likelihood": t[5],
                "magnitude": t[5],
                "horizon": "中期（1–3 年）",
            }
            for t in items
        ],
    }


def _clip(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 3.0
    return max(1.0, min(5.0, f))


def assess(payload: dict[str, Any]) -> dict[str, Any]:
    """payload: {industry, company_name, assessor, scores: {topic_id: {...}}}"""
    pool = {t["id"]: t for t in topics(payload.get("industry"))["topics"]}
    scores = payload.get("scores") or {}
    rows = []
    for tid, meta in pool.items():
        s = scores.get(tid) or {}
        scale = _clip(s.get("scale", meta["scale"]))
        scope = _clip(s.get("scope", meta["scope"]))
        irrem = _clip(s.get("irremediability", meta["irremediability"]))
        lik = _clip(s.get("likelihood", meta["likelihood"]))
        mag = _clip(s.get("magnitude", meta["magnitude"]))
        impact = round((scale + scope + irrem) / 3, 2)
        financial = round((lik + mag) / 2, 2)
        hi, hf = impact >= THRESHOLD, financial >= THRESHOLD
        quadrant = "双重重大" if hi and hf else ("影响重大" if hi else ("财务重大" if hf else "观察项"))
        rows.append({
            "id": tid,
            "name": meta["name"],
            "pillar": meta["pillar"],
            "metrics": meta["metrics"],
            "impact": impact,
            "financial": financial,
            "quadrant": quadrant,
            "horizon": s.get("horizon") or meta["horizon"],
            "rationale": (s.get("rationale") or "").strip(),
        })
    order = {"双重重大": 0, "影响重大": 1, "财务重大": 2, "观察项": 3}
    rows.sort(key=lambda r: (order[r["quadrant"]], -(r["impact"] + r["financial"])))
    counts = {k: sum(1 for r in rows if r["quadrant"] == k) for k in order}
    return {
        "industry": pool and (payload.get("industry") or "unknown"),
        "company_name": payload.get("company_name") or "",
        "assessor": payload.get("assessor") or "",
        "date": date.today().isoformat(),
        "threshold": THRESHOLD,
        "rows": rows,
        "counts": counts,
        "unmapped": [r["name"] for r in rows if not r["metrics"]],
    }


def to_markdown(result: dict[str, Any]) -> str:
    """生成可直接替换报告第三章的 Markdown 正文。"""
    c = result["counts"]
    md = (
        f"本次评估覆盖 {len(result['rows'])} 项议题，采用双重重要性口径："
        f"影响重要性取规模、范围、不可补救性的均值，财务重要性取可能性与影响量级的均值，"
        f"均采用 1–5 分，阈值 {result['threshold']}。\n\n"
        f"结果：**双重重大 {c['双重重大']} 项**、影响重大 {c['影响重大']} 项、"
        f"财务重大 {c['财务重大']} 项、观察项 {c['观察项']} 项。\n\n"
        "| 议题 | 维度 | 影响重要性 | 财务重要性 | 判定 | 已映射采集指标 |\n|---|---|---|---|---|---|\n"
    )
    for r in result["rows"]:
        md += (
            f"| {r['name']} | {r['pillar']} | {r['impact']} | {r['financial']} "
            f"| {r['quadrant']} | {'、'.join(r['metrics']) or '需补数据'} |\n"
        )
    dual = [r for r in result["rows"] if r["quadrant"] == "双重重大"]
    if dual:
        md += "\n**双重重大议题（披露优先）**\n\n"
        for r in dual:
            md += f"- {r['name']}（影响 {r['impact']}，财务 {r['financial']}，{r['horizon']}）\n"
    if result["unmapped"]:
        md += (
            "\n**需补充数据的议题**："
            + "、".join(result["unmapped"])
            + "。这些议题尚未映射到采集表指标，正式披露前需补设定量口径或文字说明。\n"
        )
    explained = [r for r in result["rows"] if r.get("rationale")]
    if explained:
        md += "\n**评分依据与调整理由**\n\n"
        for r in explained:
            md += f"- {r['name']}：{r['rationale']}\n"
    md += (
        "\n**评估留痕**：评估人 "
        + (result["assessor"] or "（待填写）")
        + f"，评估日期 {result['date']}。\n\n"
        "> 评分由企业自评，系统只负责计算与排序，不代替企业判断重要性。"
        "正式对外披露前，建议结合利益相关方访谈或问卷复核评分，并记录调整理由。\n"
    )
    return md


def _metric_label(analysis, code: str) -> str:
    m = analysis.client.metric(code)
    return m.name if m and m.name else code


def suggest_from_analysis(analysis) -> dict[str, Any]:
    """根据采集数据与行业对标，为每个议题生成可解释的评分建议。

    规则（保守、可解释，不把"绩效领先"误判为"议题不重要"）：
    - 议题映射的每个指标：落后 → 压力分 4；接近/领先 → 3；有数据但无对标 → 3；无数据 → 跳过。
    - 议题得分 = 其映射指标压力分的均值（四舍五入到整数，限 1–5）。
    - 影响轴（规模/范围）与财务轴（可能性/影响量级）取同一压力得分；
      不可补救性保留议题默认值（议题固有属性，不因当期数据改变）。
    - 全部映射指标无数据 → 不臆测，沿用行业建议初值，并标注"补数后可复评"。
    """
    from .client_data import resolve_value

    pool = topics(analysis.industry_key)
    gap_by_code = {g.code: g for g in analysis.gaps}
    suggestions: dict[str, dict[str, Any]] = {}
    with_data = no_data = 0

    for t in pool["topics"]:
        pressures: list[float] = []
        evidence: list[str] = []
        has_value = False
        for code in t["metrics"]:
            val = resolve_value(analysis.client, code, analysis.derived)
            gap = gap_by_code.get(code)
            if val is None:
                evidence.append(f"{_metric_label(analysis, code)}：暂无数据")
                continue
            has_value = True
            if gap is not None and gap.median:
                pos = gap.position
                dev = f"{gap.gap_pct:+.1f}%" if gap.gap_pct is not None else "—"
                if pos == "落后":
                    pressures.append(4)
                    evidence.append(
                        f"{gap.name} 落后（{gap.client_value}{gap.unit}，较中位偏离 {dev}）→ 建议上调"
                    )
                elif pos == "领先":
                    pressures.append(3)
                    evidence.append(f"{gap.name} 领先（{gap.client_value}{gap.unit}）→ 议题仍可能重大，不调低")
                else:
                    pressures.append(3)
                    evidence.append(f"{gap.name} 接近中位（{gap.client_value}{gap.unit}）→ 中性")
            else:
                pressures.append(3)
                evidence.append(f"{_metric_label(analysis, code)}：已填报，暂无行业对标 → 中性")

        if not has_value:
            no_data += 1
            suggestions[t["id"]] = {
                "scale": t["scale"], "scope": t["scope"],
                "irremediability": t["irremediability"],
                "likelihood": t["likelihood"], "magnitude": t["magnitude"],
                "horizon": t["horizon"],
                "reason": "映射指标暂无采集数据，已采用行业建议初值；补数后可复评。",
                "evidence": evidence,
                "from_data": False,
            }
            continue

        with_data += 1
        avg = sum(pressures) / len(pressures)
        score = max(1, min(5, int(round(avg))))
        suggestions[t["id"]] = {
            "scale": score, "scope": score,
            "irremediability": t["irremediability"],
            "likelihood": score, "magnitude": score,
            "horizon": t["horizon"],
            "reason": "依据采集数据与行业对标生成，可手动调整；修改后即转为企业自评。",
            "evidence": evidence,
            "from_data": True,
        }

    return {
        "industry": analysis.industry_key,
        "suggestions": suggestions,
        "summary": {
            "with_data": with_data, "no_data": no_data,
            "total": len(pool["topics"]),
        },
    }
