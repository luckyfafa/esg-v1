"""极简 Markdown 渲染，仅覆盖报告用到的语法，供打印视图与分享页使用。"""
from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)", r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', text)
    return text


def _table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    if body and all(re.fullmatch(r":?-{2,}:?", c) for c in body[0] if c):
        body = body[1:]
    out = ["<table><thead><tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue
        if stripped.startswith("### "):
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            out.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
        elif re.match(r"^[-*] ", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*] ", lines[i].strip()):
                items.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif re.fullmatch(r"-{3,}", stripped):
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    return "\n".join(out)


PRINT_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; background: #f2f2ee; color: #161a17;
  font-family: "PingFang HK", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }
.page { width: 210mm; min-height: 297mm; margin: 16px auto; background: #fff;
  padding: 20mm 18mm; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
.brandline { display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 2px solid #0F5132; padding-bottom: 8px; margin-bottom: 18px; }
.brandline b { color: #0F5132; font-size: 13pt; letter-spacing: .02em; }
.brandline span { font-size: 8.5pt; color: #6b726c; }
h1 { font-size: 19pt; color: #0F5132; margin: 0 0 6mm; }
h2 { font-size: 13.5pt; margin: 7mm 0 3mm; padding-bottom: 1.6mm; border-bottom: 1px solid #e2e3d8; }
h3 { font-size: 11pt; margin: 5mm 0 2mm; }
p { font-size: 9.8pt; line-height: 1.75; margin: 0 0 2.6mm; }
ul { margin: 0 0 3mm; padding-left: 6mm; }
li { font-size: 9.8pt; line-height: 1.7; margin-bottom: 1mm; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm; font-size: 8.6pt; }
th, td { border: 1px solid #dfe2d8; padding: 1.8mm 2mm; text-align: left; vertical-align: top; line-height: 1.5; }
th { background: #eff5f1; color: #0F5132; font-weight: 700; }
code { background: #f4f5ee; padding: 0 1mm; border-radius: 2px; font-size: 8.8pt; }
blockquote { margin: 0 0 3mm; padding: 2mm 3mm; border-left: 2px solid #0F5132; background: #f7f9f4; font-size: 9pt; }
hr { border: 0; border-top: 1px solid #e2e3d8; margin: 5mm 0; }
.foot { margin-top: 8mm; padding-top: 3mm; border-top: 1px solid #e2e3d8; font-size: 8pt; color: #6b726c; }
.toolbar { width: 210mm; margin: 12px auto 0; display: flex; gap: 8px; justify-content: flex-end; }
.toolbar button { background: #0F5132; color: #fff; border: 0; border-radius: 6px; padding: 7px 14px;
  font-size: 12px; cursor: pointer; }
@media print {
  body { background: #fff; }
  .page { width: auto; min-height: auto; margin: 0; padding: 0; box-shadow: none; }
  .toolbar { display: none; }
  @page { size: A4; margin: 16mm 14mm; }
}
"""


def print_page(title: str, subtitle: str, body_md: str) -> str:
    _desc = (subtitle or "ESG 智能报告系统生成的只读报告").strip()[:120]
    return f"""<!DOCTYPE html>
<html lang="zh-HK"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(_desc)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="ESG 智能报告系统">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(_desc)}">
<style>{PRINT_STYLE}</style></head>
<body>
<div class="toolbar"><button onclick="window.print()">打印 / 另存为 PDF</button></div>
<div class="page">
  <div class="brandline"><b>ESG 智能报告系统 V3</b><span>{html.escape(subtitle)}</span></div>
  {render(body_md)}
  <div class="foot">本文件由系统按公开数据与采集数据生成，用于内部讨论与底稿整理；
  不构成审计、鉴证或投资意见。关键数值请对照原始报告复核。</div>
</div>
</body></html>"""
