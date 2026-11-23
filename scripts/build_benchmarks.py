"""批量生成 7 个行业的实测基线。

做什么：
  1. 读取 peers.json 里的「行业 → 上市公司列表」
  2. 每家公司的 ESG 报告 PDF → 抽取 KPI（已有缓存则直接复用）
  3. 按行业用 compute_bands_from_kpis() 算 P25 / 中位 / P75
  4. 缺失指标用参考区间补齐，并逐项标注来源
  5. 写入 data/cache/benchmarks/{industry}.json，报告会自动读取

用法（在项目根目录执行）：
    python scripts/build_benchmarks.py --config scripts/peers.json
    python scripts/build_benchmarks.py --config scripts/peers.json --industry retail
    python scripts/build_benchmarks.py --config scripts/peers.json --dry-run

peers.json 格式：
{
  "retail": [
    {"name": "某某集团", "code": "01999", "year": 2024, "pdf": "https://.../report.pdf"},
    {"name": "某某国际", "code": "02331", "year": 2024, "pdf": "D:/reports/2331_2024.pdf"}
  ]
}

注意：
  - 只对当前 KPI schema 有字段的 5 项做实测：E15 / E07 / S07 / S15 / S14。
  - E21（废弃物回收率）、S09（女性管理者）、G09（反腐培训）、S19_COV（供应商 ESG 覆盖）
    现有字段无法准确计算，脚本会用参考区间兜底并标注「本行业未实测」。
  - 每项实测至少 3 个有效样本，否则该指标仍用参考区间。
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.benchmark import (  # noqa: E402
    CORE_CODES,
    METRIC_NAMES,
    REFERENCE_BANDS,
    compute_bands_from_kpis,
    save_bands,
)
from app.config import settings  # noqa: E402
from app.extractor import extract_kpis_from_pdf, save_kpis  # noqa: E402
from app.schemas import BenchmarkBand, ESGKPIs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_benchmarks")

# 当前 KPI schema 能准确支撑的指标；其余走参考区间
MEASURABLE = {"E15", "E07", "S07", "S15", "S14"}


def _kpi_cache_path(code: str, year: int) -> Path:
    return settings.cache_dir / "kpis" / f"{code}_{year}.json"


def _download(url: str, dest_dir: Path) -> Path:
    name = Path(urlparse(url).path).name or "report.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    target = dest_dir / name
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (ESG benchmark builder)"})
    with urlopen(req, timeout=120) as resp, target.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    logger.info("已下载 %s → %s", url, target)
    return target


def _load_or_extract(peer: dict, tmp_dir: Path) -> ESGKPIs:
    code = str(peer["code"]).zfill(5)
    year = int(peer["year"])
    cache = _kpi_cache_path(code, year)
    if cache.exists():
        logger.info("复用缓存 %s", cache.name)
        return ESGKPIs.model_validate_json(cache.read_text(encoding="utf-8"))

    raw = str(peer["pdf"]).strip()
    if raw.startswith(("http://", "https://")):
        pdf_path = _download(raw, tmp_dir)
    else:
        pdf_path = Path(raw)
        if not pdf_path.is_absolute():
            pdf_path = (ROOT / pdf_path).resolve()

    kpis = extract_kpis_from_pdf(
        pdf_path=pdf_path,
        company_name=peer.get("name") or code,
        stock_code=code,
        report_year=year,
    )
    saved = save_kpis(kpis)
    logger.info("抽取完成并缓存 %s", saved.name)
    return kpis


def _fallback_band(code: str) -> BenchmarkBand:
    name, unit, direction = METRIC_NAMES[code]
    ref = REFERENCE_BANDS[code]
    return BenchmarkBand(
        code=code,
        unit=unit,
        p25=ref["p25"],
        median=ref["median"],
        p75=ref["p75"],
        direction=direction,
        peer_count=0,
        source="参考区间（本行业未实测）",
    )


def build_industry(industry: str, peers: list[dict], dry_run: bool = False) -> dict:
    tmp_dir = Path(tempfile.mkdtemp(prefix="esg_bench_"))
    kpis_list: list[ESGKPIs] = []
    failures: list[str] = []

    try:
        for peer in peers:
            label = f"{peer.get('name', '?')}({peer.get('code', '?')})"
            if dry_run:
                code = str(peer["code"]).zfill(5)
                cached = _kpi_cache_path(code, int(peer["year"])).exists()
                logger.info("[dry-run] %s 缓存=%s", label, cached)
                continue
            try:
                kpis_list.append(_load_or_extract(peer, tmp_dir))
            except Exception as exc:  # noqa: BLE001
                logger.warning("跳过 %s：%s", label, exc)
                failures.append(f"{label}: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if dry_run:
        return {"industry": industry, "dry_run": True, "peers": len(peers)}

    measured = compute_bands_from_kpis(kpis_list)
    bands: list[BenchmarkBand] = []
    for code in CORE_CODES:
        if code in measured:
            bands.append(measured[code])
        else:
            bands.append(_fallback_band(code))

    if not dry_run:
        path = save_bands(industry, bands)
        logger.info("已写入 %s", path)

    measured_codes = sorted(measured)
    summary = {
        "industry": industry,
        "peers_ok": len(kpis_list),
        "peers_failed": len(failures),
        "measured": {
            c: {
                "peer_count": measured[c].peer_count,
                "median": measured[c].median,
                "source": measured[c].source,
            }
            for c in measured_codes
        },
        "fallback": [c for c in CORE_CODES if c not in measured],
        "failures": failures,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生成行业实测基线")
    parser.add_argument("--config", required=True, help="peers.json 路径")
    parser.add_argument("--industry", help="只跑某个行业 key")
    parser.add_argument("--dry-run", action="store_true", help="只检查缓存与文件，不抽取不写盘")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (ROOT / config_path).resolve()
    universe = json.loads(config_path.read_text(encoding="utf-8"))
    universe = {k: v for k, v in universe.items() if not k.startswith("_")}

    if args.industry:
        if args.industry not in universe:
            logger.error("配置里没有行业 %s，可选：%s", args.industry, ", ".join(universe))
            return 2
        universe = {args.industry: universe[args.industry]}

    summaries = []
    for industry, peers in universe.items():
        if not peers:
            logger.warning("行业 %s 没有配置公司，跳过", industry)
            continue
        logger.info("=== %s：%d 家公司 ===", industry, len(peers))
        summaries.append(build_industry(industry, peers, dry_run=args.dry_run))

    print("\n===== 汇总 =====")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    missing = [c for c in CORE_CODES if c not in MEASURABLE]
    if missing:
        print(
            "\n提示：以下指标现有 KPI 字段无法准确实测，已用参考区间兜底："
            + ", ".join(f"{c} {METRIC_NAMES[c][0]}" for c in missing)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
