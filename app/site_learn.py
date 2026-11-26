"""在主站域名下挂载「学习中心 / 每日拆解」栏目。

渲染逻辑与内容读取直接复用 V2 的 site 模块（纯标准库），因此不需要跨服务
转发、不依赖第二个 Render 实例。挂载点：

    /learn              学习中心
    /learn/<slug>       单条学习路径
    /learn/site.css     学习页样式（主站 /ui 已被工具界面占用）
    /daily              每日拆解索引（全部期次）
    /daily/<slug>       单期独立页；旧锚点 /daily#<slug> 由索引页脚本转到该地址
    /daily/feed.xml     每日拆解 RSS
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from v2.app.site import content as site_content
from v2.app.site import render as site_render

CSS_FILE = Path(__file__).resolve().parent.parent / "v2" / "web" / "site.css"
CSS_VER = "20261126"

# 学习页走主站域名时，样式表落在 /learn 下，版本号与资产一致
site_render.set_css_href(f"/learn/site.css?v={CSS_VER}")

router = APIRouter()


def _site_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/learn/site.css", include_in_schema=False)
def learn_css():
    if not CSS_FILE.exists():
        return Response(status_code=404)
    return FileResponse(CSS_FILE, media_type="text/css; charset=utf-8")


@router.get("/learn", include_in_schema=False)
def learn_home(request: Request):
    """学习中心：五条路径。"""
    return HTMLResponse(site_render.render_learn_index(_site_base(request)))


@router.get("/learn/{slug}", include_in_schema=False)
def learn_detail(slug: str, request: Request):
    """单条学习路径。"""
    base = _site_base(request)
    path = site_content.learn_path(slug)
    if path is None:
        return HTMLResponse(site_render.render_not_found(base), status_code=404)
    return HTMLResponse(site_render.render_learn_detail(path, base))


@router.get("/daily/feed.xml", include_in_schema=False)
def daily_feed(request: Request):
    """每日拆解 RSS 2.0（绝对地址取自主站域名）。"""
    return Response(
        site_render.render_feed(_site_base(request)),
        media_type="application/rss+xml; charset=utf-8",
    )


@router.get("/daily", include_in_schema=False)
def daily_home(request: Request):
    """每日拆解列表。"""
    return HTMLResponse(site_render.render_daily_index(_site_base(request)))


@router.get("/daily/{slug}", include_in_schema=False)
def daily_detail(slug: str, request: Request):
    """单期独立页；旧锚点 /daily#<slug> 由索引页脚本转到同一地址。"""
    base = _site_base(request)
    ep = site_content.daily_episode(slug)
    if ep is None:
        return HTMLResponse(site_render.render_not_found(base), status_code=404)
    return HTMLResponse(site_render.render_daily_detail(ep, base))


def sitemap_entries() -> list[tuple[str, str, str, str]]:
    """sitemap 条目：(路径, lastmod, changefreq, priority)。

    lastmod 只用能从内容里确定的日期；工具落地页没有可靠改动日期，留空不写。
    """
    latest = site_content.site_stats()["latest"]
    entries: list[tuple[str, str, str, str]] = [
        ("/", latest, "weekly", "1.0"),
        ("/tools/collection-template", "", "monthly", "0.8"),
        ("/tools/carbon-guide", "", "monthly", "0.8"),
        ("/learn", latest, "weekly", "0.9"),
        ("/daily", latest, "daily", "0.9"),
    ]
    entries += [(f"/learn/{p['slug']}", latest, "monthly", "0.8") for p in site_content.learn_paths()]
    entries += [
        (f"/daily/{ep['slug']}", ep["date"], "monthly", "0.7")
        for ep in site_content.daily_episodes()
    ]
    return entries
