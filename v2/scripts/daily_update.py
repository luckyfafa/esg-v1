"""每日生成一期「上市公司 ESG 报告拆解」。

一条命令跑完整个流程（现有 08:00 定时任务直接调用）：
    python -m v2.scripts.daily_update

流程：
    1. 从港交所公开证券名单里取一家尚未拆解过的公司
    2. 在披露易检索它最新的一份 ESG/可持续报告（或最近年报）
    3. 下载 PDF，提取书签、章节页码、披露框架与鉴证等级
    4. 抓取新浪财经公开评级快照（接口没有的机构不补值）
    5. 生成 v2/content/daily/<日期>-<代码>.json

约束：
    - 只用公开渠道，评级不编造
    - 已存在的文章只读不写；同一天已有文章则直接退出
    - 列表、sitemap、RSS 都从 daily 目录实时读取，新文件写入后自动出现
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from .build_daily import parse_rating_date
from .fetch_esg_ratings import fetch_snapshot
from .hkex_source import HkexSource, download_pdf, load_universe
from .report_facts import READING_ORDER, extract_facts
from ..app.indexnow import episode_url, submit_safely

ROOT = Path(__file__).resolve().parents[2]
DAILY_DIR = ROOT / "v2" / "content" / "daily"
RATINGS_DIR = ROOT / "v2" / "content" / "ratings"
STATE_FILE = ROOT / "v2" / "content" / "daily_state.json"

STALE_DAYS = 365

INDUSTRY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("银行", ("銀行", "银行", "bank")),
    ("保险", ("保險", "保险", "insurance", "友邦", "平安")),
    ("房地产", ("地產", "地产", "置業", "置业", "房地產", "房地产", "新鴻基", "新鸿基", "恒基", "長實", "长实")),
    ("医药", ("醫藥", "医药", "藥業", "药业", "生物", "製藥", "制药", "醫療", "医疗", "石藥", "石药")),
    ("能源", ("能源", "石油", "天然氣", "天然气", "煤", "油氣", "油气", "電力", "电力", "發電", "发电", "中電", "中电")),
    ("科技", ("科技", "軟件", "软件", "騰訊", "腾讯", "阿里巴巴", "小米", "美團", "美团", "網易", "网易", "快手", "京東", "京东")),
    ("物流", ("物流", "航運", "航运", "港口", "運輸", "运输", "海控", "貨櫃", "货柜")),
    ("汽车", ("汽車", "汽车", "吉利", "比亞迪", "比亚迪", "蔚來", "蔚来", "小鵬", "小鹏")),
    ("食品饮料", ("食品", "飲料", "饮料", "啤酒", "乳業", "乳业", "農夫山泉", "农夫山泉", "蒙牛", "華潤啤酒", "华润啤酒")),
    ("公用事业", ("燃氣", "燃气", "水務", "水务", "公用")),
    ("制造业", ("製造", "制造", "工業", "工业", "電子", "电子", "機械", "机械", "偉易達", "伟易达")),
]


def _short(text: str, limit: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def infer_industry(name: str, title: str = "") -> str:
    haystack = f"{name} {title}".lower()
    for industry, keys in INDUSTRY_KEYWORDS:
        if any(k.lower() in haystack for k in keys):
            return industry
    return "综合"


def load_existing() -> list[dict]:
    if not DAILY_DIR.exists():
        return []
    episodes = []
    for path in sorted(DAILY_DIR.glob("*.json")):
        try:
            episodes.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] 跳过无法读取的往期文件 {path.name}: {exc}", file=sys.stderr)
    return episodes


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"cursor": 0}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def build_reading_map(facts: dict) -> list[dict]:
    rows: list[dict] = []
    topics = facts["topics"]
    for key in READING_ORDER:
        topic = topics[key]
        if not topic["page"]:
            continue
        locator = f"PDF p{topic['page']}"
        outline_title = topic.get("outline_title")
        if outline_title and outline_title.lower() != topic["label"].lower():
            locator += f" · {_short(outline_title, 28)}"
        rows.append(
            {
                "section": topic["label"],
                "locator": locator,
                "look_for": topic["look_for"],
                "clause": topic["clause"],
            }
        )
        if len(rows) >= 6:
            break

    if len(rows) < 3:
        used = {r["section"] for r in rows}
        for item in facts["outline"]:
            if len(rows) >= 5:
                break
            if not item["page"] or item["title"] in used:
                continue
            rows.append(
                {
                    "section": item["title"],
                    "locator": f"PDF p{item['page']}",
                    "look_for": "按书签核对这一章的起止范围",
                    "clause": "—",
                }
            )

    if not rows:
        rows.append(
            {
                "section": "报告全文",
                "locator": "—",
                "look_for": "PDF 文本层不可检索，需要按目录人工定位",
                "clause": "—",
            }
        )
    return rows


def _rating_rows(snapshot: dict, today: date) -> list[dict]:
    ratings = []
    for item in snapshot.get("ratings", []):
        parsed = parse_rating_date(item.get("rating_date"))
        ratings.append({**item, "stale": bool(parsed and (today - parsed).days > STALE_DAYS)})
    return ratings


def _msci_band(rating: str | None) -> str:
    value = (rating or "").strip().upper()
    if value in ("AAA", "AA"):
        return "领先"
    if value in ("A", "BBB", "BB"):
        return "平均"
    if value in ("B", "CCC"):
        return "落后"
    return "未分档"


def build_rating_read(ratings: list[dict], snapshot: dict) -> str:
    if not ratings:
        return (
            "本期评级接口未返回可用结果。没有公开评级不等于评级差，也不能据此推断档位；"
            "页面只保留空快照，不做补值。"
        )
    msci = next((r for r in ratings if r.get("agency") == "MSCI"), None)
    parts = [f"本期快照抓取于 {snapshot.get('fetched_at')}，共 {len(ratings)} 家机构有公开结果。"]
    if msci:
        parts.append(
            f"MSCI 为 {msci['rating']}（{msci.get('rating_date') or '日期未返回'}），"
            f"按七档口径属于{_msci_band(msci['rating'])}档。"
        )
    else:
        parts.append("接口没有返回 MSCI 结果，不能据此推断评级高低。")
    others = [
        f"{r['agency']} {r['rating']}" for r in ratings if r.get("agency") != "MSCI"
    ][:5]
    if others:
        parts.append("其余机构：" + "、".join(others) + "。")
    stale = sum(1 for r in ratings if r["stale"])
    if stale:
        parts.append(f"其中 {stale} 家结果超过 {STALE_DAYS} 天，只作历史参考。")
    parts.append(
        "不同机构的行业调整、指标权重与数据年份不同，字母与分数不可横向比较；"
        "这里只并列呈现，不替机构下结论。"
    )
    return "".join(parts)


def build_episode(
    *,
    code: str,
    company: str,
    industry: str,
    report: dict,
    facts: dict,
    snapshot: dict,
    episode_date: date,
) -> dict:
    ratings = _rating_rows(snapshot, episode_date)
    msci = next((r for r in ratings if r.get("agency") == "MSCI"), None)
    topics = facts["topics"]
    found = [(key, topics[key]) for key in READING_ORDER if topics[key]["page"]]
    missing = [topics[key]["label"] for key in READING_ORDER if not topics[key]["page"]]
    page_of = {key: topics[key]["page"] for key in topics}

    kind = facts["report_kind"]
    outline_n = facts["outline_count"]
    pages = facts["pages"]
    reading_map = build_reading_map(facts)
    map_n = len(reading_map)

    if not facts["has_text"]:
        angle = "文本层不可检索：先按目录搭阅读地图"
    elif not outline_n:
        angle = "没有书签的报告，怎么用关键词定位"
    elif kind == "整合年报":
        angle = "ESG 写在年报里：先定位，再判断"
    elif page_of.get("scope3"):
        angle = "范围三：这家公司把边界划到哪一步"
    elif facts.get("assurance_level"):
        angle = "有鉴证的 ESG 数据，可信度看哪一行"
    else:
        angle = "从目录到数据表：一份 ESG 报告的读法"

    if facts["has_text"]:
        lead = f"{company}这份《{_short(report['title'], 46)}》共 {pages} 页，类型判断为{kind}"
        lead += f"，可读取 {outline_n} 条带页码的书签。" if outline_n else "，PDF 没有可用书签。"
        if found:
            points = "、".join(f"{t['label']} p{t['page']}" for _, t in found[:4])
            hook = lead + f"我们先不从头读，用 {map_n} 个定位点（{points}）搭结构，再对照 {len(ratings)} 家机构的公开评级。"
        else:
            hook = lead + "全文关键词没有定位到稳定章节，这期先练「确认文件与目录」，不做数据判断。"
    else:
        hook = (
            f"{company}这份《{_short(report['title'], 46)}》共 {pages} 页，但 PDF 文本层无法检索。"
            "这期只做一件事：确认文件类型、目录结构与披露框架，页码留给人工核对。"
        )

    why = (
        f"选这份报告，是因为它同时提供了三个可练习的点：{kind}的文件结构、"
        f"{map_n} 个可核对章节、{len(ratings)} 家机构的公开评级。"
        f"{industry}行业的读者可以把同一套定位动作复用到其他公司。"
    )
    if not ratings:
        why = (
            f"选这份报告，是因为它提供了{kind}的文件结构与 {map_n} 个可核对章节；"
            f"本期没有公开评级，练习重点放在报告本身。"
        )

    found_desc = "、".join(f"{t['label']} p{t['page']}" for _, t in found) or "无"
    missing_desc = "、".join(missing) if missing else "无"
    observations = [
        f"《{report['title']}》由港交所披露易于 {report['release'].isoformat()} 公布，共 {pages} 页，"
        f"类型判断为{kind}；可读取带页码的书签 {outline_n} 条。",
        f"关键词定位结果：{found_desc}。未定位：{missing_desc}。页码为 PDF 页序，需与报告目录核对。",
        f"文本中检出：{'、'.join(facts['standards']) or '未检出常见披露框架名称'}；"
        f"鉴证：{facts.get('assurance_level') or '未检出独立鉴证声明'}。",
    ]
    if page_of.get("scope3"):
        observations.append(
            f"范围三内容出现在 p{page_of['scope3']}。{industry}公司的价值链边界通常比直接排放更关键，"
            "先确认报告覆盖了哪几类，再看是否给出数字。"
        )
    else:
        observations.append(
            "未定位到范围三页码，可能是文本层限制，也可能报告没有单列；不能据此判断公司没有披露。"
        )
    observations.append(
        f"评级快照共 {len(ratings)} 家机构有结果"
        + (f"，MSCI 为 {msci['rating']}（{msci.get('rating_date') or '日期未返回'}）" if msci else "，接口未返回 MSCI")
        + f"；{sum(1 for r in ratings if r['stale'])} 家结果超过 {STALE_DAYS} 天。"
    )

    questions = [
        (
            f"董事会在 p{page_of['board']} 的声明，是否同时覆盖监管、策略与目标检讨？"
            if page_of.get("board")
            else "报告有没有董事会层面的 ESG 声明？如果没有单独章节，它可能写在哪里？"
        ),
        (
            "范围一/二/三的边界写在哪一页？哪些类别只给方法、没有数字？"
            if page_of.get("scope1") or page_of.get("scope3")
            else "报告用什么方式呈现排放数据：表格、图表还是叙述？"
        ),
        (
            f"MSCI 为 {msci['rating']}（{msci.get('rating_date') or '日期未返回'}），"
            "与国内机构的字母分档能不能直接比较？"
            if msci
            else "本期没有 MSCI 公开结果，能否据此判断公司评级高低？"
        ),
    ]

    skills = [
        {"name": "报告类型识别", "how": "先看标题、期间与附件清单，确认是独立 ESG 报告、整合年报还是中期报告"},
        {
            "name": "关键词定位",
            "how": "用中英文关键词在 PDF 中检索章节，再回目录核对页码与起止范围",
        },
        (
            {"name": "范围三边界", "how": "先列价值链类别，再确认哪些类别有数据、哪些只给方法"}
            if page_of.get("scope3")
            else {"name": "评级时效判断", "how": "先看机构与日期，超过 365 天的结果只作历史参考"}
        ),
    ]

    if not facts["has_text"]:
        takeaway = "文本层不可检索时，先读目录与框架，再决定哪些页码值得人工翻。"
    elif not outline_n:
        takeaway = "没有书签不是障碍，关键词加目录同样能定位。"
    elif kind == "整合年报":
        takeaway = "先确认文件类型，再谈章节与页码；否则越读越乱。"
    elif page_of.get("scope3"):
        takeaway = "范围三的难点不是算，而是先划清边界。"
    elif facts.get("assurance_level"):
        takeaway = "看到「鉴证」先问等级，再引用数字。"
    else:
        takeaway = "先定位，再判断；报告不是从头读到尾的。"

    return {
        "slug": f"{episode_date.isoformat()}-{code}",
        "date": episode_date.isoformat(),
        "code": code,
        "company": company,
        "industry": industry,
        "angle": angle,
        "hook": hook,
        "why": why,
        "reading_map": reading_map,
        "observations": observations,
        "rating_read": build_rating_read(ratings, snapshot),
        "questions": questions,
        "skills": skills,
        "takeaway": takeaway,
        "report": {
            "title": report["title"],
            "release_date": report["release"].isoformat(),
            "url": report["url"],
            "pages": pages,
            "source": "港交所披露易",
        },
        # 每期独立页的 title/description；后续可人工改写为更贴搜索词的版本
        "seo": {
            "title": f"{company}（{code}）ESG 报告拆解：{angle}",
            "description": (
                f"{company}（{code}）《{report['title']}》拆解：报告阅读地图、章节页码与公开评级快照"
                + (f"，MSCI {msci['rating']}" if msci else "")
                + "，并说明不同机构评级口径的差异。"
            ),
        },
        "ratings": ratings,
        "rating_meta": {
            "count": len(ratings),
            "stale_count": sum(1 for r in ratings if r["stale"]),
            "msci": None
            if msci is None
            else {"rating": msci["rating"], "date": msci.get("rating_date")},
            "fetched_at": snapshot.get("fetched_at"),
            "source": snapshot.get("source"),
            "source_url": snapshot.get("source_url"),
            "note": snapshot.get("note"),
        },
    }


def _try_candidate(
    source: HkexSource,
    item: dict,
    today: date,
    max_downloads: int,
    downloads: list[int],
) -> tuple[dict, dict, dict] | None:
    code = item["code"]
    try:
        report = source.latest_report(code, today)
    except Exception as exc:  # noqa: BLE001 - 单家检索失败不应中断整轮
        print(f"[FAIL] {code} {item['name']}: 披露易检索失败：{exc}")
        return None
    if report is None:
        print(f"[SKIP] {code} {item['name']}: 近三年没有可用 ESG/年报")
        return None
    if downloads[0] >= max_downloads:
        return None
    downloads[0] += 1

    with tempfile.TemporaryDirectory(prefix="esg-daily-") as tmp:
        target = Path(tmp) / f"{code}_{report['release'].isoformat()}.pdf"
        print(f"[GET ] {code} {item['name']}: {report['title']} ({report['release']})")
        try:
            download_pdf(report["url"], target)
            facts = extract_facts(target, report["title"])
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {code} PDF 下载或解析失败：{exc}")
            return None

    if facts["pages"] == 0 or (not facts["has_text"] and not facts["outline_count"]):
        print(f"[SKIP] {code} PDF 无可读文本与书签，换下一家")
        return None
    return item, report, facts


def select_company(
    source: HkexSource,
    universe: list[dict],
    covered: set[str],
    state: dict,
    today: date,
    max_scan: int,
    max_downloads: int,
    forced_code: str | None,
) -> tuple[dict, dict, dict] | None:
    downloads = [0]

    if forced_code:
        item = next((x for x in universe if x["code"] == forced_code), None)
        if item is None:
            item = {"code": forced_code, "name": forced_code, "board": "—"}
        return _try_candidate(source, item, today, max_downloads, downloads)

    start = int(state.get("cursor", 0)) % max(len(universe), 1)
    for offset in range(min(max_scan, len(universe))):
        index = (start + offset) % len(universe)
        item = universe[index]
        state["cursor"] = (index + 1) % len(universe)
        if item["code"] in covered:
            continue
        print(f"[SCAN] {item['code']} {item['name']}")
        result = _try_candidate(source, item, today, max_downloads, downloads)
        if result is not None:
            return result
        if downloads[0] >= max_downloads:
            print(f"[STOP] 已尝试 {max_downloads} 份 PDF，仍未得到可解析报告")
            break
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成今日的港股 ESG 报告拆解")
    parser.add_argument("--date", help="发布日期，默认今天；仅用于补跑或测试")
    parser.add_argument("--code", help="指定股票代码（测试或补录），跳过自动选择")
    parser.add_argument("--max-scan", type=int, default=25, help="最多检查多少家公司")
    parser.add_argument("--max-downloads", type=int, default=3, help="最多下载多少份 PDF")
    parser.add_argument("--refresh-universe", action="store_true", help="强制刷新港交所证券名单")
    parser.add_argument("--dry-run", action="store_true", help="只生成预览，不写入文件")
    args = parser.parse_args(argv)

    episode_date = date.fromisoformat(args.date) if args.date else date.today()
    existing = load_existing()
    covered = {ep.get("code") for ep in existing if ep.get("code")}
    same_day = [ep for ep in existing if ep.get("date") == episode_date.isoformat()]

    if same_day and not args.code:
        print(f"{episode_date} 已有 {len(same_day)} 期，今日不重复生成：{same_day[0].get('slug')}")
        return 0
    if same_day and args.code and not args.dry_run:
        print(f"{episode_date} 已有文章，今日不重复生成；如需预览请加 --dry-run")
        return 0
    if args.code and args.code in covered:
        print(f"{args.code} 已经拆解过，不覆盖往期；如需换一家请去掉 --code")
        return 0

    universe = load_universe(force=args.refresh_universe)
    state = load_state()
    print(f"证券名单 {len(universe)} 家；已拆解 {len(covered)} 家；目标日期 {episode_date}")

    with HkexSource() as source:
        picked = select_company(
            source,
            universe,
            covered,
            state,
            episode_date,
            args.max_scan,
            args.max_downloads,
            args.code,
        )

    if picked is None:
        print("[FAIL] 本轮没有找到可用的新报告；已保存扫描游标，明日继续", file=sys.stderr)
        if not args.dry_run:
            state.update({"last_run": date.today().isoformat(), "last_result": "no_report"})
            save_state(state)
        return 2

    item, report, facts = picked
    code = item["code"]
    company = item["name"]
    industry = infer_industry(company, report["title"])

    try:
        snapshot = fetch_snapshot(code, company)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] {code} 评级抓取失败：{exc}；本期按空快照发布")
        snapshot = {
            "code": code,
            "company": company,
            "fetched_at": date.today().isoformat(),
            "source": "新浪财经 ESG 公开数据",
            "source_url": "https://finance.sina.com.cn/",
            "note": "本次抓取失败，未获得公开评级；不补值、不推断。",
            "ratings": [],
        }
    if not snapshot.get("ratings"):
        cached = RATINGS_DIR / f"{code}.json"
        if cached.exists():
            snapshot = json.loads(cached.read_text(encoding="utf-8"))

    episode = build_episode(
        code=code,
        company=company,
        industry=industry,
        report=report,
        facts=facts,
        snapshot=snapshot,
        episode_date=episode_date,
    )

    if args.dry_run:
        print("\n--- 预览 ---")
        print(json.dumps(
            {
                "slug": episode["slug"],
                "company": company,
                "industry": industry,
                "angle": episode["angle"],
                "hook": episode["hook"],
                "reading_map": episode["reading_map"],
                "ratings": episode["rating_meta"],
            },
            ensure_ascii=False,
            indent=2,
        ))
        print("--- 预览结束，未写入任何文件 ---")
        return 0

    RATINGS_DIR.mkdir(parents=True, exist_ok=True)
    target = DAILY_DIR / f"{episode['slug']}.json"
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as fh:
            json.dump(episode, fh, ensure_ascii=False, indent=2)
    except FileExistsError:
        print(f"[SKIP] {target.name} 已存在，拒绝覆盖", file=sys.stderr)
        return 0

    rating_file = RATINGS_DIR / f"{code}.json"
    if snapshot.get("ratings") or not rating_file.exists():
        rating_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    state.update(
        {
            "last_run": date.today().isoformat(),
            "last_result": "ok",
            "last_code": code,
            "last_slug": episode["slug"],
            "cursor": state.get("cursor", 0),
        }
    )
    save_state(state)
    # 通知 IndexNow（Bing 等）；失败只记日志，不中断发布
    submit_safely([episode_url(episode["slug"])], label=f"新一期 {episode['slug']}")
    print(f"\n[OK] 已发布 {episode['slug']}：{company} · {industry} · {episode['angle']}")
    print(f"     报告：{report['title']}（{report['release']}）")
    print(f"     评级：{len(episode['ratings'])} 家；文件：{target}")
    print("     列表、sitemap、RSS 均由 daily 目录实时读取，无需再构建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
