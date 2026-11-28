"""付费下载接口：下单、查单、微信回调。

挂载到主站（V1）后，回调地址即 https://<主站域名>/api/pay/notify。
授权不落库：查单确认 SUCCESS 后按订单号现签授权码，服务重启也不影响已付款用户。
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import catalog, licenses, wechatpay

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pay", tags=["pay"])

# 同一 IP 每分钟最多创建 20 笔订单 / 查询 60 次
_BUCKETS: dict[str, list[float]] = {}
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _client_ip(request: Request) -> str:
    """微信 V2 要求 IPv4；代理/测试环境拿不到合法 IPv4 时退化为 127.0.0.1。"""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "") or ""
    ip = ip.replace("::ffff:", "").strip()
    return ip if _IPV4.match(ip) else "127.0.0.1"


def _allow(key: str, limit: int, window: float = 60.0) -> bool:
    now = time.time()
    hits = [t for t in _BUCKETS.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _BUCKETS[key] = hits
        return False
    hits.append(now)
    _BUCKETS[key] = hits
    return True


def _expected_fee(raw: dict, product_id: str = "") -> int:
    """从回调/查单的 attach 里取订单实付金额（下单时写入），取不到再用商品表。"""
    attach = str(raw.get("attach") or "")
    if "|" in attach:
        _, _, fee = attach.partition("|")
        try:
            return int(fee)
        except ValueError:
            pass
    product = catalog.find(product_id) if product_id else None
    return product["priceFen"] if product else 0


def _order_payload(order_no: str, amount_fen: int, status: str, **extra) -> dict:
    product = catalog.find_by_code(order_no[2:4])
    data = {
        "orderNo": order_no,
        "status": status,
        "paid": status == "PAID",
        "amount": f"{amount_fen / 100:.2f}",
        "amountFen": amount_fen,
        "product": (
            {"id": product["id"], "name": product["name"], "desc": product["desc"]}
            if product
            else None
        ),
    }
    data.update(extra)
    return data


@router.get("/config")
def pay_config():
    """前端读取：是否启用在线支付、商品与价格（价格的唯一来源）。"""
    return {
        "pay_enabled": wechatpay.settings.enabled,
        "currency": "CNY",
        "products": catalog.public_items(),
        "notice": "付款后页面自动确认并发授权码；未开通在线支付时请联系人工发码。",
    }


@router.post("/order")
def create_order(payload: dict, request: Request):
    """创建 Native 扫码订单；金额只按商品表计算，前端传金额一律忽略。"""
    if not wechatpay.settings.enabled:
        raise HTTPException(
            status_code=503,
            detail={"code": "PAY_DISABLED", "message": "在线支付未开通，请联系人工获取授权码。"},
        )
    if not _allow(f"order:{_client_ip(request)}", 20):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")

    product = catalog.find(str(payload.get("productId") or ""))
    if product is None:
        raise HTTPException(status_code=400, detail="商品不存在或已下架，请刷新页面重试")

    order_no = licenses.new_order_no(product["orderCode"])
    result = wechatpay.unified_order(
        out_trade_no=order_no,
        total_fee=product["priceFen"],
        body=product["name"],
        spbill_create_ip=_client_ip(request),
        attach=f"{product['id']}|{product['priceFen']}",
    )
    if not result["ok"]:
        log.warning(
            "[pay] 下单失败 %s %s %s",
            order_no,
            result["raw"].get("err_code"),
            result["raw"].get("return_msg") or result["raw"].get("err_code_des"),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": result["raw"].get("err_code") or "ORDER_FAILED",
                "message": result["message"] or "下单失败，请稍后重试",
            },
        )

    log.info("[pay] 创建订单 %s ¥%.2f", order_no, product["priceFen"] / 100)
    return _order_payload(
        order_no,
        product["priceFen"],
        "PENDING",
        codeUrl=result["code_url"],
        qrDataUrl=wechatpay.qr_data_url(result["code_url"]),
    )


@router.get("/order/{order_no}")
def order_status(order_no: str, request: Request):
    """查单：向微信二次确认，支付成功才现签授权码。"""
    order_no = order_no.strip().upper()
    if not licenses.ORDER_RE.match(order_no):
        raise HTTPException(status_code=404, detail="订单不存在")
    if not _allow(f"query:{_client_ip(request)}", 60):
        raise HTTPException(status_code=429, detail="查询过于频繁，请稍后再试")

    product = catalog.find_by_code(order_no[2:4])
    amount_fen = product["priceFen"] if product else 0

    if not wechatpay.settings.enabled:
        return _order_payload(order_no, amount_fen, "PENDING")

    raw = wechatpay.order_query(order_no)
    if raw.get("return_code") != "SUCCESS" or raw.get("result_code") != "SUCCESS":
        # 用户还未扫码时微信返回 ORDERNOTEXIST，按待支付处理
        return _order_payload(order_no, amount_fen, "PENDING")

    state = raw.get("trade_state", "")
    status_map = {
        "SUCCESS": "PAID",
        "REFUND": "REFUNDED",
        "NOTPAY": "PENDING",
        "USERPAYING": "PENDING",
        "CLOSED": "CLOSED",
        "REVOKED": "CLOSED",
        "PAYERROR": "FAILED",
    }
    status = status_map.get(state, "PENDING")

    if status == "PAID":
        expected = _expected_fee(raw, product["id"] if product else "")
        try:
            paid_fee = int(raw.get("total_fee") or 0)
        except ValueError:
            paid_fee = 0
        if expected and paid_fee != expected:
            log.warning("[pay] 金额不一致 %s 应付 %s 实付 %s", order_no, expected, paid_fee)
            return _order_payload(order_no, paid_fee, "AMOUNT_MISMATCH")
        code = licenses.issue(order_no)
        if not code:
            log.error("[pay] 已付款但未配置 LICENSE_SECRET，无法签发授权码 %s", order_no)
            raise HTTPException(
                status_code=500,
                detail={"code": "LICENSE_NOT_CONFIGURED", "message": "已收到付款，但授权签名未配置，请联系人工发码。"},
            )
        return _order_payload(
            order_no,
            paid_fee,
            "PAID",
            transactionId=raw.get("transaction_id") or "",
            paidAt=raw.get("time_end") or "",
            license=code,
        )

    return _order_payload(order_no, amount_fen, status)


@router.post("/notify")
async def wechat_notify(request: Request):
    """微信支付结果回调：验签 + 金额核对，仅记录，不依赖它发货。

    发货靠前端轮询查单（Render 免费层会睡眠，回调可能冷启动超时）。
    """
    body = (await request.body()).decode("utf-8", "ignore")
    data = wechatpay.from_xml(body)

    ok_xml = Response(
        "<xml><return_code><![CDATA[SUCCESS]]></return_code>"
        "<return_msg><![CDATA[OK]]></return_msg></xml>",
        media_type="text/xml",
    )
    fail_xml = Response(
        "<xml><return_code><![CDATA[FAIL]]></return_code>"
        "<return_msg><![CDATA[FAIL]]></return_msg></xml>",
        media_type="text/xml",
    )

    if not wechatpay.settings.enabled:
        return fail_xml

    verified, reason = wechatpay.verify_notify(data)
    if not verified:
        log.warning("[pay] 回调验签失败：%s", reason)
        return fail_xml

    if data.get("return_code") != "SUCCESS" or data.get("result_code") != "SUCCESS":
        return ok_xml

    order_no = str(data.get("out_trade_no") or "")
    expected = _expected_fee(data)
    try:
        paid_fee = int(data.get("total_fee") or 0)
    except ValueError:
        paid_fee = 0
    if expected and paid_fee != expected:
        log.warning("[pay] 回调金额不一致 %s 应付 %s 实付 %s", order_no, expected, paid_fee)
        return ok_xml

    log.info("[pay] 回调确认支付成功 %s ¥%.2f", order_no, paid_fee / 100)
    return ok_xml


@router.get("/license")
def check_license(code: str = ""):
    """独立校验授权码（不下载，仅返回是否有效）。"""
    plan = licenses.verify(code)
    return JSONResponse({"valid": bool(plan), "plan": plan})