"""港交所公开数据源：证券名单、公告检索与报告下载。

全部数据来自港交所公开页面，无需账号或 token：
    证券名单  https://www.hkex.com.hk/chi/services/trading/securities/securitieslists/ListOfSecurities_c.xlsx
    公告检索  https://www1.hkexnews.hk/search/titleSearchServlet.do
    股票检索  https://www1.hkexnews.hk/search/prefix.do

只做检索与下载，不解释内容；页面与每日拆解共用同一份来源。
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
CONTENT_DIR = ROOT / "v2" / "content"
UNIVERSE_FILE = CONTENT_DIR / "hkex_universe.json"

LIST_URL = (
    "https://www.hkex.com.hk/chi/services/trading/securities/securitieslists/"
    "ListOfSecurities_c.xlsx"
)
PREFIX_URL = "https://www1.hkexnews.hk/search/prefix.do"
SEARCH_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www1.hkexnews.hk/listedco/listconews/advancedsearch/search_active_main.aspx",
    "Origin": "https://www1.hkexnews.hk",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}

ESG_KEYWORDS = (
    "esg",
    "sustainability",
    "sustainable",
    "csr",
    "environmental, social",
    "environmental social",
    "環境、社會",
    "环境、社会",
    "可持續",
    "可持续",
    "社會責任",
    "社会责任",
    "氣候",
    "气候",
)
ANNUAL_KEYWORDS = ("annual report", "annual report and accounts", "年報", "年报")
INTERIM_KEYWORDS = ("interim report", "中期報告", "中期报告")

UNIVERSE_MAX_AGE_DAYS = 30


class HkexSourceError(RuntimeError):
    pass


def _rows(payload: dict) -> list[dict]:
    raw = payload.get("result")
    if raw is None:
        return []
    if isinstance(raw, str):
        return json.loads(raw) if raw else []
    return raw


def _parse_release(text: str, fallback: date) -> date:
    try:
        day, month, year = text.split()[0].split("/")
        return date(int(year), int(month), int(day))
    except (ValueError, IndexError):
        return fallback


def _classify(title: str) -> str:
    lowered = title.lower()
    if any(k in lowered or k in title for k in ESG_KEYWORDS):
        return "esg"
    if any(k in lowered or k in title for k in ANNUAL_KEYWORDS):
        return "annual"
    if any(k in lowered or k in title for k in INTERIM_KEYWORDS):
        return "interim"
    return "other"


def _xlsx_rows(content: bytes):
    """流式读取 xlsx 首个工作表，避免 17MB 名单在低内存实例上撑爆。"""
    import io
    import zipfile
    from xml.etree import ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as fh:
                for _, elem in ET.iterparse(fh, events=("end",)):
                    if elem.tag == f"{ns}si":
                        shared.append(
                            "".join(t.text or "" for t in elem.iter() if t.tag == f"{ns}t")
                        )
                        elem.clear()
        with z.open("xl/worksheets/sheet1.xml") as fh:
            for _, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag != f"{ns}row":
                    continue
                cells: dict[str, str] = {}
                for cell in elem:
                    if cell.tag != f"{ns}c":
                        continue
                    ref = cell.get("r", "")
                    match = re.match(r"([A-Z]+)", ref)
                    if not match:
                        continue
                    kind = cell.get("t")
                    if kind == "inlineStr":
                        value = "".join(
                            t.text or "" for t in cell.iter() if t.tag == f"{ns}t"
                        )
                    else:
                        node = cell.find(f"{ns}v")
                        value = node.text if node is not None and node.text is not None else ""
                        if kind == "s" and value:
                            try:
                                value = shared[int(value)]
                            except (ValueError, IndexError):
                                pass
                    cells[match.group(1)] = value
                if cells:
                    yield (
                        cells.get("A", ""),
                        cells.get("B", ""),
                        cells.get("C", ""),
                        cells.get("D", ""),
                    )
                elem.clear()


def load_universe(force: bool = False, max_age_days: int = UNIVERSE_MAX_AGE_DAYS) -> list[dict]:
    """港股股本证券名单，缓存 30 天；来源于港交所公开 xlsx。"""
    if UNIVERSE_FILE.exists() and not force:
        cached = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
        try:
            updated = date.fromisoformat(cached["updated"])
        except (KeyError, ValueError):
            updated = date.min
        items = cached.get("items") or []
        if items and (date.today() - updated).days <= max_age_days:
            return items

    resp = httpx.get(LIST_URL, headers=HEADERS, timeout=180.0, follow_redirects=True)
    resp.raise_for_status()

    items: list[dict] = []
    seen: set[str] = set()
    for row in _xlsx_rows(resp.content):
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        name = str(row[1]).strip() if row[1] else ""
        category = str(row[2]).strip() if row[2] else ""
        sub = str(row[3]).strip() if row[3] else ""
        if category != "股本" or not code.isdigit() or not name:
            continue
        if "主板" not in sub and "gem" not in sub.lower():
            continue
        code = code.zfill(5)
        if code in seen:
            continue
        seen.add(code)
        items.append(
            {
                "code": code,
                "name": name,
                "board": "主板" if "主板" in sub else "GEM",
            }
        )
    if len(items) < 100:
        raise HkexSourceError(f"证券名单解析异常，仅得到 {len(items)} 条")

    items.sort(key=lambda x: x["code"])
    UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_FILE.write_text(
        json.dumps(
            {
                "updated": date.today().isoformat(),
                "source": LIST_URL,
                "count": len(items),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return items


class HkexSource:
    """带连接复用的港交所检索客户端。"""

    def __init__(self, timeout: float = 90.0):
        self.client = httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True)
        self._stock_ids: dict[str, int | None] = {}

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "HkexSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def resolve_stock_id(self, code: str) -> int | None:
        if code in self._stock_ids:
            return self._stock_ids[code]
        resp = self.client.get(
            PREFIX_URL,
            params={
                "callback": "cb",
                "lang": "ZH",
                "type": "A",
                "name": code,
                "market": "SEHK",
            },
        )
        resp.raise_for_status()
        match = re.search(r"\((\{.*\})\)", resp.text, re.S)
        stock_id = None
        if match:
            for item in json.loads(match.group(1)).get("stockInfo", []):
                if item.get("code") == code:
                    stock_id = int(item["stockId"])
                    break
        self._stock_ids[code] = stock_id
        return stock_id

    def search_documents(self, stock_id: int, today: date, years: int = 3) -> list[dict]:
        from_date = today - timedelta(days=365 * years)
        params = {
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": str(stock_id),
            "documentType": "-1",
            "fromDate": from_date.strftime("%Y%m%d"),
            "toDate": today.strftime("%Y%m%d"),
            "title": "",
            "searchType": "1",
            "t1code": "40000",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": "1-200",
        }
        resp = self.client.get(SEARCH_URL, params=params)
        resp.raise_for_status()
        docs: list[dict] = []
        for item in _rows(resp.json()):
            title = (item.get("TITLE") or "").strip()
            if not title:
                continue
            kind = _classify(title)
            if kind == "other":
                continue
            link = item.get("FILE_LINK") or ""
            if not link:
                continue
            docs.append(
                {
                    "title": title,
                    "release": _parse_release(item.get("DATE_TIME") or "", today),
                    "url": f"https://www1.hkexnews.hk{link}",
                    "kind": kind,
                }
            )
        return docs

    def latest_report(self, code: str, today: date, years: int = 3) -> dict | None:
        stock_id = self.resolve_stock_id(code)
        if stock_id is None:
            return None
        docs = self.search_documents(stock_id, today, years=years)
        return pick_latest_report(docs, today)


def pick_latest_report(docs: list[dict], today: date) -> dict | None:
    """优先最近 18 个月的独立 ESG/可持续报告，其次最新年报，最后中期报告。"""
    usable = [d for d in docs if d["release"] <= today]
    if not usable:
        return None

    esg = sorted((d for d in usable if d["kind"] == "esg"), key=lambda d: d["release"], reverse=True)
    annual = sorted(
        (d for d in usable if d["kind"] == "annual"), key=lambda d: d["release"], reverse=True
    )
    interim = sorted(
        (d for d in usable if d["kind"] == "interim"), key=lambda d: d["release"], reverse=True
    )

    if esg and (today - esg[0]["release"]).days <= 548:
        return esg[0]
    if annual:
        return annual[0]
    if esg:
        return esg[0]
    if interim:
        return interim[0]
    return None


def download_pdf(url: str, target: Path) -> int:
    """流式下载，先写 .part 再替换，避免中断留下半个文件。"""
    tmp = target.with_suffix(".part")
    with httpx.Client(headers=HEADERS, timeout=300.0, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    tmp.replace(target)
    return target.stat().st_size
