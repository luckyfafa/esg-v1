"""报告润色版接口：V1 主站与 V2 共用同一套路由与授权校验。"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from ..payment import licenses as payment_licenses

router = APIRouter(prefix="/api/polish", tags=["polish"])

ROOT = Path(__file__).resolve().parents[3]
DEMO_WORKBOOK = ROOT / "docs" / "非上市企业ESG数据采集表-填写示例.xlsx"
POLISH_DIR = settings.data_dir / "reports" / "polished"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MEDIA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _payload(result) -> dict:
    data = result.as_dict(include_text=False)
    data["downloads"] = {
        k: f"/api/polish/file/{Path(v).name}" for k, v in result.files.items()
    }
    return data


def _allowed(code: str) -> bool:
    if payment_licenses.verify(code) == "polish":
        return True
    admin = os.environ.get("ADMIN_TOKEN", "")
    return bool(admin) and code == admin


@router.get("/samples")
def polish_samples():
    """已下载的港股 ESG 报告样本（用于写作风格对标）。"""
    index = ROOT / "data" / "hkex_reports" / "_index.json"
    if not index.exists():
        return {"samples": [], "note": "样本尚未下载，请运行 python -m v2.scripts.fetch_hkex_samples"}
    import json

    samples = json.loads(index.read_text(encoding="utf-8"))
    return {
        "count": len(samples),
        "samples": [
            {k: v for k, v in s.items() if k != "file"} | {"file": Path(s["file"]).name}
            for s in samples.values()
        ],
    }


@router.post("/report")
async def polish_report(
    file: UploadFile = File(..., description="非上市企业 ESG 数据采集表（.xlsx）"),
    industry: str | None = Query(None, description="行业覆盖：manufacturing/retail/technology/..."),
    code: str = Query("", description="报告润色版授权码（或管理员口令）"),
):
    """把采集表生成「底稿 + 上市公司风格润色版」（需「报告润色版」授权）。"""
    if not _allowed(code):
        raise HTTPException(
            status_code=402,
            detail={
                "code": "POLISH_LOCKED",
                "message": "润色版需「报告润色版」授权码；采集表码与报告码不可通用。",
                "unlock_url": "/#pricing",
            },
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm 格式")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    from . import polish_bytes

    try:
        result = polish_bytes(content, file.filename or "upload.xlsx", industry=industry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logging.exception("润色版生成失败")
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    return _payload(result)


@router.post("/sample")
def polish_demo(industry: str | None = Query(None)):
    """用内置填写示例生成一套润色版，免费查看效果。"""
    if not DEMO_WORKBOOK.exists():
        raise HTTPException(status_code=404, detail="缺少示例采集表 docs/非上市企业ESG数据采集表-填写示例.xlsx")
    from . import polish_excel

    try:
        result = polish_excel(DEMO_WORKBOOK, industry=industry)
    except Exception as e:  # noqa: BLE001
        logging.exception("示例润色版生成失败")
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    return _payload(result)


@router.get("/file/{name}")
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