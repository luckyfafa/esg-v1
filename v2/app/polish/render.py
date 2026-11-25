"""润色版排版：Markdown 与 A4 打印版 HTML。

版式参考样本报告：封面 → 目录 → 正文章节（每章新起一页）→ 数据表 → 附录。
只负责版式，不产生任何内容或数字。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

PAGEBREAK = "<!-- pagebreak -->"


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    anchor: str = ""


def _inline(text: str) -> str:
    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md_to_blocks(md: str) -> list[Block]:
    lines = md.splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s == PAGEBREAK:
            blocks.append(Block(kind="pagebreak"))
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            blocks.append(Block(kind="h", level=len(m.group(1)), text=m.group(2).strip()))
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in rows[1] if c):
                rows = [rows[0]] + rows[2:]
            blocks.append(Block(kind="table", headers=rows[0] if rows else [], rows=rows[1:]))
            continue
        if s.startswith("> "):
            blocks.append(Block(kind="quote", text=s[2:]))
            i += 1
            continue
        if re.match(r"^[-*]\s+", s):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            blocks.append(Block(kind="ul", items=items))
            continue
        if re.match(r"^\d+\.\s+", s):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append(Block(kind="ol", items=items))
            continue
        if re.fullmatch(r"-{3,}", s):
            blocks.append(Block(kind="hr"))
            i += 1
            continue
        # 段落：连续非空行合并
        para = [s]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "|", ">", "- ", "* "))
                    or re.match(r"^\d+\.\s+", nxt) or nxt == PAGEBREAK):
                break
            para.append(nxt)
            i += 1
        blocks.append(Block(kind="p", text="".join(para)))
    return blocks


def to_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for b in blocks:
        if b.kind == "h":
            out.append("#" * b.level + " " + b.text)
        elif b.kind == "p":
            out.append(b.text)
        elif b.kind == "quote":
            out.append("> " + b.text)
        elif b.kind == "ul":
            out.extend(f"- {x}" for x in b.items)
        elif b.kind == "ol":
            out.extend(f"{i}. {x}" for i, x in enumerate(b.items, 1))
        elif b.kind == "table":
            out.append("| " + " | ".join(b.headers) + " |")
            out.append("|" + "|".join(["---"] * len(b.headers)) + "|")
            out.extend("| " + " | ".join(r) + " |" for r in b.rows)
        elif b.kind == "hr":
            out.append("---")
        elif b.kind == "pagebreak":
            out.append(PAGEBREAK)
        out.append("")
    return "\n".join(out).strip() + "\n"


def _slug(idx: int, level: int) -> str:
    return f"sec-{level}-{idx}"


def render_html(blocks: list[Block], cover: dict, footer: str) -> str:
    """生成 A4 打印版 HTML；目录由二级标题自动生成。"""
    # 目录
    toc: list[tuple[str, str]] = []
    counter = 0
    for idx, b in enumerate(blocks):
        if b.kind == "h" and b.level == 2:
            counter += 1
            b.anchor = _slug(counter, 2)
            toc.append((b.anchor, b.text))

    body: list[str] = []
    for b in blocks:
        if b.kind == "h":
            tag = f"h{b.level}"
            anchor = f' id="{b.anchor}"' if b.anchor else ""
            body.append(f"<{tag}{anchor}>{_inline(b.text)}</{tag}>")
        elif b.kind == "p":
            body.append(f"<p>{_inline(b.text)}</p>")
        elif b.kind == "quote":
            body.append(f"<blockquote>{_inline(b.text)}</blockquote>")
        elif b.kind == "ul":
            body.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in b.items) + "</ul>")
        elif b.kind == "ol":
            body.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in b.items) + "</ol>")
        elif b.kind == "table":
            head = "".join(f"<th>{_inline(c)}</th>" for c in b.headers)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in b.rows
            )
            body.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        elif b.kind == "hr":
            body.append("<hr>")
        elif b.kind == "pagebreak":
            body.append('<div class="pb"></div>')

    toc_html = "".join(f'<li><a href="#{a}">{_inline(t)}</a></li>' for a, t in toc)
    meta = "".join(
        f"<div><dt>{_inline(k)}</dt><dd>{_inline(v)}</dd></div>"
        for k, v in [
            ("所属行业", cover.get("industry", "")),
            ("组织边界", cover.get("boundary", "")),
            ("编制依据", cover.get("basis", "")),
        ] if v
    )

    return f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(cover['company'])} {html.escape(cover['title'])} {cover['year']}</title>
<meta name="robots" content="noindex,nofollow">
<style>{CSS}</style>
</head>
<body>
<div class="toolbar">
  <span class="hint">打印时选择「另存为 PDF」，纸张 A4、边距默认、勾选背景图形</span>
  <button onclick="window.print()">打印 / 另存为 PDF</button>
</div>
<div class="page">
  <section class="cover">
    <div class="band"></div>
    <div class="kicker">{html.escape(cover['tag'])}</div>
    <h1>{_inline(cover['company'])}</h1>
    <div class="ctitle">{html.escape(cover['title'])}</div>
    <div class="cen">{html.escape(cover['title_en'])}</div>
    <div class="year">{cover['year']}</div>
    <dl class="meta">{meta}</dl>
    <div class="coverfoot">本报告依据企业填报数据整理，未经第三方审阅或鉴证</div>
  </section>

  <section class="toc">
    <h2 class="toc-title">目录</h2>
    <ol>{toc_html}</ol>
  </section>

  <main>
  {''.join(body)}
  </main>

  <div class="foot">{html.escape(footer)}</div>
</div>
</body>
</html>
"""


CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #e9ece6; color: #1b2320; }
body { font-family: "PingFang HK","PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif; }
.toolbar { width: 210mm; margin: 14px auto 0; display: flex; align-items: center;
  justify-content: flex-end; gap: 12px; }
.toolbar .hint { font-size: 12px; color: #66756e; }
.toolbar button { background: #0F5132; color: #fff; border: 0; border-radius: 6px;
  padding: 8px 16px; font-size: 13px; cursor: pointer; }
.page { width: 210mm; min-height: 297mm; margin: 14px auto 40px; background: #fff;
  padding: 20mm 18mm 16mm; box-shadow: 0 4px 24px rgba(15,81,50,.12); }

/* 封面 */
.cover { position: relative; min-height: 245mm; padding-top: 18mm; page-break-after: always; }
.cover .band { position: absolute; left: -18mm; top: 0; width: 8mm; height: 240mm;
  background: linear-gradient(180deg,#0F5132,#2E7D5B 55%,#C9A227); }
.cover .kicker { font-size: 10pt; letter-spacing: .18em; color: #0F5132; margin-bottom: 22mm; }
.cover h1 { font-size: 30pt; line-height: 1.25; margin: 0 0 8mm; color: #122a1f;
  font-weight: 700; letter-spacing: .01em; }
.cover .ctitle { font-size: 20pt; color: #0F5132; font-weight: 600; margin-bottom: 4mm; }
.cover .cen { font-size: 8.5pt; letter-spacing: .22em; color: #8a9a92; margin-bottom: 26mm; }
.cover .year { font-size: 64pt; font-weight: 800; color: #E7EDE7; line-height: 1;
  margin: 0 0 18mm; letter-spacing: -.02em; }
.cover .meta { border-top: 1px solid #d8e0d8; padding-top: 6mm; margin: 0; }
.cover .meta div { display: flex; gap: 6mm; padding: 1.6mm 0; font-size: 9.5pt; }
.cover .meta dt { width: 24mm; color: #7b8a82; margin: 0; }
.cover .meta dd { margin: 0; color: #253530; }
.cover .coverfoot { position: absolute; bottom: 0; left: 0; right: 0; font-size: 8pt;
  color: #8a9a92; border-top: 1px solid #d8e0d8; padding-top: 3mm; }

/* 目录 */
.toc { page-break-after: always; }
.toc-title { font-size: 16pt; color: #0F5132; border-bottom: 2px solid #0F5132;
  padding-bottom: 3mm; margin: 4mm 0 8mm; }
.toc ol { list-style: none; padding: 0; margin: 0; }
.toc li { font-size: 10.5pt; padding: 2.6mm 0; border-bottom: 1px dotted #dbe3d9; }
.toc a { color: #1b2320; text-decoration: none; }

/* 正文 */
main { counter-reset: chapter; }
h2 { font-size: 15pt; color: #0F5132; margin: 0 0 4mm; padding: 0 0 2.5mm;
  border-bottom: 1.5px solid #C9D8C9; page-break-before: always; page-break-after: avoid; }
h2::before { content: ""; }
h3 { font-size: 11.5pt; color: #1d4d38; margin: 6mm 0 2.5mm; page-break-after: avoid; }
p { font-size: 9.8pt; line-height: 1.85; margin: 0 0 2.8mm; text-align: justify; }
ul, ol { margin: 0 0 3mm; padding-left: 6mm; }
li { font-size: 9.8pt; line-height: 1.75; margin-bottom: 1.2mm; }
strong { color: #122a1f; }
hr { border: 0; border-top: 1px solid #e2e6dd; margin: 6mm 0; }
blockquote { margin: 0 0 3mm; padding: 2.5mm 3.5mm; background: #f4f8f2;
  border-left: 3px solid #0F5132; font-size: 9.2pt; color: #3b4b43; }

table { width: 100%; border-collapse: collapse; margin: 2.5mm 0 5mm; font-size: 8.6pt;
  page-break-inside: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #dde3d9; padding: 1.7mm 2mm; text-align: left;
  vertical-align: top; line-height: 1.55; }
th { background: #eef4ec; color: #0F5132; font-weight: 700; }
tbody tr:nth-child(even) { background: #fafbf8; }

.pb { page-break-after: always; height: 0; }
.foot { margin-top: 8mm; padding-top: 3mm; border-top: 1px solid #dfe5dc;
  font-size: 7.8pt; color: #7b8a82; }

@media print {
  html, body { background: #fff; }
  .toolbar { display: none; }
  .page { width: auto; min-height: auto; margin: 0; padding: 0; box-shadow: none; }
  @page { size: A4; margin: 16mm 15mm 14mm; }
}
"""
