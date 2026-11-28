"""订单台账管理：查询、CSV 导出、微信账单补录与查单核对。

全部接口要求 ADMIN_TOKEN（查询参数 token=...）。台账只读旁路，
不会改变授权码签发与查单逻辑。
"""
from __future__ import annotations

import hmac
import html
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from . import catalog, ledger, licenses, wechatpay

router = APIRouter(prefix="/api/pay/admin", tags=["pay-admin"])
status_router = APIRouter(prefix="/api/pay", tags=["pay"])

CST = timezone(timedelta(hours=8))
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_QUERY_STATUS = {
    "SUCCESS": "PAID",
    "REFUND": "REFUNDED",
    "NOTPAY": "PENDING",
    "USERPAYING": "PENDING",
    "CLOSED": "CLOSED",
    "REVOKED": "CLOSED",
    "PAYERROR": "FAILED",
}


def _require_admin(token: str) -> None:
    expected = (os.environ.get("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail="未配置 ADMIN_TOKEN，台账管理已禁用")
    if not hmac.compare_digest((token or "").strip(), expected):
        raise HTTPException(status_code=403, detail="口令错误")


def _check_date(value: str, field: str) -> str:
    value = (value or "").strip()
    if value and not _DATE_RE.match(value):
        raise HTTPException(status_code=400, detail=f"{field} 需为 YYYY-MM-DD")
    return value


def _query_status(raw: dict) -> tuple[str, str, str]:
    """微信查单结果 → (台账状态, trade_state, 错误)。"""
    if raw.get("return_code") != "SUCCESS" or raw.get("result_code") != "SUCCESS":
        message = (
            raw.get("err_code_des")
            or raw.get("err_code")
            or raw.get("return_msg")
            or "微信查单失败"
        )
        return "", "", message
    state = str(raw.get("trade_state") or "")
    return _QUERY_STATUS.get(state, "PENDING"), state, ""


def _bill_rows(raw_rows: list[dict]) -> list[dict]:
    """微信账单原始行 → 台账补录行。"""
    rows: list[dict] = []
    for r in raw_rows:
        state = str(r.get("交易状态") or "").upper()
        status = "PAID" if state == "SUCCESS" else ("REFUNDED" if "REFUND" in state else "PAID")
        rows.append(
            {
                "order_no": r.get("商户订单号", ""),
                "amount_fen": wechatpay.yuan_to_fen(r.get("总金额", "")),
                "status": status,
                "transaction_id": r.get("微信订单号", ""),
                "paid_at": r.get("交易时间", ""),
                "raw": r,
            }
        )
    return rows


def _filters(date_from: str, date_to: str, product: str, status: str, limit: int, offset: int) -> dict:
    return {
        "date_from": _check_date(date_from, "date_from"),
        "date_to": _check_date(date_to, "date_to"),
        "product_id": (product or "").strip(),
        "status": (status or "").strip().upper(),
        "limit": max(1, min(int(limit or 200), 2000)),
        "offset": max(0, int(offset or 0)),
    }


# ---------------------------------------------------------------- 公共状态

@status_router.get("/ledger/status")
def ledger_status():
    """公开的台账连通状态：只暴露后端类型与是否持久，不暴露订单数据。"""
    info = ledger.health()
    return {
        "backend": info["backend"],
        "durable": info["durable"],
        "ready": info["schema_ready"],
        "warning": info.get("warning", ""),
    }


# ---------------------------------------------------------------- 管理接口

@router.get("/ledger/health")
def ledger_health(token: str = ""):
    _require_admin(token)
    return ledger.health()


@router.get("/ledger")
def ledger_list(
    token: str = "",
    date_from: str = "",
    date_to: str = "",
    product: str = "",
    status: str = "",
    limit: int = 200,
    offset: int = 0,
):
    """订单台账 JSON：按日期、商品、状态筛选，附汇总金额。"""
    _require_admin(token)
    data = ledger.list_orders(**_filters(date_from, date_to, product, status, limit, offset))
    return {**data, "health": ledger.health()}


@router.get("/ledger.csv")
def ledger_csv(
    token: str = "",
    date_from: str = "",
    date_to: str = "",
    product: str = "",
    status: str = "",
):
    """导出对账清单 CSV（UTF-8 BOM，Excel 可直接打开）。"""
    _require_admin(token)
    content = ledger.export_csv(**_filters(date_from, date_to, product, status, 100000, 0))
    name = f"esg-orders-{datetime.now(CST).strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/ledger/reconcile")
def ledger_reconcile(
    token: str = "",
    date: str = Query("", description="账单日期 YYYY-MM-DD，微信账单为 T+1"),
):
    """下载微信交易账单并补录台账，返回条数与差异。"""
    _require_admin(token)
    day = _check_date(date, "date")
    if not day:
        raise HTTPException(status_code=400, detail="请提供 date=YYYY-MM-DD")
    if not wechatpay.settings.enabled:
        raise HTTPException(status_code=503, detail="微信支付未配置，无法下载账单")
    bill = wechatpay.download_bill(day.replace("-", ""))
    if not bill["ok"]:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "BILL_UNAVAILABLE",
                "message": bill["message"] or "账单不可用（T+1 生成，或当日无交易）",
                "bill_date": day,
            },
        )
    summary = ledger.reconcile_bill_rows(_bill_rows(bill["rows"]))
    return {
        "ok": True,
        "bill_date": day,
        "bill_rows": len(bill["rows"]),
        "bill_summary": bill["summary"],
        **summary,
    }


@router.get("/ledger/verify")
def ledger_verify(
    token: str = "",
    orderNo: str = "",
    date_from: str = "",
    date_to: str = "",
    product: str = "",
    status: str = "",
    limit: int = 20,
):
    """逐单向微信 orderquery 核对；只核对台账中已有订单（最多 50 单）。"""
    _require_admin(token)
    if not wechatpay.settings.enabled:
        raise HTTPException(status_code=503, detail="微信支付未配置，无法查单")

    if orderNo.strip():
        order = ledger.get_order(orderNo.strip().upper())
        if not order:
            raise HTTPException(status_code=404, detail="台账无此订单，请先对账补录")
        orders = [order]
    else:
        filters = _filters(date_from, date_to, product, status, limit, 0)
        filters["limit"] = min(int(filters["limit"]), 50)
        orders = ledger.list_orders(**filters)["orders"]

    results = []
    for order in orders:
        raw = wechatpay.order_query(order["order_no"])
        wechat_status, state, error = _query_status(raw)
        if error:
            results.append({"orderNo": order["order_no"], "match": None, "error": error})
            continue

        amount = int(raw.get("total_fee") or 0)
        expected = int(order["amount_fen"] or 0)
        target = wechat_status
        if wechat_status == "PAID" and expected and amount != expected:
            target = "AMOUNT_MISMATCH"

        license_code = licenses.issue(order["order_no"]) if target in ("PAID", "REFUNDED") else ""
        prod = catalog.find_by_code(order["order_no"][2:4])
        updated = ledger.record_status(
            order["order_no"],
            target,
            paid_fen=amount if target in ("PAID", "REFUNDED", "AMOUNT_MISMATCH") else None,
            transaction_id=raw.get("transaction_id") or "",
            paid_at=raw.get("time_end") or "",
            license_code=license_code,
            license_scope=prod["scope"] if prod else "",
            source="verify",
            raw=raw,
            product=prod,
        )
        results.append(
            {
                "orderNo": order["order_no"],
                "ledger": {
                    "status": order["status"],
                    "amountFen": expected,
                    "transactionId": order["transaction_id"],
                },
                "wechat": {
                    "tradeState": state,
                    "totalFee": amount,
                    "transactionId": raw.get("transaction_id") or "",
                },
                "match": target == order["status"] and target != "AMOUNT_MISMATCH",
                "updated": updated,
            }
        )

    return {
        "checked": len(results),
        "matched": sum(1 for r in results if r.get("match") is True),
        "results": results,
    }


@router.get("/ledger/import")
def ledger_import(token: str = "", orderNo: str = ""):
    """按订单号向微信查单并立即补录（同一天账单尚未生成时使用）。"""
    _require_admin(token)
    order_no = orderNo.strip().upper()
    if not licenses.ORDER_RE.match(order_no):
        raise HTTPException(status_code=400, detail="订单号格式不正确")
    if not wechatpay.settings.enabled:
        raise HTTPException(status_code=503, detail="微信支付未配置，无法查单")

    raw = wechatpay.order_query(order_no)
    status, state, error = _query_status(raw)
    if error:
        raise HTTPException(status_code=404, detail=error)
    if status not in ("PAID", "REFUNDED", "PENDING", "CLOSED", "FAILED"):
        raise HTTPException(status_code=409, detail=f"订单状态不可补录：{state}")

    amount = int(raw.get("total_fee") or 0)
    prod = catalog.find_by_code(order_no[2:4])
    ok = ledger.record_status(
        order_no,
        status,
        paid_fen=amount if status in ("PAID", "REFUNDED") else None,
        transaction_id=raw.get("transaction_id") or "",
        paid_at=raw.get("time_end") or "",
        license_code=licenses.issue(order_no) if status in ("PAID", "REFUNDED") else "",
        license_scope=prod["scope"] if prod else "",
        source="import",
        raw=raw,
        product=prod,
        backfilled=True,
    )
    return {"ok": True, "updated": ok, "order": ledger.get_order(order_no)}


# ---------------------------------------------------------------- 管理页

@router.get("/ledger/view", response_class=HTMLResponse)
def ledger_view(
    token: str = "",
    date_from: str = "",
    date_to: str = "",
    product: str = "",
    status: str = "",
    limit: int = 200,
):
    """人类可读的台账页：筛选、汇总、CSV 导出、对账/核对入口。"""
    _require_admin(token)
    filters = _filters(date_from, date_to, product, status, limit, 0)
    health = ledger.health()

    try:
        data = ledger.list_orders(**filters)
        rows = data["orders"]
        load_error = ""
    except Exception as e:  # noqa: BLE001
        data = {"count": 0, "amount_fen": 0, "paid_fen": 0}
        rows = []
        load_error = f"{type(e).__name__}: {e}"

    def esc(v) -> str:
        return html.escape(str(v if v is not None else ""))

    def yuan(fen) -> str:
        return f"{int(fen or 0) / 100:,.2f}"

    body = "".join(
        "<tr>"
        f"<td>{esc(o['created_at'][:16].replace('T', ' '))}</td>"
        f"<td>{esc((o['paid_at'] or '')[:16].replace('T', ' '))}</td>"
        f"<td><code>{esc(o['order_no'])}</code></td>"
        f"<td>{esc(o['product_name'] or o['product_id'])}<br><span class='muted'>{esc(o['order_code'])} · {esc(o['scope'])}</span></td>"
        f"<td class='num'>{yuan(o['amount_fen'])}</td>"
        f"<td class='num'>{yuan(o['paid_fen']) if o['paid_fen'] is not None else ''}</td>"
        f"<td><span class='st st-{esc(o['status'].lower())}'>{esc(o['status'])}</span></td>"
        f"<td><code>{esc(o['transaction_id'])}</code></td>"
        f"<td><code class='lic'>{esc(o['license_code'])}</code></td>"
        f"<td>{esc(o['source'])}{' · 补录' if o['backfilled'] else ''}</td>"
        "</tr>"
        for o in rows
    ) or f"<tr><td colspan='10'>暂无订单{'（' + esc(load_error) + '）' if load_error else ''}</td></tr>"

    query = {"token": token, "date_from": filters["date_from"], "date_to": filters["date_to"],
             "product": filters["product_id"], "status": filters["status"]}
    csv_url = "/api/pay/admin/ledger.csv?" + urlencode(query)
    reconcile_url = "/api/pay/admin/ledger/reconcile?" + urlencode({**query, "date": (datetime.now(CST) - timedelta(days=1)).strftime("%Y-%m-%d")})
    verify_url = "/api/pay/admin/ledger/verify?" + urlencode({**query, "limit": 20})
    status_url = "/api/pay/ledger/status"

    options = "".join(
        f'<option value="{esc(p["id"])}"{" selected" if p["id"] == filters["product_id"] else ""}>{esc(p["shortName"])}</option>'
        for p in catalog.list_active()
    )
    statuses = "".join(
        f'<option value="{s}"{" selected" if s == filters["status"] else ""}>{s}</option>'
        for s in ledger.STATUSES
    )
    backend_label = "Postgres（持久）" if health["durable"] else "SQLite（临时，仅演示）"
    warn = ""
    if not health["durable"]:
        warn = (
            '<div class="warn">当前未配置 ORDER_DATABASE_URL，台账写在本地 SQLite，'
            "Render 重新部署会清空。请配置免费 Neon/Supabase Postgres 后再对外对账。</div>"
        )

    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>订单台账 · 对账</title>
<style>
body{{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:24px;color:#161A17;background:#FBFBF7}}
h1{{font-size:19px;margin:0 0 4px}}
.meta{{color:#6B726C;font-size:12.5px;margin-bottom:10px}}
.warn{{background:#FAF4F0;border:1px solid #EAD8CC;border-left:3px solid #A8542A;padding:9px 12px;
      border-radius:6px;font-size:12.5px;color:#6B4432;margin:10px 0}}
form{{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin:12px 0}}
label{{display:block;font-size:11.5px;color:#6B726C;margin-bottom:4px}}
input,select{{padding:7px 9px;border:1px solid #E2E3D8;border-radius:6px;font:inherit;background:#fff}}
button{{background:#0F5132;color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer}}
a{{color:#0F5132}}
.tools{{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;margin:8px 0 12px}}
table{{border-collapse:collapse;width:100%;font-size:12px;background:#fff}}
th,td{{border:1px solid #E2E3D8;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#F7F7F1;color:#6B726C;font-weight:600;white-space:nowrap}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
code{{font-size:11px;word-break:break-all}}
.lic{{max-width:190px;display:inline-block}}
.muted{{color:#6B726C;font-size:10.5px}}
.st{{padding:1px 7px;border-radius:99px;font-size:10.5px;border:1px solid}}
.st-paid{{background:#EFF5F1;color:#1F6B4C;border-color:#C9DCD2}}
.st-pending{{background:#FBF6E8;color:#8A6A1F;border-color:#E9DDBB}}
.st-refunded,.st-closed,.st-failed,.st-amount_mismatch{{background:#FBEFED;color:#A33B32;border-color:#EBCFC9}}
</style></head><body>
<h1>订单台账 · 对账</h1>
<div class="meta">后端：<b>{backend_label}</b> · 共 {data['count']} 笔 ·
订单金额 ¥{yuan(data['amount_fen'])} · 实收 ¥{yuan(data['paid_fen'])}</div>
{warn}
<form method="get" action="/api/pay/admin/ledger/view">
<input type="hidden" name="token" value="{esc(token)}">
<div><label>开始日期</label><input type="date" name="date_from" value="{esc(filters['date_from'])}"></div>
<div><label>结束日期</label><input type="date" name="date_to" value="{esc(filters['date_to'])}"></div>
<div><label>商品</label><select name="product"><option value="">全部</option>{options}</select></div>
<div><label>状态</label><select name="status"><option value="">全部</option>{statuses}</select></div>
<button type="submit">筛选</button>
</form>
<div class="tools">
<a href="{esc(csv_url)}">导出对账 CSV</a>
<a href="{esc(reconcile_url)}">补录昨日微信账单</a>
<a href="{esc(verify_url)}">核对前 20 笔微信状态</a>
<a href="{esc(status_url)}">连通状态</a>
</div>
<table><thead><tr>
<th>创建</th><th>支付</th><th>订单号</th><th>商品</th><th>订单金额</th><th>实付</th>
<th>状态</th><th>微信交易号</th><th>授权码</th><th>来源</th>
</tr></thead><tbody>{body}</tbody></table>
<p class="muted">授权码是访问凭证，本页与 CSV 仅限管理员使用；请勿转发。</p>
</body></html>"""
    return HTMLResponse(page)