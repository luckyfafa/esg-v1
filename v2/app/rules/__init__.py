"""HKEX 附录 C2 规则库：结构化条文 + 适用性判定。

设计原则：
- 条文内容来自港交所官方页面，不加工、不推断；
- 适用性由 issuer_type / 财政年度 / 是否大型股 三个维度确定；
- 同一规则有多条适用段时，取最严格的责任（强制 > 不遵守就解释 > 自愿）；
- 规则状态默认 draft，专业复核后方可用于对外交付。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

RULES_FILE = Path(__file__).with_name("hkex_esg_c2.json")

Obligation = Literal["mandatory", "comply_or_explain", "voluntary"]
ObligationRank = {"mandatory": 3, "comply_or_explain": 2, "voluntary": 1}
IssuerType = Literal["main_board", "gem", "largecap"]


class RuleApplicability(BaseModel):
    """规则在某类发行人、某个财政年度起的披露责任。"""

    issuer_type: IssuerType
    fy_from: Optional[int] = None  # None 表示不设起始年度
    obligation: Obligation


class Rule(BaseModel):
    """单条披露规则。"""

    id: str
    part: Literal["B", "C", "D"]
    paragraph: str
    title: str
    requirement: str
    applicability: list[RuleApplicability] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    status: Literal["draft", "reviewed"] = "draft"


class RuleSet(BaseModel):
    standard: str
    version: str
    source_url: str = ""
    coverage_note: str = ""
    rules: list[Rule] = Field(default_factory=list)


@lru_cache(maxsize=1)
def load_ruleset() -> RuleSet:
    """读取并校验规则库；文件缺失或格式错误时直接抛错，避免静默返回空规则。"""
    raw = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    return RuleSet(**raw)


def _match(rule: Rule, issuer_type: str, fy: int, is_largecap: bool) -> Optional[Obligation]:
    """返回该规则对该发行人的有效责任；不适用则返回 None。"""
    matched: list[Obligation] = []
    for seg in rule.applicability:
        if seg.issuer_type == "largecap":
            if not is_largecap:
                continue
        elif seg.issuer_type != issuer_type:
            continue
        if seg.fy_from is not None and fy < seg.fy_from:
            continue
        matched.append(seg.obligation)
    if not matched:
        return None
    return max(matched, key=lambda o: ObligationRank[o])


def applicable_rules(
    issuer_type: str,
    fy: int,
    is_largecap: bool = False,
) -> list[dict]:
    """按发行人类型与财政年度筛选适用规则，返回规则与有效责任。"""
    if issuer_type not in ("main_board", "gem"):
        raise ValueError("issuer_type 仅支持 main_board 或 gem")
    out: list[dict] = []
    for rule in load_ruleset().rules:
        obligation = _match(rule, issuer_type, fy, is_largecap)
        if obligation is None:
            continue
        item = rule.model_dump()
        item["effective_obligation"] = obligation
        out.append(item)
    return out
