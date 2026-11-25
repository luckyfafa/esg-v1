"""学习中心与每日拆解的内容读取。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"
LEARN_DIR = CONTENT_DIR / "learn"
DAILY_DIR = CONTENT_DIR / "daily"
PLAN_FILE = CONTENT_DIR / "daily_plan.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def learn_paths() -> list[dict]:
    if not LEARN_DIR.exists():
        return []
    items = [_read(p) for p in LEARN_DIR.glob("*.json")]
    return sorted(items, key=lambda x: x.get("order", 99))


@lru_cache(maxsize=8)
def learn_path(slug: str) -> dict | None:
    for item in learn_paths():
        if item.get("slug") == slug:
            return item
    return None


def daily_episodes() -> list[dict]:
    # 不做进程级缓存：每日命令写入新文件后，无需重启服务即可出现在列表里
    if not DAILY_DIR.exists():
        return []
    items = [_read(p) for p in DAILY_DIR.glob("*.json")]
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    # 期次与上下期在读取时计算：新增文章不需要改写往期文件
    total = len(items)
    decorated = []
    for i, item in enumerate(items):
        ep = dict(item)
        ep["issue"] = total - i
        ep["prev"] = items[i + 1].get("slug") if i + 1 < total else None
        ep["next"] = items[i - 1].get("slug") if i > 0 else None
        decorated.append(ep)
    return decorated


def daily_episode(slug: str) -> dict | None:
    for item in daily_episodes():
        if item.get("slug") == slug:
            return item
    return None


@lru_cache(maxsize=1)
def series() -> dict:
    if not PLAN_FILE.exists():
        return {"title": "每日拆解一个上市公司 ESG 报告和评级", "intro": "", "method": [], "disclaimer": ""}
    return _read(PLAN_FILE)["series"]


def episodes_for_codes(codes: list[str]) -> list[dict]:
    wanted = set(codes)
    return [e for e in daily_episodes() if e["code"] in wanted]


def site_stats() -> dict:
    eps = daily_episodes()
    agencies: set[str] = set()
    for ep in eps:
        agencies.update(r["agency"] for r in ep.get("ratings", []))
    return {
        "episodes": len(eps),
        "companies": len({e["code"] for e in eps}),
        "industries": len({e["industry"] for e in eps}),
        "agencies": len(agencies),
        "paths": len(learn_paths()),
        "latest": eps[0].get("date", "") if eps else "",
    }
