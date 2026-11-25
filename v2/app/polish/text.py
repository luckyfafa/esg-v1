"""叙述层公用文本工具：表格、同比句、行业指标句。"""
from __future__ import annotations

from .facts import Facts


def table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_本项暂无已填数据。_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def delta_text(f: Facts, cur: float | None, prev: float | None, unit: str,
               label: str, better: str = "lower") -> str:
    """同比表述：有上期才比较，否则如实说明；百分数不出现双重符号。"""
    if cur is None:
        return f"{label}本期未采集，本报告不作估算。"
    if unit == "%":
        cur_s = f.pct(cur)
        prev_s = f.pct(prev) if prev is not None else ""
    else:
        cur_s = f"{f.fmt(cur)} {unit}"
        prev_s = f"{f.fmt(prev)} {unit}" if prev is not None else ""
    if prev is None:
        return f"本期{label}为 **{cur_s}**，上期数据未采集，暂不列示同比变化。"
    d = f.delta(cur, prev)
    if d is None:
        return f"本期{label}为 **{cur_s}**，上期为 {prev_s}，同比变化不可比。"
    word = "下降" if d < 0 else "上升" if d > 0 else "持平"
    if d == 0:
        return f"本期{label}为 **{cur_s}**，与上期持平。"
    good = (d < 0) if better == "lower" else (d > 0)
    judge = "，方向符合管理预期" if good else "，需在下一报告期重点跟进"
    return (
        f"本期{label}为 **{cur_s}**，上期为 {prev_s}，"
        f"同比{word} **{f.pct(abs(d))}**{judge}。"
    )


def h_sentence(f: Facts, code: str, label: str | None = None) -> str:
    """行业特有指标的一句话表述；未填报返回空字符串。"""
    m = f.metric(code)
    if m is None:
        return ""
    v = f.val(code)
    if v is None:
        return ""
    name = label or m.name
    unit = m.unit or ""
    if unit == "%":
        return f"本期{name}为 **{f.pct(v)}**"
    return f"本期{name}为 **{f.fmt(v)} {unit}**".rstrip()


def join_sentences(items: list[str]) -> str:
    body = [x.rstrip("。；; ") for x in items if x]
    return "；".join(body) + "。" if body else ""
