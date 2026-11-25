"""数字一致性校验：润色版不得出现底稿之外的数字。

两道检查：
1. 白名单：润色版中每个数字都必须能在事实表（填报值 / 派生值 / 合计 / 章节号）中找到；
2. 核心指标：环境、社会、治理三组核心指标的填报值，必须以原格式出现在润色版中。
"""
from __future__ import annotations

import re

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(s: str) -> str:
    s = s.replace(",", "").replace("，", "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def extract_numbers(text: str) -> set[str]:
    out = set()
    for m in NUM_RE.findall(text):
        n = _norm(m)
        if n:
            out.add(n)
    return out


def verify(polished_md: str, facts) -> dict:
    found = extract_numbers(polished_md)
    allowed = {_norm(x) for x in facts.allowed_numbers}
    unmatched = sorted(found - allowed)

    missing: list[str] = []
    for spec in ():  # 占位，便于未来扩展更多核心指标组
        pass
    from .facts import E_CORE, G_CORE, S_CORE
    from .facts import Facts as _Facts  # noqa: F401

    for spec in (E_CORE, S_CORE, G_CORE):
        for r in facts.kpi(spec):
            if r.value is None:
                continue
            formatted = facts.fmt(r.value)
            token = _norm(formatted)
            if token not in found:
                missing.append(f"{r.code} {r.name}={formatted}")

    return {
        "ok": not unmatched and not missing,
        "numbers_checked": len(found),
        "unmatched": unmatched[:30],
        "missing_core_values": missing[:30],
    }
