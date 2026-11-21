"""非上市企业碳核算：范围一（燃料）与范围二（外购电力）。

排放因子均标注来源与年份，便于报告中追溯；电网因子随年度变化，
计算结果页会提示使用者按最新年报核对。
"""
from __future__ import annotations

from typing import Any

# 燃料因子：每单位活动数据的千克二氧化碳
FUEL_FACTORS: dict[str, dict[str, Any]] = {
    "natural_gas": {"label": "天然气", "unit": "m³", "factor": 2.162, "source": "IPCC 2019 默认值"},
    "lpg": {"label": "液化石油气", "unit": "kg", "factor": 3.010, "source": "IPCC 2019 默认值"},
    "diesel": {"label": "柴油", "unit": "L", "factor": 2.653, "source": "IPCC 2019 默认值"},
    "petrol": {"label": "汽油", "unit": "L", "factor": 2.392, "source": "IPCC 2019 默认值"},
    "kerosene": {"label": "煤油", "unit": "L", "factor": 2.536, "source": "IPCC 2019 默认值"},
    "coal": {"label": "煤炭", "unit": "t", "factor": 2460.0, "source": "IPCC 2019 默认值（2.46 t CO₂/t）"},
}

# 电网因子：每度电的千克二氧化碳当量
GRID_FACTORS: dict[str, dict[str, Any]] = {
    "CLP": {
        "label": "中电（九龙及新界）",
        "factor": 0.39,
        "year": 2023,
        "source": "中电 2023 可持续发展报告：中华电力售电量碳排放强度 0.39 kg CO₂e/kWh",
    },
    "HKE": {
        "label": "港灯（香港岛及南丫岛）",
        "factor": 0.66,
        "year": 2023,
        "source": "港灯 2023 绩效数据：每度电售电量 0.66 kg CO₂e（2024 为 0.60）",
    },
    "CN_AVG": {
        "label": "中国内地电网平均",
        "factor": 0.581,
        "year": 2022,
        "source": "中国生态环境部全国电网平均排放因子（参考值）",
    },
}

# 香港交易所《ESG 报告守则》对应条款，用于结果页提示填回位置
HKEX_HINTS = {
    "scope1": "A1.2 直接能源间接温室气体排放",
    "scope2": "A1.2 能源间接温室气体排放",
    "intensity": "A1.2 排放强度（按收入或员工）",
}


def factors() -> dict[str, Any]:
    return {"fuels": FUEL_FACTORS, "grids": GRID_FACTORS, "hkex": HKEX_HINTS}


def calculate(payload: dict[str, Any]) -> dict[str, Any]:
    """按活动数据计算范围一、范围二排放。

    payload:
      fuels: {fuel_key: 用量}
      electricity_kwh: 用电量
      grid: CLP / HKE / CN_AVG / custom
      grid_factor: 自定义电网因子（grid=custom 时使用）
      revenue_hkd_million / employees: 可选，用于强度指标
    """
    items: list[dict[str, Any]] = []
    scope1 = 0.0
    for key, qty in (payload.get("fuels") or {}).items():
        spec = FUEL_FACTORS.get(key)
        try:
            qty_f = float(qty or 0)
        except (TypeError, ValueError):
            qty_f = 0.0
        if not spec or qty_f <= 0:
            continue
        kg = qty_f * spec["factor"]
        scope1 += kg
        items.append({
            "scope": "范围一",
            "name": spec["label"],
            "activity": qty_f,
            "unit": spec["unit"],
            "factor": spec["factor"],
            "factor_source": spec["source"],
            "kg": round(kg, 1),
            "t": round(kg / 1000, 3),
        })

    scope2 = 0.0
    kwh = payload.get("electricity_kwh")
    try:
        kwh_f = float(kwh or 0)
    except (TypeError, ValueError):
        kwh_f = 0.0
    if kwh_f > 0:
        grid = payload.get("grid") or "CLP"
        if grid == "custom":
            try:
                gf = float(payload.get("grid_factor") or 0)
            except (TypeError, ValueError):
                gf = 0.0
            source = "使用者自订电网因子"
            label = "外购电力（自订因子）"
        else:
            spec = GRID_FACTORS.get(grid) or GRID_FACTORS["CLP"]
            gf = float(spec["factor"])
            source = spec["source"]
            label = f"外购电力（{spec['label']}）"
        if gf > 0:
            scope2 = kwh_f * gf
            items.append({
                "scope": "范围二",
                "name": label,
                "activity": kwh_f,
                "unit": "kWh",
                "factor": gf,
                "factor_source": source,
                "kg": round(scope2, 1),
                "t": round(scope2 / 1000, 3),
            })

    total_t = (scope1 + scope2) / 1000
    out: dict[str, Any] = {
        "scope1_t": round(scope1 / 1000, 3),
        "scope2_t": round(scope2 / 1000, 3),
        "total_t": round(total_t, 3),
        "items": items,
        "notes": [
            "结果为运营边界内的范围一与范围二，不含范围三。",
            "电网因子随发电组合逐年变化，正式披露前请按最新年报核对。",
            "若企业使用煤气、蒸汽或区域供冷，请向供应方索取排放因子后以自订因子计算。",
        ],
        "hkex": HKEX_HINTS,
    }

    revenue = _num(payload.get("revenue_hkd_million"))
    employees = _num(payload.get("employees"))
    if revenue and revenue > 0:
        out["intensity_revenue"] = round(total_t / revenue, 4)  # 吨/百万港元收入
    if employees and employees > 0:
        out["intensity_employee"] = round(total_t * 1000 / employees, 1)  # 千克/人
    return out


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
