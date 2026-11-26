"""IndexNow 提交：内容更新后主动通知 Bing 等支持 IndexNow 的搜索引擎。

协议要点：
    - 站点根路径必须有与 key 同名的 txt 文件，内容就是 key 本身
    - 提交 POST JSON 到 https://api.indexnow.org/indexnow
    - 端点返回 200/202 视为受理；失败只记录，不影响主流程
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

HOST = "esg-v1-0uyh.onrender.com"
SITE_URL = f"https://{HOST}"
KEY = "4464cc2ae719a5be8be9d9429eede453"
KEY_LOCATION = f"{SITE_URL}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
TIMEOUT = 15


def episode_url(slug: str) -> str:
    return f"{SITE_URL}/daily/{slug}"


def site_urls() -> list[str]:
    """当前站点全部可索引 URL，与 sitemap 条目保持一致。"""
    from .site import content

    latest = content.site_stats()["latest"]
    urls = [
        f"{SITE_URL}/",
        f"{SITE_URL}/tools/collection-template",
        f"{SITE_URL}/tools/carbon-guide",
        f"{SITE_URL}/learn",
        f"{SITE_URL}/daily",
    ]
    urls += [f"{SITE_URL}/learn/{p['slug']}" for p in content.learn_paths()]
    urls += [episode_url(ep["slug"]) for ep in content.daily_episodes()]
    return urls


def submit(urls: list[str]) -> tuple[bool, int, str]:
    """提交一批 URL，返回 (是否受理, HTTP 状态码, 失败详情)。"""
    if not urls:
        return True, 0, "无 URL 需要提交"
    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": urls,
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return True, response.status, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return False, exc.code, detail
    except Exception as exc:  # noqa: BLE001 - 网络异常一律只记录
        return False, 0, f"{type(exc).__name__}: {exc}"


def submit_safely(urls: list[str], label: str = "") -> None:
    """提交失败只打印日志，不抛出，不影响每日发布主流程。"""
    ok, status, detail = submit(urls)
    tag = f"[INDEXNOW] {label}" if label else "[INDEXNOW]"
    if ok:
        print(f"{tag} 已提交 {len(urls)} 条，HTTP {status}")
    else:
        print(f"{tag} 提交失败（HTTP {status or '无响应'}）：{detail}", flush=True)