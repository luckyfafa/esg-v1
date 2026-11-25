"""从港交所 ESG/年报 PDF 中提取可核对的事实。

只记录「哪一页出现了什么」，不猜测数字含义：
    - 页数、书签/目录条目
    - 董事会声明、重要性、排放、员工、鉴证、GRI 索引等章节的页码
    - 报告类型、披露框架、鉴证等级
提取不到时返回空值，由调用方如实写成「未定位」。
"""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

MAX_PAGES = 320

# key, 展示名, 检索词, 对应条款, 看什么, 同页需要的上下文
TOPICS: list[tuple[str, str, tuple[str, ...], str, str, tuple[str, ...]]] = [
    (
        "board",
        "董事会声明",
        ("董事會聲明", "董事会声明", "board statement", "statement from the board", "chairman's statement", "主席報告", "主席报告"),
        "B-13",
        "董事会对 ESG 的监管、策略与目标检讨",
        ("esg", "sustainability", "可持續", "可持续", "risk", "風險", "风险", "strategy", "策略"),
    ),
    (
        "materiality",
        "重要性评估",
        ("materiality", "material topics", "重大主題", "重大主题", "重要性評估", "重要性评估", "雙重重要性", "双重重要性"),
        "B-14",
        "议题识别过程、持份者参与、优次排列",
        ("stakeholder", "持份者", "利益相關方", "利益相关方", "assessment", "評估", "评估", "matrix", "矩陣", "矩阵"),
    ),
    (
        "climate",
        "气候与排放",
        ("climate change", "氣候變化", "气候变化", "tcfd", "氣候相關", "气候相关", "climate risk", "氣候風險", "气候风险"),
        "D-19",
        "气候风险、情景分析、目标与排放",
        ("risk", "風險", "风险", "scenario", "情景", "emission", "排放", "target", "目標", "目标"),
    ),
    (
        "scope1",
        "范围一/二排放",
        ("範圍一", "范围一", "scope 1", "scope 2", "範圍二", "范围二"),
        "C-A1.1",
        "排放边界、基准年、总量与强度",
        ("emission", "排放", "tonnes", "噸", "吨", "intensity", "強度", "强度", "kwh", "mwh"),
    ),
    (
        "scope3",
        "范围三排放",
        ("範圍三", "范围三", "scope 3", "價值鏈", "价值链"),
        "C-A1.2 / D-17",
        "价值链类别、估算方法与覆盖范围",
        ("value chain", "價值鏈", "价值链", "category", "類別", "类别", "purchased", "採購", "采购"),
    ),
    (
        "energy",
        "能源使用",
        ("energy consumption", "能源消耗", "耗電", "耗电", "用電量", "用电量", "renewable energy", "可再生能源"),
        "C-A2.1",
        "能耗总量、强度、可再生能源占比",
        ("kwh", "mwh", "gj", "總量", "总量", "強度", "强度", "consume", "消耗"),
    ),
    (
        "employee",
        "员工与社会",
        ("employment", "僱傭", "雇佣", "health and safety", "健康與安全", "健康与安全", "occupational health", "職業健康", "职业健康", "training", "培訓", "培训"),
        "C-B1",
        "雇佣、健康安全、培训与流失",
        ("employee", "僱員", "雇员", "员工", "workforce", "員工", "员工"),
    ),
    (
        "supply",
        "供应链管理",
        ("供應鏈", "供应链", "supply chain", "供應商", "供应商"),
        "C-B5",
        "供应商准入、评估、环境与社会要求",
        ("supplier", "供應商", "供应商", "procurement", "採購", "采购", "assessment", "評估", "评估"),
    ),
    (
        "governance",
        "治理与商业道德",
        ("anti-corruption", "反貪污", "反贪污", "anti-bribery", "反賄賂", "反贿赂", "whistleblowing", "舉報", "举报", "business ethics", "商業道德", "商业道德"),
        "C-B7",
        "反腐败制度、举报机制、培训",
        ("policy", "政策", "training", "培訓", "培训", "case", "個案", "个案"),
    ),
    (
        "assurance",
        "鉴证",
        ("independent practitioner", "assurance report", "independent assurance", "獨立鑑證", "独立鉴证", "核證報告", "核证报告", "verification statement", "鑒證", "鉴证"),
        "B-14",
        "保证等级、覆盖指标、结论措辞",
        ("limited", "reasonable", "有限", "合理", "opinion", "結論", "结论", "scope", "範圍", "范围"),
    ),
    (
        "gri",
        "GRI 内容索引",
        ("gri content index", "gri 內容索引", "gri 内容索引", "gri index", "gri 索引", "gri 準則", "gri 准则"),
        "B-14",
        "指标索引、披露缺口",
        ("指標", "指标", "披露", "disclosure", "附錄", "附录"),
    ),
]

READING_ORDER = ("board", "materiality", "climate", "scope1", "scope3", "assurance", "employee", "gri")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _flatten_outline(items, reader: PdfReader, depth: int = 0, out: list[dict] | None = None):
    if out is None:
        out = []
    for item in items or []:
        if isinstance(item, list):
            _flatten_outline(item, reader, depth + 1, out)
            continue
        title = getattr(item, "title", None) or str(item)
        title = _normalize(str(title))
        page = None
        try:
            page = reader.get_destination_page_number(item) + 1
        except Exception:  # noqa: BLE001 - 个别书签指向无效目标
            page = None
        out.append({"title": title, "page": page, "depth": depth})
    return out


def _toc_pages(texts: list[str]) -> set[int]:
    """识别目录页：同时出现 contents/目錄 与多个章节词。"""
    pages: set[int] = set()
    all_needles = [n for _, _, needles, _, _, _ in TOPICS for n in needles]
    for idx, text in enumerate(texts, start=1):
        low = text.lower()
        if not any(k in low for k in ("contents", "目錄", "目录")):
            continue
        if sum(1 for needle in all_needles if needle.lower() in low) >= 4:
            pages.add(idx)
    return pages


def _strip_repeated(texts: list[str]) -> list[str]:
    """去掉在多数页面重复出现的页眉/侧边导航，避免章节名被误命中。"""
    from collections import Counter

    counts: Counter[str] = Counter()
    for text in texts:
        for line in text.split("\n"):
            line = line.strip()
            if 3 <= len(line) <= 90:
                counts[line] += 1
    threshold = max(4, int(len(texts) * 0.2))
    repeated = {line for line, count in counts.items() if count >= threshold}
    cleaned = []
    for text in texts:
        kept = [line for line in text.split("\n") if line.strip() not in repeated]
        cleaned.append(_normalize(" ".join(kept)))
    return cleaned


def _best_page(
    texts: list[str],
    needles: tuple[str, ...],
    context: tuple[str, ...] = (),
    skip: set[int] | None = None,
    start: int = 4,
) -> int | None:
    """在目录页之后找命中最多的一页；命中多通常意味着该章正文而非一句提及。"""
    skip = skip or set()
    best_score = 0.0
    best_page = None
    for idx in range(start - 1, len(texts)):
        page = idx + 1
        if page in skip:
            continue
        low = texts[idx].lower()
        hits = sum(low.count(n.lower()) for n in needles)
        if not hits:
            continue
        if context:
            ctx = sum(low.count(c.lower()) for c in context)
            if not ctx:
                continue
            score = hits + ctx * 0.5
        else:
            score = float(hits)
        if score > best_score:
            best_score = score
            best_page = page
    return best_page


def _first_outline(outline: list[dict], needles: tuple[str, ...]) -> dict | None:
    for item in outline:
        title = item["title"].lower()
        if any(n.lower() in title for n in needles):
            return item
    return None


def _report_kind(title: str) -> str:
    t = title.lower()
    if "sustainability" in t or "sustainable" in t:
        return "独立可持续发展报告"
    if "esg" in t or "environmental, social" in t or "環境、社會" in title:
        return "独立 ESG 报告"
    if "annual report" in t or "年報" in title or "年报" in title:
        return "整合年报"
    if "interim" in t or "中期" in title:
        return "中期报告"
    return "ESG 相关披露"


def _standards(text: str) -> list[str]:
    found = []
    if re.search(r"\bGRI\b", text) or "GRI 標準" in text or "GRI 标准" in text:
        found.append("GRI")
    if any(k in text for k in ("附錄C2", "附录C2", "Appendix C2", "ESG Reporting Guide", "環境、社會及管治報告指引")):
        found.append("港交所附录 C2")
    if "TCFD" in text or "氣候相關財務揭露" in text or "气候相关财务披露" in text:
        found.append("TCFD")
    if "ISSB" in text or "IFRS S1" in text or "IFRS S2" in text:
        found.append("ISSB")
    if "SASB" in text:
        found.append("SASB")
    if re.search(r"\bCDP\b", text):
        found.append("CDP")
    return found


def extract_facts(pdf_path: Path, title: str) -> dict:
    reader = PdfReader(str(pdf_path))
    pages = len(reader.pages)
    try:
        outline = _flatten_outline(reader.outline, reader)
    except Exception:  # noqa: BLE001
        outline = []

    raw_texts: list[str] = []
    for page in reader.pages[:MAX_PAGES]:
        try:
            raw_texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            raw_texts.append("")

    texts = _strip_repeated(raw_texts)
    toc = _toc_pages(raw_texts)
    joined = "\n".join(texts)
    topics: dict[str, dict] = {}
    for key, label, needles, clause, look_for, context in TOPICS:
        start = 2 if key == "board" else 4
        page = _best_page(texts, needles, context, skip=toc, start=start)
        if page is None:
            # 放宽上下文限制再找一次，仍然避开目录页
            page = _best_page(texts, needles, (), skip=toc, start=start)
        if page is None:
            page = _best_page(texts, needles, (), skip=toc, start=1)
        hit = _first_outline(outline, needles)
        topics[key] = {
            "key": key,
            "label": label,
            "page": page,
            "clause": clause,
            "look_for": look_for,
            "outline_title": hit["title"] if hit else None,
        }

    assurance_text = " ".join(texts[topics["assurance"]["page"] - 1 : topics["assurance"]["page"] + 1]) if topics["assurance"]["page"] else joined
    if "reasonable assurance" in assurance_text.lower() or "合理保證" in assurance_text or "合理保证" in assurance_text:
        assurance_level = "合理保证（reasonable assurance）"
    elif "limited assurance" in assurance_text.lower() or "有限保證" in assurance_text or "有限保证" in assurance_text:
        assurance_level = "有限保证（limited assurance）"
    elif any(k in assurance_text.lower() for k in ("assurance report", "verification statement", "independent practitioner")):
        assurance_level = "已检出独立鉴证声明（未注明等级）"
    else:
        assurance_level = None

    emissions_numbers = bool(
        re.search(
            r"(Scope\s*1|Scope\s*2|範圍一|范围一|範圍二|范围二)[^。\n]{0,140}\d",
            joined,
            re.I,
        )
    )

    return {
        "pages": pages,
        "outline": outline,
        "outline_count": len([x for x in outline if x["page"]]),
        "has_text": len(joined) > 3000,
        "text_chars": len(joined),
        "topics": topics,
        "standards": _standards(joined),
        "assurance_level": assurance_level,
        "report_kind": _report_kind(title),
        "emissions_numbers": emissions_numbers,
    }
