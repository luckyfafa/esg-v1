"""订单台账：把下单、支付、授权码、商品与金额写入可长期留存的外部库。

设计原则
- 授权码仍是无状态的，查单仍以微信 orderquery 为准；台账只是旁路记录。
- 台账写入失败只记日志，不阻断下单、查单与发码。
- 生产环境必须配置 ORDER_DATABASE_URL（推荐免费 Neon / Supabase Postgres）；
  未配置时回落到本地 SQLite，仅用于开发与演示，Render 免费层重新部署会清空。

表结构
- orders：一笔订单一行，保存商品快照、金额、状态、微信交易号、授权码。
- order_events：状态变化审计流水，便于排查“何时由谁写入”。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..config import settings
from . import catalog, licenses

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_SCHEMA_ERROR = ""

STATUSES = ("PENDING", "PAID", "REFUNDED", "CLOSED", "FAILED", "AMOUNT_MISMATCH")
_RANK = {
    "PENDING": 0,
    "FAILED": 1,
    "CLOSED": 2,
    "PAID": 3,
    "REFUNDED": 4,
    "AMOUNT_MISMATCH": 5,
}

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS orders (
        order_no TEXT PRIMARY KEY,
        product_id TEXT NOT NULL DEFAULT '',
        product_name TEXT NOT NULL DEFAULT '',
        order_code TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '',
        amount_fen INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'PENDING',
        paid_fen INTEGER,
        transaction_id TEXT NOT NULL DEFAULT '',
        license_code TEXT NOT NULL DEFAULT '',
        license_scope TEXT NOT NULL DEFAULT '',
        paid_at TEXT NOT NULL DEFAULT '',
        attach TEXT NOT NULL DEFAULT '',
        backfilled INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        raw_json TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
    """CREATE TABLE IF NOT EXISTS order_events (
        event_id TEXT PRIMARY KEY,
        order_no TEXT NOT NULL,
        event TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_order ON order_events(order_no)",
)

_RAW_KEYS = (
    "trade_state",
    "total_fee",
    "cash_fee",
    "transaction_id",
    "time_end",
    "out_trade_no",
    "attach",
    "bank_type",
    "refund_id",
    "refund_fee",
)


# ---------------------------------------------------------------- 后端与时间

def database_url() -> str:
    """外部库连接串；兼容常见的 DATABASE_URL 命名。"""
    return (os.environ.get("ORDER_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()


def backend() -> str:
    return "postgres" if database_url() else "sqlite"


def durable() -> bool:
    """Postgres 才算长期留存；SQLite 在 Render 免费层会随部署清空。"""
    return bool(database_url())


def sqlite_path() -> Path:
    raw = (os.environ.get("ORDER_DB_PATH") or "").strip()
    path = Path(raw) if raw else settings.data_dir / "orders.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def normalize_time(value: str) -> str:
    """统一微信查单（yyyyMMddHHmmss）与账单（yyyy-MM-dd HH:MM:SS）的时间格式。"""
    text = (value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=CST).isoformat(timespec="seconds")
        except ValueError:
            continue
    return text


# ---------------------------------------------------------------- 连接与建表

@contextmanager
def _connect() -> Iterator[tuple[Any, str]]:
    url = database_url()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        import psycopg  # 延迟导入：未配置 Postgres 时本地无需该依赖
        from psycopg.rows import dict_row

        conn = psycopg.connect(
            url, connect_timeout=6, row_factory=dict_row, prepare_threshold=None
        )
        try:
            yield conn, "postgres"
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(sqlite_path(), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn, "sqlite"
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _sql(query: str, kind: str) -> str:
    return query.replace("?", "%s") if kind == "postgres" else query


def _exec(conn: Any, kind: str, query: str, args: tuple = ()) -> Any:
    cur = conn.cursor()
    cur.execute(_sql(query, kind), args)
    return cur


def ensure_schema() -> None:
    global _SCHEMA_READY, _SCHEMA_ERROR
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        try:
            with _connect() as (conn, kind):
                for stmt in _SCHEMA:
                    _exec(conn, kind, stmt)
            _SCHEMA_READY = True
            _SCHEMA_ERROR = ""
        except Exception as e:  # noqa: BLE001
            _SCHEMA_ERROR = f"{type(e).__name__}: {e}"
            raise


def health() -> dict:
    info = {
        "backend": backend(),
        "durable": durable(),
        "schema_ready": _SCHEMA_READY,
        "error": _SCHEMA_ERROR,
    }
    if not durable():
        info["warning"] = (
            "未配置 ORDER_DATABASE_URL，当前写入本地 SQLite；"
            "Render 免费层重新部署会清空，只适合本地/演示。"
        )
    try:
        ensure_schema()
        with _connect() as (conn, kind):
            row = _exec(conn, kind, "SELECT COUNT(*) AS n FROM orders").fetchone()
        info["schema_ready"] = True
        info["error"] = ""
        info["orders"] = int(row["n"] if isinstance(row, dict) else row[0])
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
    return info


# ---------------------------------------------------------------- 写入

def _merge_status(old: str, new: str) -> str:
    """只升不降：未支付轮询不能把已支付改回去；金额异常需人工复核。"""
    if not new:
        return old
    if old == "AMOUNT_MISMATCH":
        return old
    if old in ("PAID", "REFUNDED") and new in ("PENDING", "FAILED", "CLOSED"):
        return old
    if new == "REFUNDED":
        return "REFUNDED"
    if new == "AMOUNT_MISMATCH":
        return "AMOUNT_MISMATCH"
    return new if _RANK.get(new, 0) >= _RANK.get(old, 0) else old


def _raw_json(raw: dict | None) -> str:
    if not raw:
        return ""
    small = {k: raw.get(k) for k in _RAW_KEYS if raw.get(k) not in (None, "")}
    return json.dumps(small, ensure_ascii=False)


def _event(conn: Any, kind: str, order_no: str, event: str, status: str, source: str, payload: dict) -> None:
    _exec(
        conn,
        kind,
        "INSERT INTO order_events (event_id, order_no, event, status, source, payload, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, order_no, event, status, source, json.dumps(payload, ensure_ascii=False), now_iso()),
    )


def _defaults(order_no: str, product: dict | None) -> tuple[str, str, str, str]:
    product = product or catalog.find_by_code(order_no[2:4])
    if not product:
        return "", "", "", ""
    return product["id"], product["name"], product["orderCode"], product["scope"]


def _insert_order(
    conn: Any,
    kind: str,
    order_no: str,
    product: dict | None,
    amount_fen: int,
    status: str,
    paid_fen: int | None,
    transaction_id: str,
    license_code: str,
    license_scope: str,
    paid_at: str,
    attach: str,
    backfilled: bool,
    source: str,
    raw_json: str,
    created_at: str,
) -> None:
    product_id, product_name, order_code, scope = _defaults(order_no, product)
    _exec(
        conn,
        kind,
        "INSERT INTO orders (order_no, product_id, product_name, order_code, scope, amount_fen,"
        " status, paid_fen, transaction_id, license_code, license_scope, paid_at, attach,"
        " backfilled, source, raw_json, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (order_no) DO NOTHING",
        (
            order_no, product_id, product_name, order_code, scope, int(amount_fen),
            status, paid_fen, transaction_id, license_code, license_scope, paid_at, attach,
            1 if backfilled else 0, source, raw_json, created_at, now_iso(),
        ),
    )


def _update_order(
    conn: Any,
    kind: str,
    order_no: str,
    status: str,
    paid_fen: int | None,
    transaction_id: str,
    license_code: str,
    license_scope: str,
    paid_at: str,
    source: str,
    raw_json: str,
) -> None:
    _exec(
        conn,
        kind,
        "UPDATE orders SET status = ?, paid_fen = ?, transaction_id = ?, license_code = ?,"
        " license_scope = ?, paid_at = ?, source = ?, raw_json = ?, updated_at = ?"
        " WHERE order_no = ?",
        (
            status, paid_fen, transaction_id, license_code, license_scope, paid_at,
            source, raw_json, now_iso(), order_no,
        ),
    )


def record_created(
    order_no: str,
    *,
    product: dict | None = None,
    amount_fen: int = 0,
    source: str = "create",
    attach: str = "",
) -> bool:
    """下单成功后写入 PENDING；失败只记日志，不影响下单。"""
    try:
        ensure_schema()
        with _connect() as (conn, kind):
            exists = _exec(conn, kind, "SELECT 1 FROM orders WHERE order_no = ?", (order_no,)).fetchone()
            if exists:
                return False
            _insert_order(
                conn, kind, order_no, product, amount_fen, "PENDING", None, "", "",
                "", "", attach, False, source, "", now_iso(),
            )
            _event(conn, kind, order_no, "create", "PENDING", source, {"amount_fen": amount_fen})
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[ledger] 下单落库失败 %s: %s", order_no, e)
        return False


def record_status(
    order_no: str,
    status: str,
    *,
    paid_fen: int | None = None,
    transaction_id: str = "",
    paid_at: str = "",
    license_code: str = "",
    license_scope: str = "",
    source: str = "poll",
    raw: dict | None = None,
    product: dict | None = None,
    backfilled: bool = False,
) -> bool:
    """写入/更新支付状态；状态没变化时不写库。返回是否发生写入。"""
    order_no = (order_no or "").strip().upper()
    if not order_no:
        return False
    status = status if status in STATUSES else "PENDING"
    paid_at = normalize_time(paid_at)
    try:
        ensure_schema()
        with _connect() as (conn, kind):
            row = _exec(conn, kind, "SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
            existing = dict(row) if row else None
            product = product or catalog.find_by_code(order_no[2:4])
            raw_json = _raw_json(raw)

            if existing is None:
                # 下单时没写成功（或从微信账单补录）：金额以实付为准，避免历史价格变化误报
                amount = int(paid_fen) if (backfilled and paid_fen is not None) else (
                    int(product["priceFen"]) if product else int(paid_fen or 0)
                )
                created = paid_at if backfilled and paid_at else now_iso()
                _insert_order(
                    conn, kind, order_no, product, amount, status, paid_fen, transaction_id,
                    license_code, license_scope, paid_at, "", backfilled, source, raw_json, created,
                )
                _event(conn, kind, order_no, "create", status, source, {"amount_fen": amount})
                return True

            merged = _merge_status(existing["status"], status)
            new_paid = paid_fen if paid_fen is not None else existing["paid_fen"]
            new_txid = transaction_id or existing["transaction_id"]
            new_license = license_code or existing["license_code"]
            new_scope = license_scope or existing["license_scope"]
            new_paid_at = paid_at or existing["paid_at"]
            new_raw = raw_json or existing["raw_json"]
            changed = (
                merged != existing["status"]
                or new_paid != existing["paid_fen"]
                or new_txid != existing["transaction_id"]
                or new_license != existing["license_code"]
                or new_scope != existing["license_scope"]
                or new_paid_at != existing["paid_at"]
            )
            if not changed:
                return False
            _update_order(
                conn, kind, order_no, merged, new_paid, new_txid, new_license, new_scope,
                new_paid_at, source, new_raw,
            )
            _event(
                conn, kind, order_no, "update", merged, source,
                {"from": existing["status"], "to": merged, "paid_fen": new_paid},
            )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[ledger] 状态落库失败 %s %s: %s", order_no, status, e)
        return False


# ---------------------------------------------------------------- 查询与导出

def get_order(order_no: str) -> dict | None:
    ensure_schema()
    with _connect() as (conn, kind):
        row = _exec(conn, kind, "SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
        return dict(row) if row else None


def _filters(date_from: str, date_to: str, product_id: str, status: str) -> tuple[str, list]:
    where: list[str] = []
    args: list[Any] = []
    if date_from:
        where.append("substr(created_at, 1, 10) >= ?")
        args.append(date_from)
    if date_to:
        where.append("substr(created_at, 1, 10) <= ?")
        args.append(date_to)
    if product_id:
        where.append("product_id = ?")
        args.append(product_id)
    if status:
        where.append("status = ?")
        args.append(status)
    return ((" WHERE " + " AND ".join(where)) if where else ""), args


def list_orders(
    *,
    date_from: str = "",
    date_to: str = "",
    product_id: str = "",
    status: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict:
    ensure_schema()
    clause, args = _filters(date_from, date_to, product_id, status)
    limit = max(1, min(int(limit or 200), 2000))
    offset = max(0, int(offset or 0))
    with _connect() as (conn, kind):
        rows = [
            dict(r)
            for r in _exec(
                conn,
                kind,
                f"SELECT * FROM orders{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        ]
        agg = _exec(
            conn,
            kind,
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount_fen), 0) AS amount_fen,"
            " COALESCE(SUM(CASE WHEN status IN ('PAID', 'REFUNDED')"
            " THEN COALESCE(paid_fen, amount_fen) ELSE 0 END), 0) AS paid_fen"
            f" FROM orders{clause}",
            tuple(args),
        ).fetchone()
    agg = dict(agg) if not isinstance(agg, dict) else agg
    return {
        "orders": rows,
        "count": int(agg["n"]),
        "amount_fen": int(agg["amount_fen"]),
        "paid_fen": int(agg["paid_fen"]),
        "limit": limit,
        "offset": offset,
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "product_id": product_id,
            "status": status,
        },
    }


CSV_HEADER = [
    "订单号", "创建时间", "支付时间", "商品ID", "商品名称", "商品码", "scope",
    "订单金额(元)", "实付金额(元)", "状态", "微信交易号", "授权码", "授权scope",
    "来源", "是否补录",
]


def export_csv(**filters) -> str:
    filters["limit"] = 100000
    filters["offset"] = 0
    data = list_orders(**filters)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    for o in data["orders"]:
        paid = "" if o["paid_fen"] is None else f"{int(o['paid_fen']) / 100:.2f}"
        writer.writerow(
            [
                o["order_no"],
                o["created_at"],
                o["paid_at"],
                o["product_id"],
                o["product_name"],
                o["order_code"],
                o["scope"],
                f"{int(o['amount_fen']) / 100:.2f}",
                paid,
                o["status"],
                o["transaction_id"],
                o["license_code"],
                o["license_scope"],
                o["source"],
                "是" if o["backfilled"] else "否",
            ]
        )
    return "\ufeff" + buf.getvalue()


# ---------------------------------------------------------------- 微信账单补录

def reconcile_bill_rows(rows: list[dict]) -> dict:
    """把微信账单行补录/更新到台账，并报告差异。

    每行需包含：order_no / amount_fen / status / transaction_id / paid_at / raw。
    """
    result = {
        "bill_rows": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "mismatches": [],
    }
    ensure_schema()
    with _connect() as (conn, kind):
        for row in rows:
            order_no = (row.get("order_no") or "").strip().upper()
            if not licenses.ORDER_RE.match(order_no):
                result["skipped"] += 1
                continue
            result["bill_rows"] += 1
            amount = int(row.get("amount_fen") or 0)
            status = row.get("status") or "PAID"
            txid = row.get("transaction_id") or ""
            paid_at = normalize_time(row.get("paid_at") or "")
            product = catalog.find_by_code(order_no[2:4])
            license = licenses.issue(order_no) if status in ("PAID", "REFUNDED") else ""
            scope = product["scope"] if product else ""
            raw_json = json.dumps(row, ensure_ascii=False)

            existing_row = _exec(conn, kind, "SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
            existing = dict(existing_row) if existing_row else None
            if existing is None:
                _insert_order(
                    conn, kind, order_no, product, amount, status, amount, txid, license,
                    scope, paid_at, "", True, "reconcile", raw_json, paid_at or now_iso(),
                )
                _event(conn, kind, order_no, "reconcile_insert", status, "reconcile", {"amount_fen": amount})
                result["inserted"] += 1
                continue

            diffs = []
            if amount and int(existing["amount_fen"]) != amount:
                diffs.append({"field": "amount_fen", "ledger": existing["amount_fen"], "wechat": amount})
            if existing["transaction_id"] and txid and existing["transaction_id"] != txid:
                diffs.append({"field": "transaction_id", "ledger": existing["transaction_id"], "wechat": txid})

            merged = _merge_status(existing["status"], status)
            new_paid = amount if amount else existing["paid_fen"]
            new_txid = txid or existing["transaction_id"]
            new_license = existing["license_code"] or license
            new_scope = existing["license_scope"] or scope
            new_paid_at = paid_at or existing["paid_at"]
            changed = (
                merged != existing["status"]
                or new_paid != existing["paid_fen"]
                or new_txid != existing["transaction_id"]
                or new_license != existing["license_code"]
                or new_paid_at != existing["paid_at"]
            )
            if changed:
                _update_order(
                    conn, kind, order_no, merged, new_paid, new_txid, new_license, new_scope,
                    new_paid_at, "reconcile", json.dumps({**row, "bill": row.get("raw", {})}, ensure_ascii=False),
                )
                _event(conn, kind, order_no, "reconcile_update", merged, "reconcile", {"from": existing["status"]})
                result["updated"] += 1
            else:
                result["unchanged"] += 1
            if diffs:
                result["mismatches"].append({"order_no": order_no, "diffs": diffs})
    return result