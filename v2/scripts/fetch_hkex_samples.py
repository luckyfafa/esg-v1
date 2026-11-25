"""下载不同行业港股上市公司的 ESG 报告，作为润色版的写作样本。

数据源：港交所披露易 hkexnews（公开、免费、无需认证）。
与 V1 的 app/collectors.py 互不依赖：V1 已冻结，这里按 V2 需要单独实现。

用法：
    python -m v2.scripts.fetch_hkex_samples            # 下载缺失样本
    python -m v2.scripts.fetch_hkex_samples --force    # 重新下载全部

产物：
    data/hkex_reports/{代码}_{发布日期}.pdf
    data/hkex_reports/_index.json   行业 → 样本文件映射与来源链接
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "hkex_reports"
INDEX_FILE = OUT_DIR / "_index.json"

PREFIX_URL = "https://www1.hkexnews.hk/search/prefix.do"
SEARCH_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www1.hkexnews.hk/listedco/listconews/advancedsearch/search_active_main.aspx",
    "Origin": "https://www1.hkexnews.hk",
    "Accept-Language": "en-US,en;q=0.9",
}

# 行业 key → (股票代码, 公司中文名, 选取理由)
TARGETS: list[tuple[str, str, str, str]] = [
    ("banking", "00005", "汇丰控股", "全球化银行，TCFD/ISSB S2 披露完整"),
    ("technology", "00700", "腾讯控股", "互联网平台，范围三与碳中和路径清晰"),
    ("utilities", "00002", "中电控股", "发电排放与能源转型披露标杆"),
    ("real_estate", "00016", "新鸿基地产", "绿色建筑与资产层面环境数据详尽"),
    ("retail", "09633", "农夫山泉", "快消零售，包装与水资源议题"),
    ("logistics", "01919", "中远海控", "航运物流，运输排放与气候情景"),
    ("energy", "00883", "中国海洋石油", "上游油气，甲烷与气候风险披露"),
    ("insurance", "01299", "友邦保险", "保险业气候风险与负责任投资"),
    ("healthcare", "01093", "石药集团", "医药制造，EHS 与产品责任"),
    ("manufacturing", "00303", "伟易达集团", "电子制造，供应链与产品碳足迹"),
]

TITLE_KEYWORDS = (
    "esg", "sustainability", "csr", "environmental", "social",
    "governance", "climate", "環境", "社會", "管治", "可持續", "環境、社會",
)

logger = logging.getLogger("fetch_hkex")


def _rows(payload: dict) -> list[dict]:
    raw = payload.get("result")
    if raw is None:
        return []
    if isinstance(raw, str):
        return json.loads(raw) if raw else []
    return raw


def resolve_stock_id(code: str) -> int | None:
    """股票代码 → 港交所内部 stockId（比 stockId=-1 全量拉取稳定得多）。"""
    r = httpx.get(
        PREFIX_URL,
        params={"callback": "cb", "lang": "EN", "type": "A", "name": code, "market": "SEHK"},
        headers=HEADERS,
        timeout=30.0,
    )
    r.raise_for_status()
    m = re.search(r"\((\{.*\})\)", r.text, re.S)
    if not m:
        return None
    for item in json.loads(m.group(1)).get("stockInfo", []):
        if item.get("code") == code:
            return int(item["stockId"])
    return None


def search_reports(stock_id: int, years: int = 3) -> list[dict]:
    today = date.today()
    from_date = f"{today.year - years}0101"
    params = {
        "sortDir": "0",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": str(stock_id),
        "documentType": "-1",
        "fromDate": from_date,
        "toDate": today.strftime("%Y%m%d"),
        "title": "",
        "searchType": "1",
        "t1code": "40000",
        "t2Gcode": "-2",
        "t2code": "-2",
        "rowRange": "1-200",
    }
    r = httpx.get(SEARCH_URL, params=params, headers=HEADERS, timeout=90.0)
    r.raise_for_status()
    out = []
    for item in _rows(r.json()):
        title = item.get("TITLE") or ""
        if not any(k in title.lower() or k in title for k in TITLE_KEYWORDS):
            continue
        try:
            release = date(
                *reversed([int(x) for x in item.get("DATE_TIME", "").split()[0].split("/")])
            )
        except (ValueError, IndexError):
            release = today
        out.append({
            "title": title,
            "release": release,
            "url": f"https://www1.hkexnews.hk{item.get('FILE_LINK', '')}",
        })
    return out


def download(url: str, target: Path) -> int:
    tmp = target.with_suffix(".part")
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=180.0) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    tmp.replace(target)
    return target.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略已有索引，重新下载")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    if INDEX_FILE.exists() and not args.force:
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))

    for industry, code, name_cn, reason in TARGETS:
        if industry in index and Path(index[industry]["file"]).exists() and not args.force:
            logger.info("%s %s 已存在，跳过", code, name_cn)
            continue
        try:
            sid = resolve_stock_id(code)
            if sid is None:
                logger.warning("%s 未找到 stockId，跳过", code)
                continue
            reports = search_reports(sid)
            if not reports:
                logger.warning("%s 未检索到 ESG 报告", code)
                continue
            latest = max(reports, key=lambda x: x["release"])
            target = OUT_DIR / f"{code}_{latest['release'].isoformat()}.pdf"
            size = target.stat().st_size if target.exists() else download(latest["url"], target)
            index[industry] = {
                "industry": industry,
                "code": code,
                "name_cn": name_cn,
                "reason": reason,
                "title": latest["title"],
                "release_date": latest["release"].isoformat(),
                "file": str(target.relative_to(ROOT)).replace("\\", "/"),
                "url": latest["url"],
                "bytes": size,
            }
            logger.info("%s %s → %s (%.1f MB)", code, name_cn, target.name, size / 1024 / 1024)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s 下载失败：%s", code, e)

    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("索引已写入 %s（%d 个行业样本）", INDEX_FILE, len(index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
