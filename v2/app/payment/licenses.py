"""采集表授权码：无状态签发与校验。

设计目标（适配 Render 免费层）：
- 不依赖数据库，订单记录丢失也能凭订单号重新查单取码；
- 校验不联网，下载接口可直接同步验签；
- 码内嵌订单号，便于人工核对与按订单退款。

码格式：ESG-<20位订单号>-<12位签名>
订单号：ES + 商品码(2位大写字母) + yyMMddHHmm + 6位随机 Base32
签名：HMAC-SHA256(LICENSE_SECRET 或 WX_API_V2_KEY, 订单号) 的 Base32 前 12 位

另支持 TEMPLATE_LICENSE_CODES（逗号/空格分隔）作为手动码：线下转账、
老用户过渡、企业批量授权时人工下发，同样按「template」授权处理。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime

PREFIX = "ESG-"
ORDER_RE = re.compile(r"^ES[A-Z]{2}\d{10}[A-Z2-7]{6}$")
CODE_RE = re.compile(r"^ESG-(ES[A-Z]{2}\d{10}[A-Z2-7]{6})-([A-Z2-7]{12})$")


def _secret() -> str:
    """签名密钥：优先 LICENSE_SECRET，其次复用微信 APIv2 密钥。为空则不签发票。"""
    return (os.environ.get("LICENSE_SECRET") or os.environ.get("WX_API_V2_KEY") or "").strip()


def new_order_no(order_code: str) -> str:
    """20 位订单号；后 6 位随机保证不可猜测（约 30 bit/分钟/商品）。"""
    stamp = datetime.now().strftime("%y%m%d%H%M")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    rand = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"ES{order_code.upper()}{stamp}{rand}"


def issue(order_no: str) -> str:
    """按订单号签发授权码；未配置密钥时返回空串。"""
    secret = _secret()
    if not secret or not ORDER_RE.match(order_no):
        return ""
    digest = hmac.new(secret.encode(), order_no.encode(), hashlib.sha256).digest()
    sig = base64.b32encode(digest).decode()[:12]
    return f"{PREFIX}{order_no}-{sig}"


def verify(code: str) -> str:
    """校验授权码；通过返回 'template'，否则返回空串。"""
    code = (code or "").strip()
    if not code:
        return ""
    if code.upper() in _override_codes():
        return "template"

    secret = _secret()
    if not secret:
        return ""
    m = CODE_RE.match(code.upper())
    if not m:
        return ""
    order_no, sig = m.group(1), m.group(2)
    expected = base64.b32encode(
        hmac.new(secret.encode(), order_no.encode(), hashlib.sha256).digest()
    ).decode()[:12]
    return "template" if hmac.compare_digest(sig, expected) else ""


def _override_codes() -> set[str]:
    raw = os.environ.get("TEMPLATE_LICENSE_CODES", "")
    return {c.strip().upper() for c in re.split(r"[,;\s]+", raw) if c.strip()}