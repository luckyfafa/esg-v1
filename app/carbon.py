"""非上市企业碳核算：范围一（燃料）与范围二（外购电力）。

排放因子均标注来源与年份，便于报告中追溯；电网因子随年度变化，
计算结果页会提示使用者按最新年报核对。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import openpyxl

# 不确定度分级（按数据来源质量，百分数）：
#   measured    实测/供应商逐笔数据      ±2%
#   published   机构公开发布因子         ±5%
#   default     IPCC/国家缺省因子        ±10%
#   custom      使用者自订因子           ±15%
#   estimated   支出法/行业平均估算       ±25%
UNCERTAINTY = {
    "measured": 2, "published": 5, "default": 10, "custom": 15, "estimated": 25,
}

# 燃料因子：每单位活动数据的千克二氧化碳
FUEL_FACTORS: dict[str, dict[str, Any]] = {
    "natural_gas": {"label": "天然气", "unit": "m³", "factor": 2.162, "source": "IPCC 2019 默认值", "unc": "default"},
    "lpg": {"label": "液化石油气", "unit": "kg", "factor": 3.010, "source": "IPCC 2019 默认值", "unc": "default"},
    "diesel": {"label": "柴油", "unit": "L", "factor": 2.653, "source": "IPCC 2019 默认值", "unc": "default"},
    "petrol": {"label": "汽油", "unit": "L", "factor": 2.392, "source": "IPCC 2019 默认值", "unc": "default"},
    "kerosene": {"label": "煤油", "unit": "L", "factor": 2.536, "source": "IPCC 2019 默认值", "unc": "default"},
    "coal": {"label": "煤炭", "unit": "t", "factor": 2460.0, "source": "IPCC 2019 默认值（2.46 t CO₂/t）", "unc": "default"},
}

# 电网因子：每度电的千克二氧化碳当量
GRID_FACTORS: dict[str, dict[str, Any]] = {
    "CLP": {
        "label": "中电（九龙及新界）",
        "factor": 0.39,
        "year": 2023,
        "source": "中电 2023 可持续发展报告：中华电力售电量碳排放强度 0.39 kg CO₂e/kWh",
        "unc": "published",
    },
    "HKE": {
        "label": "港灯（香港岛及南丫岛）",
        "factor": 0.66,
        "year": 2023,
        "source": "港灯 2023 绩效数据：每度电售电量 0.66 kg CO₂e（2024 为 0.60）",
        "unc": "published",
    },
    "CN_AVG": {
        "label": "中国内地电网平均",
        "factor": 0.581,
        "year": 2022,
        "source": "中国生态环境部全国电网平均排放因子（参考值）",
        "unc": "published",
    },
}

# 范围三因子：对应采集表 E 表 E11–E14。
# 内部统一按 kg CO₂ 计算（活动量 × factor = kg），展示时 ÷1000 转吨。
# 均为支出法/行业平均估算，unc=estimated（±25%），仅供参考，正式披露应向供应商索取实际因子。
SCOPE3_FACTORS: dict[str, dict[str, Any]] = {
    "purchased_goods": {
        "label": "类别1 采购商品与服务", "metric": "E11", "unit": "万元",
        "factor": 500.0,  # 0.5 t CO₂/万元
        "source": "WRI/中国行业平均参考值（估算）：0.5 t CO₂/万元采购额", "unc": "estimated",
    },
    "upstream_transport": {
        "label": "类别4 上游运输配送", "metric": "E12", "unit": "吨·公里",
        "factor": 0.1,  # kg CO₂/吨·公里
        "source": "中国公路货运平均参考值（估算）：0.1 kg CO₂/吨·公里", "unc": "estimated",
    },
    "business_travel": {
        "label": "类别6 商务差旅", "metric": "E13", "unit": "人·公里",
        "factor": 0.18,  # kg CO₂/人·公里
        "source": "航空+高铁混合参考值（估算）：0.18 kg CO₂/人·公里", "unc": "estimated",
    },
    "employee_commute": {
        "label": "类别7 员工通勤", "metric": "E14", "unit": "人·公里",
        "factor": 0.06,  # kg CO₂/人·公里
        "source": "城市公交+地铁+私家车混合参考值（估算）：0.06 kg CO₂/人·公里", "unc": "estimated",
    },
}

# 香港交易所《ESG 报告守则》对应条款，用于结果页提示填回位置
HKEX_HINTS = {
    "scope1": "A1.2 直接能源间接温室气体排放",
    "scope2": "A1.2 能源间接温室气体排放",
    "intensity": "A1.2 排放强度（按收入或员工）",
}


def factors() -> dict[str, Any]:
    return {
        "fuels": FUEL_FACTORS,
        "grids": GRID_FACTORS,
        "scope3": SCOPE3_FACTORS,
        "uncertainty": UNCERTAINTY,
        "hkex": HKEX_HINTS,
    }


def _combine_uncertainty(items: list[dict[str, Any]]) -> dict[str, Any]:
    """按方差加权合成一组明细的不确定度：u = sqrt(Σ(eᵢ·uᵢ)²) / Σeᵢ。

    返回 {pct(%), low(t), high(t), t}；无数据时全部为 0。
    """
    total_kg = sum(i["kg"] for i in items)
    if total_kg <= 0:
        return {"pct": 0.0, "low": 0.0, "high": 0.0, "t": 0.0}
    var = sum((i["kg"] * i["uncertainty_pct"] / 100) ** 2 for i in items)
    pct = (var ** 0.5) / total_kg * 100
    t = total_kg / 1000
    return {
        "pct": round(pct, 1),
        "low": round(t * (1 - pct / 100), 3),
        "high": round(t * (1 + pct / 100), 3),
        "t": round(t, 3),
    }


def calculate(payload: dict[str, Any]) -> dict[str, Any]:
    """按活动数据计算范围一、范围二、范围三排放及不确定性区间。

    payload:
      fuels: {fuel_key: 用量}
      electricity_kwh: 用电量
      grid: CLP / HKE / CN_AVG / custom
      grid_factor: 自定义电网因子（grid=custom 时使用）
      scope3: {类别key: 活动量}，对应 SCOPE3_FACTORS（可选，估算值）
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
            "uncertainty_pct": UNCERTAINTY[spec["unc"]],
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
            unc_pct = UNCERTAINTY["custom"]
        else:
            spec = GRID_FACTORS.get(grid) or GRID_FACTORS["CLP"]
            gf = float(spec["factor"])
            source = spec["source"]
            label = f"外购电力（{spec['label']}）"
            unc_pct = UNCERTAINTY[spec["unc"]]
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
                "uncertainty_pct": unc_pct,
            })

    scope3 = 0.0
    for key, qty in (payload.get("scope3") or {}).items():
        spec = SCOPE3_FACTORS.get(key)
        try:
            qty_f = float(qty or 0)
        except (TypeError, ValueError):
            qty_f = 0.0
        if not spec or qty_f <= 0:
            continue
        kg = qty_f * spec["factor"]
        scope3 += kg
        items.append({
            "scope": "范围三",
            "name": spec["label"],
            "metric": spec["metric"],
            "activity": qty_f,
            "unit": spec["unit"],
            "factor": spec["factor"],
            "factor_source": spec["source"],
            "kg": round(kg, 1),
            "t": round(kg / 1000, 3),
            "uncertainty_pct": UNCERTAINTY[spec["unc"]],
            "estimated": True,
        })

    total_t = (scope1 + scope2 + scope3) / 1000
    items_s1 = [i for i in items if i["scope"] == "范围一"]
    items_s2 = [i for i in items if i["scope"] == "范围二"]
    items_s3 = [i for i in items if i["scope"] == "范围三"]
    out: dict[str, Any] = {
        "scope1_t": round(scope1 / 1000, 3),
        "scope2_t": round(scope2 / 1000, 3),
        "scope3_t": round(scope3 / 1000, 3),
        "total_t": round(total_t, 3),
        "items": items,
        "uncertainty": {
            "scope1": _combine_uncertainty(items_s1),
            "scope2": _combine_uncertainty(items_s2),
            "scope3": _combine_uncertainty(items_s3),
            "total": _combine_uncertainty(items),
        },
        "notes": [
            "范围一、范围二为运营边界内的直接与能源间接排放。",
            "范围三为支出法/行业平均参考值估算（±25%），仅供参考，正式披露应向供应商索取实际因子或逐笔核算。",
            "电网因子随发电组合逐年变化，正式披露前请按最新年报核对。",
            "若企业使用煤气、蒸汽或区域供冷，请向供应方索取排放因子后以自订因子计算。",
        ],
        "hkex": HKEX_HINTS,
    }

    # 运营排放强度（范围一+二），口径与采集表 OPS_TOTAL 一致
    ops_t = (scope1 + scope2) / 1000
    revenue = _num(payload.get("revenue_hkd_million"))
    employees = _num(payload.get("employees"))
    if revenue and revenue > 0:
        out["intensity_revenue"] = round(ops_t / revenue, 4)  # 吨/百万港元收入
    if employees and employees > 0:
        out["intensity_employee"] = round(ops_t * 1000 / employees, 1)  # 千克/人
    return out


def estimate_uncertainty(scope1_t: float | None, scope2_t: float | None,
                         scope3_t: float | None = None) -> dict[str, Any]:
    """对已填报的排放量（非活动数据现算）给出默认不确定性区间。

    用于报告：填报值来源质量未知时，范围一/二取缺省因子级（±10%），
    范围三取估算级（±25%）；企业有实测数据可自行收窄。
    """
    def _band(t: float | None, pct: float) -> dict[str, Any] | None:
        if t is None:
            return None
        t = float(t)
        return {
            "pct": pct,
            "low": round(t * (1 - pct / 100), 3),
            "high": round(t * (1 + pct / 100), 3),
            "t": round(t, 3),
        }
    u1 = _band(scope1_t, UNCERTAINTY["default"])
    u2 = _band(scope2_t, UNCERTAINTY["default"])
    u3 = _band(scope3_t, UNCERTAINTY["estimated"])
    parts = [u for u in (u1, u2, u3) if u is not None]
    total = None
    if parts:
        var = sum((p["t"] * p["pct"] / 100) ** 2 for p in parts)
        t_sum = sum(p["t"] for p in parts)
        pct = (var ** 0.5) / t_sum * 100 if t_sum else 0.0
        total = {
            "pct": round(pct, 1),
            "low": round(t_sum * (1 - pct / 100), 3),
            "high": round(t_sum * (1 + pct / 100), 3),
            "t": round(t_sum, 3),
        }
    return {"scope1": u1, "scope2": u2, "scope3": u3, "total": total}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


# ========== 批量活动数据解析（上传能源明细表） ==========

# 列名别名 → (分类, 字段键)。匹配前会归一化（小写、去空格/括号/单位后缀）。
ACTIVITY_ALIASES: dict[str, tuple[str, str]] = {
    # 范围二 电力
    "电量": ("electricity", "kwh"), "用电量": ("electricity", "kwh"),
    "电力消耗": ("electricity", "kwh"), "外购电力": ("electricity", "kwh"),
    "电力": ("electricity", "kwh"), "electricity": ("electricity", "kwh"),
    "electricpower": ("electricity", "kwh"), "powerconsumption": ("electricity", "kwh"),
    # 范围一 燃料
    "天然气": ("fuels", "natural_gas"), "燃气": ("fuels", "natural_gas"),
    "管道气": ("fuels", "natural_gas"), "naturalgas": ("fuels", "natural_gas"),
    "液化石油气": ("fuels", "lpg"), "石油气": ("fuels", "lpg"), "lpg": ("fuels", "lpg"),
    "柴油": ("fuels", "diesel"), "diesel": ("fuels", "diesel"),
    "汽油": ("fuels", "petrol"), "petrol": ("fuels", "petrol"), "gasoline": ("fuels", "petrol"),
    "煤油": ("fuels", "kerosene"), "kerosene": ("fuels", "kerosene"),
    "煤炭": ("fuels", "coal"), "煤": ("fuels", "coal"), "coal": ("fuels", "coal"),
    # 范围三
    "采购商品与服务": ("scope3", "purchased_goods"), "采购商品": ("scope3", "purchased_goods"),
    "采购额": ("scope3", "purchased_goods"), "采购金额": ("scope3", "purchased_goods"),
    "purchasedgoods": ("scope3", "purchased_goods"),
    "上游运输": ("scope3", "upstream_transport"), "上游运输配送": ("scope3", "upstream_transport"),
    "运输吨公里": ("scope3", "upstream_transport"), "吨公里": ("scope3", "upstream_transport"),
    "upstreamtransport": ("scope3", "upstream_transport"),
    "商务差旅": ("scope3", "business_travel"), "差旅": ("scope3", "business_travel"),
    "businesstravel": ("scope3", "business_travel"),
    "员工通勤": ("scope3", "employee_commute"), "通勤": ("scope3", "employee_commute"),
    "employeecommute": ("scope3", "employee_commute"),
}


def _normalize_col(name: Any) -> str:
    if name is None:
        return ""
    s = str(name).lower()
    s = re.sub(r"[\s\(\)（）【】\[\]「」/／、,，.。:：;；*]", "", s)
    for u in ("kwh", "mwh", "m³", "m3", "立方米", "升", "吨", "万元", "公斤", "千克", "公里", "人公里", "吨公里"):
        if s.endswith(u):
            s = s[:-len(u)]
    return s


_NORM_ALIASES: dict[str, tuple[str, str]] = {_normalize_col(k): v for k, v in ACTIVITY_ALIASES.items()}


def _match_column(name: Any) -> tuple[str, str] | None:
    n = _normalize_col(name)
    if not n:
        return None
    if n in _NORM_ALIASES:
        return _NORM_ALIASES[n]
    cand = [(k, v) for k, v in _NORM_ALIASES.items() if k and (k in n or n in k)]
    if cand:
        cand.sort(key=lambda x: len(x[0]), reverse=True)
        return cand[0][1]
    return None


def parse_activity_sheet(rows: list[list]) -> tuple[dict, list, list]:
    """把上传的明细表（首行表头）解析成 calculate 的 payload。

    返回 (payload, recognized, unrecognized)：
      recognized: [{column, category, key}]
      unrecognized: [未识别的列名]
    """
    if not rows:
        return {}, [], []
    header = [("" if c is None else str(c)) for c in rows[0]]
    col_map: dict[int, tuple[str, str]] = {}
    unrec: list[str] = []
    for idx, h in enumerate(header):
        m = _match_column(h)
        if m:
            col_map[idx] = m
        elif h.strip():
            unrec.append(h)
    fuels: dict[str, float] = {}
    scope3: dict[str, float] = {}
    elec = 0.0
    for row in rows[1:]:
        for idx, (cat, key) in col_map.items():
            if idx >= len(row):
                continue
            v = _num(row[idx])
            if v is None:
                continue
            if cat == "electricity":
                elec += v
            elif cat == "fuels":
                fuels[key] = fuels.get(key, 0.0) + v
            elif cat == "scope3":
                scope3[key] = scope3.get(key, 0.0) + v
    payload = {"fuels": fuels, "electricity_kwh": elec, "scope3": scope3}
    recognized = [{"column": header[i], "category": cat, "key": key} for i, (cat, key) in col_map.items()]
    return payload, recognized, unrec


def parse_activity_bytes(data: bytes, filename: str) -> tuple[dict, list, list]:
    """从 xlsx/csv 字节读取并解析。"""
    name = (filename or "").lower()
    rows: list[list] = []
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    else:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        ws = wb.active
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
    return parse_activity_sheet(rows)


# ========== 反向读取：企业上传自己的 ESG 采集表即可算碳 ==========

COLLECTION_SHEET = "E-环境"
# E 表：编号 → 行号（表头在第 4 行，本期数值在 G 列）
_COLLECTION_ROWS: dict[str, int] = {
    "E01": 5, "E02": 6, "E03": 7, "E04": 8, "E05": 9, "E06": 10,
    "E08": 12, "E09": 13, "E10": 14, "E11": 15, "E12": 16, "E13": 17, "E14": 18,
}


def parse_collection_bytes(data: bytes) -> dict[str, Any] | None:
    """识别并读取「非上市企业ESG数据采集表」的 E 表。

    不是该采集表时返回 None。E02/E03/E04 是活动数据，可直接算碳；
    E08–E14 是表内已填的排放值，可作为缺省采用。
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        return None
    if COLLECTION_SHEET not in wb.sheetnames:
        return None
    ws = wb[COLLECTION_SHEET]
    val: dict[str, float | None] = {}
    name: dict[str, str] = {}
    for code, row in _COLLECTION_ROWS.items():
        val[code] = _num(ws.cell(row, 7).value)
        name[code] = str(ws.cell(row, 2).value or code).strip()

    notes: list[str] = []
    recognized: list[dict[str, Any]] = []

    elec_mwh = val.get("E02")
    gas_m3 = val.get("E03")
    fuel_l = val.get("E04")

    fuels: dict[str, float] = {}
    if gas_m3:
        fuels["natural_gas"] = gas_m3
        recognized.append({"column": f"{name['E03']} {gas_m3:g} m³", "category": "fuels", "key": "natural_gas"})
    if fuel_l:
        fuels["diesel"] = fuel_l
        recognized.append({"column": f"{name['E04']} {fuel_l:g} L（汽柴油合计按柴油计）", "category": "fuels", "key": "diesel"})
        notes.append("采集表 E04 为「汽油/柴油消耗量」合计值，已统一按柴油因子（2.653 kg CO₂/L）估算；如能拆分汽油，请在左侧分别填写后重算。")

    electricity_kwh = elec_mwh * 1000 if elec_mwh else 0.0
    if elec_mwh:
        recognized.append({"column": f"{name['E02']} {elec_mwh:g} MWh（= {electricity_kwh:,.0f} kWh）", "category": "electricity", "key": "kwh"})

    if val.get("E05"):
        notes.append(f"采集表 E05「{name['E05']}」{val['E05']:g} GJ 未计入：引擎暂无外购热力因子，请向供方索取排放因子后手动补算。")
    if val.get("E06"):
        notes.append(f"采集表 E06 清洁能源 {val['E06']:g} MWh 未从外购电力中核减，当前按电网平均因子计算（地区法口径）。")

    emissions = {c: val[c] for c in ("E08", "E09", "E10", "E11", "E12", "E13", "E14") if val.get(c)}
    if not recognized and not emissions:
        notes.append("这张表还没有可用的能耗或排放数据（E02 外购电力 / E03 天然气 / E04 汽柴油均为空），请先在表里填能耗，或手工填写。")

    return {
        "payload": {"fuels": fuels, "electricity_kwh": electricity_kwh, "scope3": {}},
        "emissions": emissions,
        "recognized": recognized,
        "unrecognized": [],
        "notes": notes,
        "refill": {"electricity_kwh": electricity_kwh, "fuels": fuels},
    }


def calculate_from_collection(parsed: dict[str, Any], grid: str = "CN_AVG") -> dict[str, Any]:
    """用采集表里的活动数据/已填排放生成与 calculate 同形的结果。"""
    payload = dict(parsed.get("payload") or {})
    payload["grid"] = grid
    res = calculate(payload)
    em = parsed.get("emissions") or {}
    notes = list(parsed.get("notes") or [])
    items = res["items"]

    def _filled_item(scope_label: str, code: str, v: float, unc_key: str) -> dict[str, Any]:
        return {
            "scope": scope_label,
            "name": f"采集表 {code} 已填值",
            "metric": code,
            "activity": round(v, 3),
            "unit": "tCO2e",
            "factor": "—",
            "factor_source": f"采集表本期数值（{code}）",
            "kg": round(v * 1000, 1),
            "t": round(v, 3),
            "uncertainty_pct": UNCERTAINTY[unc_key],
        }

    def _apply_filled(scope_label: str, code: str, curr_t: float, unc_key: str) -> float:
        v = em.get(code)
        if not v:
            return curr_t
        if curr_t <= 0:
            items.append(_filled_item(scope_label, code, v, unc_key))
            notes.append(f"{scope_label} 直接采用采集表 {code} 已填的 {v:g} tCO₂e（未用活动数据重算）。")
            return v
        diff = (curr_t - v) / v * 100
        notes.append(f"{scope_label}：本引擎按活动数据算得 {curr_t:g} tCO₂e，表内 {code} 已填 {v:g} tCO₂e，相差 {diff:+.0f}%；以哪个为准由企业确认。")
        return curr_t

    s1 = _apply_filled("范围一", "E08", res["scope1_t"], "default")
    s2 = _apply_filled("范围二", "E09", res["scope2_t"], "default")

    s3 = 0.0
    for code in ("E11", "E12", "E13", "E14"):
        v = em.get(code)
        if v:
            s3 += v
            items.append(_filled_item("范围三", code, v, "estimated"))
    if s3 > 0:
        notes.append("范围三直接采用采集表 E11–E14 已填数值（表中已是 tCO2e，无需再用活动数据换算）。")

    s1_items = [i for i in items if i["scope"] == "范围一"]
    s2_items = [i for i in items if i["scope"] == "范围二"]
    s3_items = [i for i in items if i["scope"] == "范围三"]
    res["scope1_t"] = round(s1, 3)
    res["scope2_t"] = round(s2, 3)
    res["scope3_t"] = round(s3, 3)
    res["total_t"] = round(s1 + s2 + s3, 3)
    res["uncertainty"] = {
        "scope1": _combine_uncertainty(s1_items),
        "scope2": _combine_uncertainty(s2_items),
        "scope3": _combine_uncertainty(s3_items),
        "total": _combine_uncertainty(items),
    }
    res["notes"] = notes
    res["source"] = "collection_sheet"
    res["recognized"] = parsed.get("recognized") or []
    res["unrecognized"] = parsed.get("unrecognized") or []
    res["refill"] = parsed.get("refill") or {}
    return res


def activity_template_csv() -> str:
    """下载用的活动数据标准模板（CSV，含表头与示例行）。"""
    return ("用电量(kWh),天然气(m³),柴油(L),汽油(L),煤炭(t),"
            "采购商品与服务(万元),商务差旅(人·公里),员工通勤(人·公里)\n"
            "120000,3000,500,200,15,800,12000,40000\n")


# ========== 回填导出：生成已填好排放结果的采集表 ==========

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "docs" / "非上市企业ESG数据采集表.xlsx"
# E 表行号：E08=12 范围一, E09=13 范围二, E11~E14=15~18 范围三
_METRIC_ROW = {"E11": 15, "E12": 16, "E13": 17, "E14": 18}


def build_filled_template(calc_result: dict) -> bytes:
    """读取采集表模板，写入范围一/二/三排放结果，返回 xlsx 字节。"""
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    ws = wb["E-环境"]

    def put(row: int, val, source: str, remark: str) -> None:
        ws.cell(row, 7).value = val          # 本期数值
        ws.cell(row, 10).value = source      # 数据来源
        ws.cell(row, 12).value = "已填"      # 填报状态
        ws.cell(row, 13).value = remark      # 备注

    put(12, calc_result.get("scope1_t"), "碳核算引擎",
        "燃料燃烧直接排放，因子来源：IPCC 2019 默认值（系统估算）")
    put(13, calc_result.get("scope2_t"), "碳核算引擎",
        "外购电力间接排放，按电网平均因子估算（系统估算，未核减绿电/绿证）")
    for it in calc_result.get("items", []):
        if it.get("scope") == "范围三":
            r = _METRIC_ROW.get(it.get("metric", ""))
            if r:
                put(r, it.get("t"), "碳核算引擎",
                    f"{it.get('factor_source','')}（系统估算，不确定度 ±25%）")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
