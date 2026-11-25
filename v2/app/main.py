"""V2 独立入口：碳核算 + 港交所披露合规检查。

启动（与 V1 互不影响，V1 仍用 8000）：
    python -m uvicorn v2.app.main:app --reload --port 8020

界面：http://127.0.0.1:8020/
文档：http://127.0.0.1:8020/docs
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .rules import applicable_rules, load_ruleset
from .site import content as site_content
from .site import render as site_render
from .v2_data import load_v2_excel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(
    title="HK ESG Report System V2",
    version=__version__,
    description="港交所附录C2 披露检查 + 碳核算（V2 骨架，与 V1 隔离）",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")

# V2 规划模块；skeleton 阶段如实标注状态，避免被误认为已可用
MODULES = [
    {"key": "rules", "name": "附录C2 规则库", "status": "ready"},
    {"key": "dataset", "name": "V2 采集表与数据模型", "status": "ready"},
    {"key": "polish", "name": "上市公司风格报告润色", "status": "ready"},
    {"key": "carbon", "name": "碳核算引擎", "status": "planned"},
    {"key": "climate", "name": "气候披露四支柱", "status": "planned"},
    {"key": "compliance", "name": "合规检查", "status": "planned"},
    {"key": "review", "name": "复核与签批", "status": "planned"},
    {"key": "export", "name": "PDF/Word 导出", "status": "planned"},
]

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_FILE = Path(__file__).resolve().parent.parent / "templates" / "非上市企业ESG数据采集表-v2.xlsx"
SAMPLE_INDEX = ROOT / "data" / "hkex_reports" / "_index.json"
DEMO_WORKBOOK = ROOT / "docs" / "非上市企业ESG数据采集表-填写示例.xlsx"
POLISH_DIR = settings.data_dir / "reports" / "polished"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

MEDIA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _polish_payload(result) -> dict:
    """统一润色接口返回：元数据 + 下载地址。"""
    data = result.as_dict(include_text=False)
    data["downloads"] = {
        k: f"/api/polish/file/{Path(v).name}" for k, v in result.files.items()
    }
    return data


def _site_base(request: Request) -> str:
    """对外地址：优先 V2_SITE_URL，其次代理头，最后按请求推导。

    Render 等反代会把外部域名放在 X-Forwarded-Host / X-Forwarded-Proto，
    直接读 request.base_url 容易拿到 http 或内网地址，导致 canonical、
    sitemap 与 RSS 里的链接不对。
    """
    if settings.site_url:
        return settings.site_url.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        proto = request.headers.get("x-forwarded-proto", "https").split(",")[0].strip()
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


@app.get("/learn", include_in_schema=False)
def learn_home(request: Request):
    """学习中心：五条路径。"""
    return HTMLResponse(site_render.render_learn_index(_site_base(request)))


@app.get("/learn/{slug}", include_in_schema=False)
def learn_detail(slug: str, request: Request):
    """单条学习路径。"""
    base = _site_base(request)
    path = site_content.learn_path(slug)
    if path is None:
        return HTMLResponse(site_render.render_not_found(base), status_code=404)
    return HTMLResponse(site_render.render_learn_detail(path, base))


@app.get("/daily/feed.xml", include_in_schema=False)
def daily_feed(request: Request):
    """每日拆解 RSS 2.0。"""
    return Response(
        site_render.render_feed(_site_base(request)),
        media_type="application/rss+xml; charset=utf-8",
    )


@app.get("/daily", include_in_schema=False)
def daily_home(request: Request):
    """每日拆解列表。"""
    return HTMLResponse(site_render.render_daily_index(_site_base(request)))


@app.get("/daily/{slug}", include_in_schema=False)
def daily_detail(slug: str, request: Request):
    """旧文章链接统一定位到单页里的对应一期。"""
    base = _site_base(request)
    if site_content.daily_episode(slug) is None:
        return HTMLResponse(site_render.render_not_found(base), status_code=404)
    return RedirectResponse(url=f"/daily#{slug}", status_code=301)


@app.get("/api/daily/latest", tags=["site"])
def daily_latest(limit: int = Query(3, ge=1, le=20)):
    """V2 首页用的最新拆解摘要；内容直接读 daily 目录，无需重启。"""
    episodes = site_content.daily_episodes()[:limit]
    return {
        "count": len(episodes),
        "items": [
            {
                "slug": ep["slug"],
                "date": ep["date"],
                "code": ep["code"],
                "company": ep["company"],
                "industry": ep["industry"],
                "angle": ep["angle"],
                "hook": ep["hook"],
                "msci": ep.get("rating_meta", {}).get("msci"),
                "url": f"/daily#{ep['slug']}",
            }
            for ep in episodes
        ],
    }


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request):
    """V2 主站、学习中心与每日拆解的站点地图。"""
    return Response(
        site_render.render_sitemap(_site_base(request)),
        media_type="application/xml; charset=utf-8",
    )


@app.get("/robots.txt", include_in_schema=False)
def robots_txt(request: Request):
    return Response(
        site_render.render_robots(_site_base(request)),
        media_type="text/plain; charset=utf-8",
    )


@app.get("/", include_in_schema=False)
def index():
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="V2 界面未部署（缺少 v2/web/index.html）")
    return FileResponse(page)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": __version__, "stage": "skeleton"}


@app.get("/api/meta", tags=["system"])
def meta():
    """V2 版本与模块就绪情况。"""
    ruleset = load_ruleset()
    return {
        "version": __version__,
        "stage": "skeleton",
        "v1_frozen": True,
        "data_dir": str(settings.data_dir),
        "rules": {"standard": ruleset.standard, "version": ruleset.version},
        "modules": MODULES,
    }


@app.get("/api/rules", tags=["rules"])
def list_rules(part: str | None = Query(None, description="筛选 B / C / D")):
    """列出规则库全部条文。"""
    rules = load_ruleset().rules
    if part:
        rules = [r for r in rules if r.part == part.upper()]
    return [r.model_dump() for r in rules]


@app.get("/api/rules/stats", tags=["rules"])
def rules_stats():
    """规则数量与状态分布。"""
    ruleset = load_ruleset()
    by_part: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in ruleset.rules:
        by_part[r.part] = by_part.get(r.part, 0) + 1
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "total": len(ruleset.rules),
        "by_part": by_part,
        "by_status": by_status,
        "version": ruleset.version,
        "coverage_note": ruleset.coverage_note,
    }


@app.get("/api/rules/applicable", tags=["rules"])
def rules_applicable(
    issuer_type: str = Query(..., description="main_board 或 gem"),
    fy: int = Query(..., description="财政年度起始年，如 2026"),
    is_largecap: bool = Query(False, description="是否为恒生综合大型股指数成分股"),
):
    """按发行人类型与财政年度返回适用规则及有效披露责任。"""
    try:
        rules = applicable_rules(issuer_type, fy, is_largecap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    counts: dict[str, int] = {}
    for r in rules:
        counts[r["effective_obligation"]] = counts.get(r["effective_obligation"], 0) + 1
    return {"issuer_type": issuer_type, "fy": fy, "is_largecap": is_largecap,
            "total": len(rules), "by_obligation": counts, "rules": rules}


@app.get("/api/template", tags=["dataset"])
def download_template():
    """下载 V2 采集表模板（含规则映射与碳活动表）。"""
    if not TEMPLATE_FILE.exists():
        raise HTTPException(status_code=404, detail="V2 模板未生成，请先运行 python -m v2.scripts.build_template")
    return FileResponse(
        TEMPLATE_FILE,
        filename="非上市企业ESG数据采集表-v2.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/dataset/validate", tags=["dataset"])
async def validate_dataset(file: UploadFile = File(..., description="填写后的 V2 采集表（.xlsx）")):
    """上传采集表并校验结构，返回读取结果与缺口摘要（不生成报告）。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm 格式")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    target = settings.data_dir / "uploads" / f"validate_{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    try:
        dataset = load_v2_excel(target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        target.unlink(missing_ok=True)

    return {
        "company": dataset.profile.company_name,
        "issuer_type": dataset.profile.issuer_type,
        "fy_start_year": dataset.profile.fy_start_year,
        "metrics": len(dataset.metrics),
        "disclosures": len(dataset.disclosures),
        "disclosure_counts": dataset.disclosure_counts(),
        "climate_items": len(dataset.climate),
        "targets": len(dataset.targets),
        "activities": len(dataset.activities),
        "factors_ready": sum(1 for f in dataset.factors if f.factor is not None),
        "evidence": len(dataset.evidence),
        "completeness": dataset.completeness_required(),
        "missing_required": dataset.missing_required()[:20],
        "warnings": dataset.warnings,
    }


@app.get("/api/polish/samples", tags=["polish"])
def polish_samples():
    """已下载的港股 ESG 报告样本（用于写作风格对标）。"""
    if not SAMPLE_INDEX.exists():
        return {"samples": [], "note": "样本尚未下载，请运行 python -m v2.scripts.fetch_hkex_samples"}
    import json

    samples = json.loads(SAMPLE_INDEX.read_text(encoding="utf-8"))
    return {
        "count": len(samples),
        "samples": [
            {k: v for k, v in s.items() if k != "file"} | {"file": Path(s["file"]).name}
            for s in samples.values()
        ],
    }


@app.post("/api/polish/report", tags=["polish"])
async def polish_report(
    file: UploadFile = File(..., description="非上市企业 ESG 数据采集表（.xlsx）"),
    industry: str | None = Query(None, description="行业覆盖：manufacturing/retail/technology/..." ),
):
    """把采集表生成「底稿 + 上市公司风格润色版」。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm 格式")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    from .polish import polish_bytes

    try:
        result = polish_bytes(content, file.filename or "upload.xlsx", industry=industry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logging.exception("润色版生成失败")
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    return _polish_payload(result)


@app.post("/api/polish/sample", tags=["polish"])
def polish_demo(industry: str | None = Query(None)):
    """用内置填写示例生成一套润色版，便于快速查看效果。"""
    if not DEMO_WORKBOOK.exists():
        raise HTTPException(status_code=404, detail="缺少示例采集表 docs/非上市企业ESG数据采集表-填写示例.xlsx")
    from .polish import polish_excel

    try:
        result = polish_excel(DEMO_WORKBOOK, industry=industry)
    except Exception as e:  # noqa: BLE001
        logging.exception("示例润色版生成失败")
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    return _polish_payload(result)


@app.get("/api/polish/file/{name}", tags=["polish"])
def polish_file(name: str):
    """下载润色版产物（仅允许 polished 目录下的文件名）。"""
    if Path(name).name != name:
        raise HTTPException(status_code=400, detail="非法文件名")
    target = POLISH_DIR / name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        target,
        filename=name,
        media_type=MEDIA_TYPES.get(target.suffix, "application/octet-stream"),
    )

