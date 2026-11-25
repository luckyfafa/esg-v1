"""抓取港股公司的公开 ESG 评级快照并落盘到 v2/content/ratings。

数据来源：新浪财经 ESG 公开接口（免费、无需 token）。
    https://global.finance.sina.com.cn/api/openapi.php/EsgService.getEsgStockInfo?symbol=hk00002

只保留接口实际返回的机构与结果；接口返回 "-" 视为该机构未覆盖，
不补值、不推断、不排序成"好坏"。抓取时间写入文件，便于读者核对时效。

用法：
    python -m v2.scripts.fetch_esg_ratings
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
INDEX_FILE = ROOT / "data" / "hkex_reports" / "_index.json"
OUT_DIR = ROOT / "v2" / "content" / "ratings"

API = "https://global.finance.sina.com.cn/api/openapi.php/EsgService.getEsgStockInfo"
HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (compatible; ESG-Learn/1.0)",
}

# 接口里的 ESG 分项名称，归一化成 E/S/G
DIMENSION_KEYS = {
    "环境总评": "environmental",
    "社会责任总评": "social",
    "治理总评": "governance",
}


def _clean(value):
    """接口用 "-" / 空串表示无数据。"""
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "--", "N/A"):
        return None
    return text


def parse_payload(payload: dict, code: str, company: str) -> dict:
    data = (payload.get("result") or {}).get("data") or {}
    info = data.get("info") or []
    ratings = []
    for item in info:
        score = _clean(item.get("esg_score")) or _clean(item.get("esg_level"))
        if score is None:
            continue
        dims = {}
        for detail in item.get("detail") or []:
            key = DIMENSION_KEYS.get(detail.get("name"))
            if key:
                dims[key] = _clean(detail.get("score"))
        ratings.append(
            {
                "agency": item.get("agency_name"),
                "rating": score,
                "rating_date": _clean(item.get("esg_dt")),
                "environmental": dims.get("environmental"),
                "social": dims.get("social"),
                "governance": dims.get("governance"),
            }
        )
    return {
        "code": code,
        "symbol": f"hk{code}",
        "company": company,
        "fetched_at": date.today().isoformat(),
        "source": "新浪财经 ESG 公开数据",
        "source_url": "https://finance.sina.com.cn/",
        "api": API,
        "note": "仅保留接口返回的机构与结果；未覆盖的机构不补值。不同机构口径不同，分数不可横向比较。",
        "ratings": ratings,
    }


def fetch_snapshot(code: str, company: str, client: httpx.Client | None = None) -> dict:
    """抓取一家公司的公开评级快照；调用方负责决定是否落盘。"""
    if client is None:
        with httpx.Client(timeout=25.0, follow_redirects=True) as own:
            return fetch_snapshot(code, company, own)
    resp = client.get(API, params={"symbol": f"hk{code}"}, headers=HEADERS)
    resp.raise_for_status()
    return parse_payload(resp.json(), code, company)


def fetch_one(client: httpx.Client, code: str, company: str) -> dict:
    return fetch_snapshot(code, company, client)


def main() -> int:
    if not INDEX_FILE.exists():
        print(f"缺少样本索引：{INDEX_FILE}", file=sys.stderr)
        return 1
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, []
    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        for item in index.values():
            code, company = item["code"], item["name_cn"]
            try:
                snapshot = fetch_one(client, code, company)
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)))
                print(f"[FAIL] {code} {company}: {exc}")
                continue
            target = OUT_DIR / f"{code}.json"
            target.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            names = ", ".join(r["agency"] for r in snapshot["ratings"]) or "无公开评级"
            print(f"[OK] {code} {company}: {len(snapshot['ratings'])} 家 ({names})")
            ok += 1

    print(f"\n完成：{ok} 家成功，{len(failed)} 家失败")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
