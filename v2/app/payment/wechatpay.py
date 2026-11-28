"""微信支付 V2（Native 扫码）最小客户端。

移植自已跑通的 Node 版本 wechat-pay-site：签名规则、统一下单、查单、
回调验签保持一致，便于用同一套商户参数迁移。密钥只在服务端使用。

注意：微信支付 V2 使用 XML，签名串为「非空参数按字典序拼接 + &key=API密钥」。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import logging
import secrets
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

API_BASE = "https://api.mch.weixin.qq.com"
DEFAULT_SITE = "https://esg-v1-0uyh.onrender.com"

log = logging.getLogger(__name__)


class PaySettings(BaseSettings):
    """商户参数，全部从环境变量读取（不写进仓库）。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    wx_appid: str = Field("", validation_alias="WX_APPID")
    wx_mchid: str = Field("", validation_alias="WX_MCHID")
    wx_api_v2_key: str = Field("", validation_alias="WX_API_V2_KEY")
    wx_sign_type: str = Field("MD5", validation_alias="WX_SIGN_TYPE")
    pay_public_base_url: str = Field(DEFAULT_SITE, validation_alias="PAY_PUBLIC_BASE_URL")

    @property
    def enabled(self) -> bool:
        """三个必填参数齐全才启用在线支付；否则前端回落到人工发码。"""
        return bool(self.wx_appid and self.wx_mchid and self.wx_api_v2_key)

    @property
    def sign_type(self) -> str:
        return "HMAC-SHA256" if self.wx_sign_type.upper().startswith("HMAC") else "MD5"

    @property
    def notify_url(self) -> str:
        return f"{self.pay_public_base_url.rstrip('/')}/api/pay/notify"


settings = PaySettings()


def new_nonce(length: int = 32) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_string_a(params: dict[str, Any]) -> str:
    return "&".join(
        f"{k}={params[k]}"
        for k in sorted(params)
        if k != "sign" and params[k] is not None and str(params[k]) != ""
    )


def sign(params: dict[str, Any], api_key: str, sign_type: str = "MD5") -> str:
    temp = f"{build_string_a(params)}&key={api_key}"
    if sign_type == "HMAC-SHA256":
        return hmac.new(api_key.encode(), temp.encode(), hashlib.sha256).hexdigest().upper()
    return hashlib.md5(temp.encode()).hexdigest().upper()


def verify_sign(params: dict[str, Any], api_key: str) -> bool:
    received = str(params.get("sign") or "").upper()
    if not received:
        return False
    st = "HMAC-SHA256" if params.get("sign_type") == "HMAC-SHA256" else "MD5"
    expected = sign(params, api_key, st)
    return hmac.compare_digest(received, expected)


def _cdata(value: Any) -> str:
    return str(value).replace("]]>", "]]]]><![CDATA[>")


def to_xml(params: dict[str, Any]) -> str:
    parts = ["<xml>"]
    for k, v in params.items():
        if v is None:
            continue
        parts.append(f"<{k}><![CDATA[{_cdata(v)}]]></{k}>")
    parts.append("</xml>")
    return "".join(parts)


def from_xml(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for node in root:
        out[node.tag] = node.text or ""
    return out


def _signed(params: dict[str, Any]) -> dict[str, Any]:
    data = {
        "appid": settings.wx_appid,
        "mch_id": settings.wx_mchid,
        "nonce_str": new_nonce(),
        **params,
    }
    if settings.sign_type == "HMAC-SHA256":
        data["sign_type"] = "HMAC-SHA256"
    data["sign"] = sign(data, settings.wx_api_v2_key, settings.sign_type)
    return data


def _post(path: str, params: dict[str, Any], timeout: float = 10.0) -> dict[str, str]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            res = client.post(
                f"{API_BASE}{path}",
                content=to_xml(_signed(params)).encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "Accept": "text/xml"},
            )
            res.raise_for_status()
            return from_xml(res.text)
    except Exception as e:  # noqa: BLE001
        log.warning("微信支付接口 %s 调用失败: %s", path, e)
        return {"return_code": "FAIL", "return_msg": f"请求微信支付失败: {type(e).__name__}"}


def _post_raw(path: str, params: dict[str, Any], timeout: float = 30.0) -> tuple[bool, str]:
    """下载账单等可能返回 CSV 的接口；成功返回 (True, 文本)，失败返回 (False, 错误)。"""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            res = client.post(
                f"{API_BASE}{path}",
                content=to_xml(_signed(params)).encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "Accept": "*/*"},
            )
            res.raise_for_status()
            return True, res.text
    except Exception as e:  # noqa: BLE001
        log.warning("微信支付接口 %s 调用失败: %s", path, e)
        return False, f"请求微信支付失败: {type(e).__name__}"


def unified_order(
    *,
    out_trade_no: str,
    total_fee: int,
    body: str,
    spbill_create_ip: str = "127.0.0.1",
    attach: str = "",
) -> dict[str, Any]:
    """Native 统一下单，返回 {ok, code_url, raw, message}。"""
    raw = _post(
        "/pay/unifiedorder",
        {
            "body": body[:128],
            "out_trade_no": out_trade_no,
            "total_fee": int(total_fee),
            "spbill_create_ip": spbill_create_ip or "127.0.0.1",
            "notify_url": settings.notify_url,
            "trade_type": "NATIVE",
            "attach": attach,
        },
    )
    if raw.get("return_code") != "SUCCESS":
        return {"ok": False, "raw": raw, "message": raw.get("return_msg") or "通信失败"}
    if raw.get("result_code") != "SUCCESS":
        return {
            "ok": False,
            "raw": raw,
            "message": raw.get("err_code_des") or raw.get("err_code") or "下单失败",
        }
    return {"ok": True, "code_url": raw.get("code_url", ""), "raw": raw, "message": ""}


def order_query(out_trade_no: str) -> dict[str, str]:
    """主动查单；返回微信原始字段（trade_state / total_fee / transaction_id 等）。"""
    return _post("/pay/orderquery", {"out_trade_no": out_trade_no})


def close_order(out_trade_no: str) -> dict[str, str]:
    """关闭未支付订单（联调自检后清理测试单，避免留下悬空二维码）。"""
    return _post("/pay/closeorder", {"out_trade_no": out_trade_no})


def yuan_to_fen(value: Any) -> int:
    """账单金额是元字符串；用 Decimal 避免 0.29 * 100 的浮点误差。"""
    try:
        return int((Decimal(str(value).strip()) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, AttributeError):
        return 0


def parse_bill_csv(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """解析微信 V2 交易账单 CSV（含表头/汇总行的反引号前缀）。

    返回 (数据行, 汇总行原文)。数据行以中文列名为键，金额仍为元字符串。
    """
    import csv

    lines = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return [], []
    header = [h.strip().lstrip("\ufeff") for h in next(csv.reader([lines[0].lstrip("\ufeff`")]))]
    rows: list[dict[str, str]] = []
    summary: list[str] = []
    for line in lines[1:]:
        clean = line.lstrip("`").strip()
        if clean.startswith("总"):
            summary.append(clean)
            continue
        values = next(csv.reader([clean]))
        if len(values) != len(header):
            continue
        rows.append({k: v.strip() for k, v in zip(header, values)})
    return rows, summary


def download_bill(bill_date: str, bill_type: str = "ALL") -> dict[str, Any]:
    """下载微信交易账单（T+1，bill_date 格式 yyyyMMdd）。

    成功返回 {ok: True, rows, summary, message}；
    无账单/权限不足等返回 {ok: False, message, raw}。
    """
    ok, text = _post_raw("/pay/downloadbill", {"bill_date": bill_date, "bill_type": bill_type})
    if not ok:
        return {"ok": False, "rows": [], "summary": [], "message": text[:200], "raw": {}}
    if text.lstrip().startswith("<"):
        data = from_xml(text)
        return {
            "ok": False,
            "rows": [],
            "summary": [],
            "message": data.get("err_code_des") or data.get("return_msg") or "账单下载失败",
            "raw": data,
        }
    rows, summary = parse_bill_csv(text)
    return {"ok": True, "rows": rows, "summary": summary, "message": "", "raw": {}}


def verify_notify(params: dict[str, str]) -> tuple[bool, str]:
    """回调验签：签名、appid、mch_id 三项都通过才认。"""
    if not verify_sign(params, settings.wx_api_v2_key):
        return False, "签名不正确"
    if params.get("appid") and params["appid"] != settings.wx_appid:
        return False, "appid 不匹配"
    if params.get("mch_id") and params["mch_id"] != settings.wx_mchid:
        return False, "mch_id 不匹配"
    return True, ""


def qr_data_url(text: str) -> str:
    """把支付链接转成可直接放进 <img src> 的 SVG data URL。

    qrcode 可选依赖：缺失时返回空串，前端会提示手动打开链接。
    """
    if not text:
        return ""
    try:
        import base64

        import qrcode
        import qrcode.image.svg

        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:  # noqa: BLE001
        log.warning("二维码生成失败: %s", e)
        return ""