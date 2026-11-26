"""在主站域名下挂载「学习中心 / 每日拆解」栏目。

渲染逻辑与内容读取直接复用 V2 的 site 模块（纯标准库），因此不需要跨服务
转发、不依赖第二个 Render 实例。挂载点：

    /learn              学习中心
    /learn/<slug>       单条学习路径
    /learn/site.css     学习页样式（主站 /ui 已被工具界面占用）
    /daily              每日拆解
    /daily/<slug>       301 到 /daily#<slug>
    /daily/feed.xml     每日拆解 RSS
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

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
    """旧的单期链接统一定位到单页里的对应一期。"""
    base = _site_base(request)
    if site_content.daily_episode(slug) is None:
        return HTMLResponse(site_render.render_not_found(base), status_code=404)
    return RedirectResponse(url=f"/daily#{slug}", status_code=301)


def learn_paths() -> list[str]:
    """供主站 sitemap 合并使用的栏目路径。"""
    paths = ["/learn", "/daily"]
    paths += [f"/learn/{p['slug']}" for p in site_content.learn_paths()]
    return paths
