"""把编辑稿、报告索引与评级快照合并成每日拆解文章（种子补录）。

输入：
    v2/content/daily_plan.json        编辑稿（选题、角度、阅读地图、问题）
    data/hkex_reports/_index.json     报告元数据（标题、发布日期、披露易链接）
    v2/content/ratings/<code>.json    公开评级快照（由 fetch_esg_ratings 生成）

输出：
    v2/content/daily/<date>-<code>.json

用法：
    python -m v2.scripts.build_daily

只补 daily 目录里不存在的种子文章；已发布文章只读，不删除、不覆盖。
日常新增由 v2.scripts.daily_update 负责。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN_FILE = ROOT / "v2" / "content" / "daily_plan.json"
RATINGS_DIR = ROOT / "v2" / "content" / "ratings"
OUT_DIR = ROOT / "v2" / "content" / "daily"
INDEX_FILE = ROOT / "data" / "hkex_reports" / "_index.json"

STALE_DAYS = 365
TODAY = date.today()


def parse_rating_date(text: str | None) -> date | None:
    """支持 2026-11-18 与 2026Q3 两种写法，无法解析则返回 None。"""
    if not text:
        return None
    text = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{4})Q([1-4])", text)
    if m:
        month = int(m.group(2)) * 3
        return date(int(m.group(1)), month, 28)
    return None


def build_episode(ep: dict, index_by_code: dict, ratings_by_code: dict) -> dict:
    code = ep["code"]
    sample = index_by_code.get(code)
    if sample is None:
        raise ValueError(f"报告中找不到代号 {code}")
    rating_file = ratings_by_code.get(code)
    if rating_file is None:
        raise ValueError(f"缺少评级快照 {code}，请先运行 fetch_esg_ratings")

    ratings = []
    for r in rating_file["ratings"]:
        parsed = parse_rating_date(r.get("rating_date"))
        ratings.append(
            {
                **r,
                "stale": bool(parsed and (TODAY - parsed).days > STALE_DAYS),
            }
        )
    msci = next((r for r in ratings if r["agency"] == "MSCI"), None)

    slug = f"{ep['date']}-{code}"
    return {
        "slug": slug,
        "date": ep["date"],
        "code": code,
        "company": sample["name_cn"],
        "industry": ep["industry"],
        "angle": ep["angle"],
        "hook": ep["hook"],
        "why": ep["why"],
        "reading_map": ep["reading_map"],
        "observations": ep["observations"],
        "rating_read": ep["rating_read"],
        "questions": ep["questions"],
        "skills": ep["skills"],
        "takeaway": ep["takeaway"],
        "report": {
            "title": sample["title"],
            "release_date": sample["release_date"],
            "url": sample["url"],
            "source": "港交所披露易",
        },
        "ratings": ratings,
        "rating_meta": {
            "count": len(ratings),
            "stale_count": sum(1 for r in ratings if r["stale"]),
            "msci": None if msci is None else {"rating": msci["rating"], "date": msci["rating_date"]},
            "fetched_at": rating_file["fetched_at"],
            "source": rating_file["source"],
            "source_url": rating_file["source_url"],
            "note": rating_file["note"],
        },
    }


def main() -> int:
    for path in (PLAN_FILE, INDEX_FILE):
        if not path.exists():
            print(f"缺少输入文件：{path}", file=sys.stderr)
            return 1

    plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    index_by_code = {v["code"]: v for v in json.loads(INDEX_FILE.read_text(encoding="utf-8")).values()}
    ratings_by_code = {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in RATINGS_DIR.glob("*.json")
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    created, skipped, failed = 0, 0, 0
    for ep in plan["episodes"]:
        slug = f"{ep['date']}-{ep['code']}"
        target = OUT_DIR / f"{slug}.json"
        if target.exists():
            # 已发布内容只读：只补缺失的种子文章，绝不覆盖每日新增
            skipped += 1
            continue
        try:
            article = build_episode(ep, index_by_code, ratings_by_code)
        except ValueError as exc:
            print(f"[FAIL] {ep.get('date')} {ep.get('code')}: {exc}", file=sys.stderr)
            failed += 1
            continue
        target.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
        created += 1
        print(f"[OK] {article['date']} {article['code']} {article['company']}: {article['angle']}")

    total = len(list(OUT_DIR.glob("*.json")))
    print(f"\n完成：新增 {created} 期，跳过已存在 {skipped} 期，失败 {failed} 期；目录共 {total} 期")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
