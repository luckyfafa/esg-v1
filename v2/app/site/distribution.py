"""从一期拆解里生成可直接复制发布的社交媒体文案。

不调用外部服务，只使用文章里已经核实的字段：
公司、行业、报告标题/页数、阅读地图、评级快照、三个问题与带走一句话。
"""
from __future__ import annotations

import re

RATING_CAVEAT = "不同机构的行业调整、指标权重和数据年份不同，字母与分数不可直接比较。"
DISCLAIMER = "仅引用公开数据，不构成投资建议。"


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def _pages(ep: dict) -> str:
    report = ep.get("report") or {}
    if report.get("pages"):
        return str(report["pages"])
    match = re.search(r"共\s*(\d+)\s*页", ep.get("hook", ""))
    return match.group(1) if match else ""


def _points(ep: dict, count: int = 3) -> list[str]:
    rows = ep.get("reading_map") or []
    points = [
        f"{_one_line(row.get('section'))}（{_one_line(row.get('locator'))}）"
        for row in rows[:count]
        if row.get("section")
    ]
    return points or ["报告目录与数据表（按 PDF 页码核对）"]


def _rating_summary(ep: dict) -> str:
    meta = ep.get("rating_meta") or {}
    msci = meta.get("msci") or {}
    total = meta.get("count", len(ep.get("ratings") or []))
    if msci:
        return f"MSCI {msci.get('rating')}（{msci.get('date') or '日期未返回'}），共 {total} 家机构有公开结果"
    if total:
        return f"公开接口未返回 MSCI 结果，共 {total} 家机构有公开结果"
    return "公开接口未返回评级结果"


def _hashtags(ep: dict) -> str:
    industry = _one_line(ep.get("industry")).replace(" ", "")
    tags = ["#ESG", "#港股", "#可持续发展", "#ESG报告"]
    if industry and industry != "综合":
        tags.append(f"#{industry}")
    return " ".join(tags)


def build_posts(ep: dict, url: str) -> list[dict]:
    """返回四种平台的现成文案；url 建议带 #slug，落地到这一期。"""
    company = _one_line(ep.get("company"))
    code = _one_line(ep.get("code"))
    industry = _one_line(ep.get("industry")) or "综合"
    angle = _one_line(ep.get("angle"))
    takeaway = _one_line(ep.get("takeaway"))
    pages = _pages(ep)
    points = _points(ep)
    rating = _rating_summary(ep)
    report = ep.get("report") or {}
    title = _one_line(report.get("title")) or "ESG 报告"
    release = _one_line(report.get("release_date"))
    questions = [_one_line(q) for q in (ep.get("questions") or [])][:3]

    point_lines = "\n".join(f"· {p}" for p in points)
    page_text = f"共 {pages} 页，" if pages else ""
    release_text = f"，{release} 发布" if release else ""

    xiaohongshu = (
        f"{company} ESG 报告怎么读？先看这 {len(points)} 个位置\n\n"
        f"{angle}\n\n"
        f"{company}（{code}）这份《{title}》{page_text}我们不做全文翻译，只做定位：\n"
        f"{point_lines}\n\n"
        f"评级快照：{rating}。{RATING_CAVEAT}\n\n"
        f"带走一句：{takeaway}\n\n"
        f"完整拆解（含阅读地图和评级表）：\n{url}\n\n"
        f"{_hashtags(ep)}"
    )

    weibo = (
        f"{company}（{code}）ESG 报告拆解：{angle}。"
        f"{page_text}先定位 {len(points)} 个章节；{rating}。"
        f"评级口径不同，不能直接比较。阅读地图：{url}"
    )

    moments = (
        f"今天拆{company}（{code}）。\n"
        f"{angle}：{takeaway}\n"
        f"先看{points[0]}，再看{points[1] if len(points) > 1 else points[0]}。\n"
        f"完整拆解：{url}"
    )

    linkedin_lines = [
        f"【港股 ESG 报告拆解】{company}（{code}）｜{industry}",
        "",
        f"本期角度：{angle}",
        f"报告：《{title}》{release_text}，{page_text.rstrip('，') or '页数未标注'}。",
        f"阅读地图：{'；'.join(points)}",
        "",
        f"评级快照：{rating}。{RATING_CAVEAT}",
    ]
    if questions:
        linkedin_lines += ["", "三个可以带走的判断："]
        linkedin_lines += [f"{i + 1}. {q}" for i, q in enumerate(questions)]
    linkedin_lines += ["", f"完整拆解：{url}", DISCLAIMER]
    linkedin = "\n".join(linkedin_lines)

    return [
        {"key": "xiaohongshu", "label": "小红书 / 公众号", "hint": "分段 + 话题标签", "text": xiaohongshu},
        {"key": "weibo", "label": "微博 / 短帖", "hint": "短文案，直接发", "text": weibo},
        {"key": "moments", "label": "朋友圈 / 社群", "hint": "短句 + 链接", "text": moments},
        {"key": "linkedin", "label": "LinkedIn / 专业版", "hint": "结构化 + 三个问题", "text": linkedin},
    ]
