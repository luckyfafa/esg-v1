"""商品目录：价格的唯一来源。

价格写在 v2/products.json（随仓库提交，Render 重新部署不丢失）。
前端下单只传 productId，金额一律由服务端按此表计算，前端改价无效。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CATALOG_FILE = Path(__file__).resolve().parents[2] / "products.json"
CACHE_SECONDS = 2.0

_cache: dict = {"at": 0.0, "items": []}


def _load() -> list[dict]:
    now = time.time()
    if now - float(_cache["at"]) < CACHE_SECONDS:
        return _cache["items"]

    try:
        raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = []

    items: list[dict] = []
    for p in raw if isinstance(raw, list) else []:
        try:
            price = int(p.get("priceFen"))
        except (TypeError, ValueError):
            continue
        code = str(p.get("orderCode") or "").strip().upper()
        scope = str(p.get("scope") or "template").strip().lower()
        if not p.get("id") or not p.get("name") or price <= 0:
            continue
        if len(code) != 2 or not code.isalpha():
            continue
        if scope not in ("template", "report", "polish"):
            continue
        items.append(
            {
                "id": str(p["id"]),
                "orderCode": code,
                "scope": scope,
                "shortName": str(p.get("shortName") or p["name"])[:30],
                "name": str(p["name"])[:60],
                "desc": str(p.get("desc") or ""),
                "benefits": [str(x)[:120] for x in (p.get("benefits") or [])][:6],
                "priceFen": price,
                "active": p.get("active") is not False,
            }
        )

    _cache.update(at=now, items=items)
    return items


def list_active() -> list[dict]:
    """上架商品。"""
    return [p for p in _load() if p["active"]]


def find(product_id: str) -> dict | None:
    """按 id 查找上架商品。"""
    if not product_id:
        return None
    return next((p for p in list_active() if p["id"] == str(product_id)), None)


def find_by_code(order_code: str) -> dict | None:
    """按订单号里的商品码查找（回调与查单时校验金额用）。"""
    code = str(order_code or "").upper()
    return next((p for p in _load() if p["orderCode"] == code), None)


def public_items() -> list[dict]:
    """给前端展示的字段（不含内部订单码）。"""
    return [
        {
            "id": p["id"],
            "scope": p["scope"],
            "shortName": p["shortName"],
            "name": p["name"],
            "desc": p["desc"],
            "benefits": p["benefits"],
            "price": f"{p['priceFen'] / 100:.2f}",
            "priceFen": p["priceFen"],
        }
        for p in list_active()
    ]