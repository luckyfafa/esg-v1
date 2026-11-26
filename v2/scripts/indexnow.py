"""IndexNow 命令行：补推全部 URL 或指定 URL。

    python -m v2.scripts.indexnow --all
    python -m v2.scripts.indexnow --url https://esg-v1-0uyh.onrender.com/daily/2026-11-26-01211
"""
from __future__ import annotations

import argparse
import sys

from ..app.indexnow import site_urls, submit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="向 IndexNow 提交 URL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="提交站点全部可索引 URL")
    group.add_argument("--url", action="append", help="提交指定 URL，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要提交的 URL")
    args = parser.parse_args(argv)

    urls = site_urls() if args.all else list(args.url or [])
    if args.dry_run:
        print(f"待提交 {len(urls)} 条：")
        for url in urls:
            print(f"  {url}")
        return 0

    ok, status, detail = submit(urls)
    if ok:
        print(f"[INDEXNOW] 已提交 {len(urls)} 条，HTTP {status}")
        return 0
    print(f"[INDEXNOW] 提交失败（HTTP {status or '无响应'}）：{detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())