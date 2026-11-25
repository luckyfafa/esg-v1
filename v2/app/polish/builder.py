"""底稿 → 润色版：把非上市公司的诊断底稿改写成上市公司风格的 ESG 报告。

底稿原样保留（V1 的 build_client_report 产物），润色版只做章节重组、
版式包装与文字叙述，数字全部来自 Facts，不新增任何外部信息。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.benchmark import analyze, normalize_industry
from app.client_data import derive_metrics, load_client_excel
from app.client_report import build_client_report
from app.materiality import assess, suggest_from_analysis

from ..config import settings
from .content_core import (
    appendix_data,
    appendix_glossary,
    appendix_index,
    appendix_prepare,
    appendix_regulation,
    chapter_environment,
    chapter_ethics,
    chapter_governance,
    chapter_industry,
    chapter_kpis,
    chapter_materiality,
    chapter_social,
    chapter_targets,
)
from .content_front import about_report, chapter_company, cover_data, management_message
from .facts import Facts
from .industries import get_pack
from .render import PAGEBREAK, md_to_blocks, render_html, to_markdown
from .verify import verify


@dataclass
class PolishResult:
    company: str
    year: int
    draft_md: str
    polished_md: str
    html: str
    verify: dict
    sections: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def as_dict(self, include_text: bool = True) -> dict:
        out = {
            "company": self.company,
            "year": self.year,
            "sections": self.sections,
            "verify": self.verify,
            "files": self.files,
            "meta": self.meta,
        }
        if include_text:
            out["draft_md"] = self.draft_md
            out["polished_md"] = self.polished_md
        return out


def _safe(name: str) -> str:
    return "".join(ch for ch in name if ch not in '\\/:*?"<>|').replace(" ", "_")


def _assemble_body(
    facts: Facts,
    pack,
    source_name: str,
    materiality: dict,
    suggestions: dict,
) -> str:
    parts = [
        about_report(facts, pack, source_name),
        management_message(facts, pack),
        chapter_company(facts, pack),
        chapter_governance(facts, pack),
        chapter_materiality(facts, pack, materiality, suggestions),
        chapter_environment(facts, pack),
        chapter_social(facts, pack),
        chapter_ethics(facts, pack),
        chapter_targets(facts, pack),
        chapter_industry(facts, pack),
        chapter_kpis(facts, pack, materiality),
        appendix_index(facts),
        appendix_data(facts, source_name),
        appendix_glossary(facts, pack),
        appendix_regulation(facts, pack),
        appendix_prepare(facts, source_name),
    ]
    return f"\n\n{PAGEBREAK}\n\n".join(parts)


def _auto_materiality(facts: Facts, industry_key: str) -> tuple[dict, dict]:
    """按填报数据与行业对标自动生成双重重要性评分（可在调用方覆盖）。"""
    suggestion = suggest_from_analysis(facts.analysis)
    suggestions = suggestion.get("suggestions") or {}
    result = assess({
        "industry": industry_key,
        "company_name": facts.company,
        "assessor": "系统依据填报数据自动建议（待企业复核）",
        "scores": suggestions,
    })
    return result, suggestions


def polish_excel(
    path: Path | str,
    industry: str | None = None,
    materiality: dict | None = None,
    out_dir: Path | None = None,
    persist: bool = True,
) -> PolishResult:
    """读取采集表 → 生成底稿与润色版。"""
    p = Path(path)
    client = load_client_excel(p)
    key = normalize_industry(industry or client.profile.industry)
    analysis = analyze(client, key, derive_metrics(client))

    draft = build_client_report(analysis, use_llm_narrative=False, materiality=materiality)

    facts = Facts(analysis)
    pack = get_pack(key, client.profile.industry)
    auto_mt, suggestions = _auto_materiality(facts, key)
    mt = materiality or auto_mt
    source_name = p.name
    body_md = _assemble_body(facts, pack, source_name, mt, suggestions)
    blocks = md_to_blocks(body_md)
    sections = [b.text for b in blocks if b.kind == "h" and b.level == 2]

    cover = cover_data(facts, pack)
    cover["year"] = str(facts.profile.report_year)
    footer = (
        f"{facts.company} · {facts.profile.report_year} 年度 ESG 报告（润色版）· "
        "本报告依据企业填报数据整理，未经第三方审阅或鉴证"
    )
    html = render_html(blocks, cover, footer)

    toc = "\n".join(f"{i}. {t}" for i, t in enumerate(sections, 1))
    for i in range(1, len(sections) + 1):
        facts._register(str(i))
    polished_md = (
        f"# {facts.company}\n\n"
        f"## 环境、社会及管治报告 {facts.profile.report_year}\n\n"
        f"> 润色版 · 依据企业填报数据整理 · 底稿原文见同名「底稿」文件\n\n"
        f"{PAGEBREAK}\n\n## 目录\n\n{toc}\n\n"
        f"{PAGEBREAK}\n\n{body_md}"
    )

    check = verify(polished_md, facts)
    meta = {
        "company": facts.company,
        "industry": facts.industry_label,
        "industry_key": key,
        "pack": pack.label,
        "year": facts.profile.report_year,
        "completeness_pct": facts.completeness_pct,
        "gaps": len(facts.gaps),
        "positions": facts.position_counts,
        "materiality_counts": mt.get("counts") if isinstance(mt, dict) else None,
        "source_file": source_name,
        "generated_at": date.today().isoformat(),
        "sections": len(sections),
        "draft_chars": len(draft.markdown),
        "polished_chars": len(polished_md),
        "verify_ok": check["ok"],
    }

    result = PolishResult(
        company=facts.company,
        year=facts.profile.report_year,
        draft_md=draft.markdown,
        polished_md=polished_md,
        html=html,
        verify=check,
        sections=sections,
        meta=meta,
    )

    if persist:
        target_dir = out_dir or (settings.data_dir / "reports" / "polished")
        target_dir.mkdir(parents=True, exist_ok=True)
        base = _safe(f"{facts.company}_{facts.profile.report_year}")
        files = {
            "draft": target_dir / f"{base}_底稿.md",
            "polished": target_dir / f"{base}_润色版.md",
            "html": target_dir / f"{base}_润色版.html",
            "meta": target_dir / f"{base}_润色版.json",
        }
        files["draft"].write_text(draft.markdown, encoding="utf-8")
        files["polished"].write_text(polished_md, encoding="utf-8")
        files["html"].write_text(html, encoding="utf-8")
        files["meta"].write_text(
            json.dumps({**meta, "verify": check}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result.files = {k: str(v) for k, v in files.items()}

    return result


def polish_bytes(
    content: bytes,
    filename: str,
    industry: str | None = None,
    materiality: dict | None = None,
) -> PolishResult:
    """API 用：把上传的字节落到 V2 上传目录后生成。"""
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "upload.xlsx").suffix.lower() or ".xlsx"
    target = upload_dir / f"polish_{date.today().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    target.write_bytes(content)
    try:
        return polish_excel(target, industry=industry, materiality=materiality)
    finally:
        target.unlink(missing_ok=True)
