"""把内容渲染成带 SEO 元数据与结构化数据的静态 HTML。"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from html import escape
from xml.sax.saxutils import escape as xml_escape

from . import content, distribution

SITE_NAME = "ESG 智能报告系统"
SITE_TAGLINE = "港股 ESG 报告 · 公开评级 · 可复用方法"


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _jsonld(obj) -> str:
    payload = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def _short(text: str, limit: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ext_link(url: str, label: str, cls: str = "") -> str:
    class_attr = f' class="{cls}"' if cls else ""
    return (
        f'<a href="{esc(url)}"{class_attr} target="_blank" rel="noopener noreferrer">'
        f"{esc(label)}</a>"
    )


def layout(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    base: str,
    active: str = "",
    jsonld: list | None = None,
    og_type: str = "website",
    published: str | None = None,
) -> str:
    nav_items = [
        ("home", "/", "工具首页"),
        ("learn", "/learn", "学习中心"),
        ("daily", "/daily", "报告拆解"),
    ]
    nav = "".join(
        f'<a href="{href}" class="{"is-active" if key == active else ""}">{esc(label)}</a>'
        for key, href, label in nav_items
    )
    extra_meta = ""
    if published:
        extra_meta += f'\n<meta property="article:published_time" content="{esc(published)}">'
    scripts = "\n".join(_jsonld(x) for x in (jsonld or []))
    return f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:locale" content="zh_CN">
<meta property="og:type" content="{esc(og_type)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{esc(base)}/ui/og-default.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="ESG 智能报告系统：港股报告拆解、公开评级与方法">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{esc(base)}/ui/og-default.png">
<link rel="alternate" type="application/rss+xml" title="报告拆解 RSS" href="{esc(base)}/daily/feed.xml">
<link rel="stylesheet" href="/ui/site.css?v=20261125b">{extra_meta}
{scripts}
</head>
<body>
<a class="skip" href="#main">跳到正文</a>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="brand" href="/">
      <strong>ESG 智能报告系统</strong>
      <span>V2 · 港股报告与评级</span>
    </a>
    <nav class="site-nav" aria-label="主导航">{nav}<a href="/daily/feed.xml" class="rss">RSS</a></nav>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap footer-inner">
    <div>
      <strong>{esc(SITE_NAME)}</strong>
      <p>{esc(SITE_TAGLINE)}。报告取自港交所披露易，评级只引用公开数据，不构成投资或合规建议。</p>
    </div>
    <div class="footer-links">
      <a href="/">工具首页</a>
      <a href="/learn">学习中心</a>
      <a href="/daily">报告拆解</a>
      <a href="/daily/feed.xml">RSS 订阅</a>
      <a href="/sitemap.xml">站点地图</a>
    </div>
  </div>
</footer>
</body>
</html>"""


def _breadcrumb(items: list[tuple[str, str]]) -> tuple[str, dict]:
    links = "".join(
        f'<a href="{esc(url)}">{esc(label)}</a><span aria-hidden="true">/</span>'
        for label, url in items[:-1]
    )
    html = f'<nav class="breadcrumb" aria-label="面包屑">{links}<span>{esc(items[-1][0])}</span></nav>'
    data = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": label, "item": url if url.startswith("http") else None}
            for i, (label, url) in enumerate(items)
        ],
    }
    for item in data["itemListElement"]:
        if item["item"] is None:
            item.pop("item")
    return html, data


def _rating_chip(ep: dict) -> str:
    msci = ep.get("rating_meta", {}).get("msci")
    if not msci:
        return '<span class="chip muted">无 MSCI 公开结果</span>'
    return f'<span class="chip">MSCI <strong>{esc(msci["rating"])}</strong> · {esc(msci["date"])}</span>'


def _episode_toc(ep: dict) -> str:
    d = date.fromisoformat(ep["date"])
    return f"""<a class="toc-item" href="#{esc(ep['slug'])}">
  <span class="toc-date">{d.month} 月 {d.day:02d} 日</span>
  <strong>{esc(ep['company'])}</strong>
  <span class="toc-angle">{esc(ep['angle'])}</span>
</a>"""


def render_learn_index(base: str) -> str:
    stats = content.site_stats()
    paths = content.learn_paths()
    episodes = content.daily_episodes()[:3]
    cards = "".join(
        f"""<a class="path-card" href="/learn/{esc(p['slug'])}">
  <span class="path-index">0{esc(p['order'])}</span>
  <h3>{esc(p['title'])}</h3>
  <p>{esc(p['subtitle'])}</p>
  <div class="card-meta"><span>{esc(p['duration'])}</span><span>{len(p['modules'])} 个模块</span></div>
  <span class="card-cta">查看路径 →</span>
</a>"""
        for p in paths
    )
    latest = "".join(_episode_row(e) for e in episodes)
    body = f"""
<section class="hero">
  <p class="kicker">给零背景转行者与 ESG 咨询新人</p>
  <h1>学会读一份上市公司 ESG 报告，<br>并说清它为什么被这样评级。</h1>
  <p class="lead">五条学习路径，覆盖核心知识、沟通与英语、工具、作品集与证书；每日拆解栏目用真实的港股报告做练习，报告来自港交所披露易，评级只引用公开数据。</p>
  <div class="hero-actions">
    <a class="btn primary" href="/daily">从今日拆解开始</a>
    <a class="btn ghost" href="/learn/esg-core">看核心知识路径</a>
  </div>
  <dl class="hero-stats">
    <div><dt>{stats['paths']}</dt><dd>条学习路径</dd></div>
    <div><dt>{stats['episodes']}</dt><dd>期报告拆解</dd></div>
    <div><dt>{stats['companies']}</dt><dd>家港股公司</dd></div>
    <div><dt>{stats['agencies']}</dt><dd>家评级机构</dd></div>
  </dl>
</section>

<section class="section">
  <div class="section-head"><h2>五条路径，按顺序走</h2><p>每条路径都有明确产出，不做无结果的阅读。</p></div>
  <div class="path-grid">{cards}</div>
</section>

<section class="section">
  <div class="section-head"><h2>怎么用这个站</h2><p>一次 30 分钟，完成一个闭环。</p></div>
  <ol class="steps">
    <li><strong>读结构</strong><span>先确认报告类型、范围与鉴证，再看目录。</span></li>
    <li><strong>找证据</strong><span>按条款定位董事会声明、重要性、数据表与索引。</span></li>
    <li><strong>看评级</strong><span>先看日期与机构，再解释档位差异。</span></li>
    <li><strong>写下来</strong><span>三条事实、两个疑问、一个可复用方法。</span></li>
  </ol>
</section>

<section class="section">
  <div class="section-head"><h2>最新拆解</h2><a class="more" href="/daily">全部 {stats['episodes']} 期 →</a></div>
  <div class="daily-list">{latest}</div>
</section>

<section class="callout">
  <h2>先把话说清楚：这里不承诺评级</h2>
  <p>本站只做学习与方法拆解。评级引用公开接口返回的结果，标注机构与日期；不同机构口径不同，字母不可横向比较；没有公开评级不等于评级差。任何公司分析都不构成投资建议或合规意见。</p>
</section>
"""
    canonical = f"{base}/learn"
    jsonld = [
        {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "ESG 学习中心",
            "description": "五条 ESG 学习路径：核心知识、沟通与英语、工具、作品集、证书。",
            "url": canonical,
            "inLanguage": "zh-Hans",
            "hasPart": [
                {"@type": "Article", "name": p["title"], "url": f"{base}/learn/{p['slug']}"}
                for p in paths
            ],
        },
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": base,
            "inLanguage": "zh-Hans",
            "description": SITE_TAGLINE,
        },
    ]
    return layout(
        title=f"ESG 学习中心｜五条路径学会读港股 ESG 报告 · {SITE_NAME}",
        description="面向零背景转行者与 ESG 咨询新人的学习路径：GRI、范围一/二/三碳核算、CDP、MSCI 评级、沟通英语、Excel/Python/Power BI、报告拆解作品集与证书选择。",
        canonical=canonical,
        body=body,
        base=base,
        active="learn",
        jsonld=jsonld,
    )


def render_learn_detail(path: dict, base: str) -> str:
    slug = path["slug"]
    canonical = f"{base}/learn/{slug}"
    modules = "".join(
        f"""<section class="module" id="m{i}">
  <div class="module-head"><span class="module-no">{i:02d}</span><h2>{esc(m['name'])}</h2></div>
  <p class="module-why">{esc(m['why'])}</p>
  <div class="module-grid">
    <div>
      <h3>学什么</h3>
      <ul class="check-list">{''.join(f'<li>{esc(x)}</li>' for x in m['learn'])}</ul>
    </div>
    <div>
      <h3>动手做</h3>
      <p class="practice">{esc(m['practice'])}</p>
      <h3>免费资源</h3>
      <ul class="resource-list">{''.join(f'<li>{_ext_link(r["url"], r["title"])}<span>{esc(r.get("note", ""))}</span></li>' for r in m.get('resources', []))}</ul>
    </div>
  </div>
  <div class="pitfall"><strong>常见误区</strong><ul>{''.join(f'<li>{esc(x)}</li>' for x in m.get('pitfalls', []))}</ul></div>
</section>"""
        for i, m in enumerate(path["modules"], start=1)
    )
    outcomes = "".join(f"<li>{esc(x)}</li>" for x in path["outcomes"])
    weeks = "".join(
        f"<tr><td>{esc(w['week'])}</td><td>{esc(w['focus'])}</td><td>{esc(w['deliverable'])}</td></tr>"
        for w in path.get("week_plan", [])
    )
    checklist = "".join(f"<li>{esc(x)}</li>" for x in path.get("checklist", []))
    related = content.episodes_for_codes(path.get("related_daily_codes", []))
    related_html = "".join(_episode_row(e) for e in related) or '<p class="muted">暂无关联拆解。</p>'
    crumb_items = [("首页", "/learn"), ("学习中心", "/learn"), (path["title"], f"/learn/{slug}")]
    crumb, crumb_data = _breadcrumb(crumb_items)
    crumb_data["itemListElement"] = [
        {"@type": "ListItem", "position": i + 1, "name": label, "item": f"{base}{url}" if url.startswith("/") else url}
        for i, (label, url) in enumerate(crumb_items)
    ]
    body = f"""
{crumb}
<article class="article">
  <header class="article-head">
    <p class="kicker">学习路径 0{esc(path['order'])}</p>
    <h1>{esc(path['title'])}</h1>
    <p class="lead">{esc(path['subtitle'])}</p>
    <div class="meta-row">
      <span>适合：{esc(path['audience'])}</span>
      <span>周期：{esc(path['duration'])}</span>
    </div>
  </header>

  <div class="summary-box">
    <h2>这条路径解决什么</h2>
    <p>{esc(path['goal'])}</p>
    <ul class="outcome-list">{outcomes}</ul>
  </div>

  {f'<section class="section"><div class="section-head"><h2>四周节奏</h2></div><table class="data-table"><thead><tr><th>时间</th><th>重点</th><th>产出</th></tr></thead><tbody>{weeks}</tbody></table></section>' if weeks else ''}

  <div class="module-list">{modules}</div>

  <section class="callout">
    <h2>完成标准</h2>
    <ul class="check-list">{checklist}</ul>
  </section>

  <section class="section">
    <div class="section-head"><h2>用真实报告练习</h2><a class="more" href="/daily">全部拆解 →</a></div>
    <div class="daily-list">{related_html}</div>
  </section>
</article>
"""
    jsonld = [
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": path["title"],
            "description": path["subtitle"],
            "inLanguage": "zh-Hans",
            "author": {"@type": "Organization", "name": SITE_NAME},
            "publisher": {"@type": "Organization", "name": SITE_NAME},
            "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
            "about": "ESG 学习与职业转行",
            "educationalLevel": "入门到进阶",
            "teaches": path["outcomes"],
        },
        {"@context": "https://schema.org", **crumb_data},
    ]
    return layout(
        title=f"{path['title']}｜ESG 学习路径 · {SITE_NAME}",
        description=_short(path["goal"], 150),
        canonical=canonical,
        body=body,
        base=base,
        active="learn",
        jsonld=jsonld,
        og_type="article",
    )


def _social_section(ep: dict, base: str) -> str:
    url = f"{base}/daily#{ep['slug']}"
    cards = []
    for post in distribution.build_posts(ep, url):
        target = f"copy-{ep['slug']}-{post['key']}"
        cards.append(
            f"""<div class="post-card">
  <div class="post-head">
    <div><strong>{esc(post['label'])}</strong><span>{esc(post['hint'])}</span></div>
    <button type="button" class="copy-btn" data-copy="{esc(target)}">复制</button>
  </div>
  <pre class="post-text" id="{esc(target)}">{esc(post['text'])}</pre>
</div>"""
        )
    return f"""
<section class="section distribution">
  <div class="section-head"><h3>内容分发文案</h3><p>已按平台分好，点「复制」即可直接发布；发布前可按需删改。</p></div>
  <div class="post-grid">{''.join(cards)}</div>
</section>
"""


def _daily_article_body(ep: dict, base: str) -> str:
    meta = ep["rating_meta"]
    report = ep["report"]
    map_rows = "".join(
        f"<tr><td><strong>{esc(r['section'])}</strong></td><td>{esc(r['locator'])}</td>"
        f"<td>{esc(r['look_for'])}</td><td><code>{esc(r['clause'])}</code></td></tr>"
        for r in ep["reading_map"]
    )
    observations = "".join(f"<li>{esc(x)}</li>" for x in ep["observations"])
    rating_rows = "".join(
        f"""<tr class="{'is-stale' if r['stale'] else ''}">
  <td><strong>{esc(r['agency'])}</strong></td>
  <td><span class="rating-value">{esc(r['rating'])}</span></td>
  <td>{esc(r['rating_date'] or '—')}</td>
  <td>{esc(r['environmental'] or '—')}</td>
  <td>{esc(r['social'] or '—')}</td>
  <td>{esc(r['governance'] or '—')}</td>
  <td>{'时效提示' if r['stale'] else ''}</td>
</tr>"""
        for r in ep["ratings"]
    )
    rating_block = (
        f"""<div class="table-scroll"><table class="data-table rating-table">
      <thead><tr><th>机构</th><th>结果</th><th>日期</th><th>环境</th><th>社会</th><th>治理</th><th>备注</th></tr></thead>
      <tbody>{rating_rows}</tbody>
    </table></div>"""
        if ep["ratings"]
        else '<p class="muted">本期公开接口未返回评级结果；没有公开评级不等于评级差，也不补值。</p>'
    )
    questions = "".join(f"<li>{esc(x)}</li>" for x in ep["questions"])
    skills = "".join(
        f"<li><strong>{esc(s['name'])}</strong><span>{esc(s['how'])}</span></li>" for s in ep["skills"]
    )
    return f"""
  <header class="article-head">
    <p class="kicker">{esc(ep['date'])} · {esc(ep['industry'])} · 第 {esc(ep.get('issue', ''))} 期</p>
    <h2>{esc(ep['company'])}｜{esc(ep['angle'])}</h2>
    <div class="meta-row">
      <span>公司代号 {esc(ep['code'])}</span>
      <span>报告：{_ext_link(report['url'], _short(report['title'], 64))}</span>
      <span>发布：{esc(report['release_date'])}</span>
    </div>
  </header>

  <div class="summary-box hook"><p>{esc(ep['hook'])}</p></div>

  <section class="section">
    <h3>为什么读这一份</h3>
    <p>{esc(ep['why'])}</p>
  </section>

  <section class="section">
    <div class="section-head"><h3>报告阅读地图</h3><p>页码为 PDF 页码，按报告书签或目录核对。</p></div>
    <div class="table-scroll"><table class="data-table map-table">
      <thead><tr><th>章节</th><th>位置</th><th>看什么</th><th>对应条款</th></tr></thead>
      <tbody>{map_rows}</tbody>
    </table></div>
  </section>

  <section class="section">
    <h3>本期观察</h3>
    <ul class="observation-list">{observations}</ul>
  </section>

  <section class="section rating-section">
    <div class="section-head"><h3>公开评级快照</h3><p>抓取于 {esc(meta['fetched_at'])} · 来源：{esc(meta['source'])}</p></div>
    {rating_block}
    <p class="table-note">{esc(meta['note'])} 环境／社会／治理分项仅部分机构提供，未提供者以「—」表示，不补值。</p>
    <div class="rating-read"><h3>怎么看这组评级</h3><p>{esc(ep['rating_read'])}</p></div>
  </section>

  <section class="section">
    <h3>三个咨询师问题</h3>
    <ol class="question-list">{questions}</ol>
  </section>

  <section class="section">
    <h3>可复用技能</h3>
    <ul class="skill-list">{skills}</ul>
  </section>

  <section class="callout takeaway"><h3>带走一句话</h3><p>{esc(ep['takeaway'])}</p></section>

  {_social_section(ep, base)}
"""


def render_daily_index(base: str) -> str:
    series = content.series()
    episodes = content.daily_episodes()
    stats = content.site_stats()
    method = "".join(f"<li>{esc(x)}</li>" for x in series.get("method", []))
    toc = "".join(_episode_toc(e) for e in episodes)

    blocks = []
    for index, ep in enumerate(episodes):
        blocks.append(
            f"""<details class="episode-block" id="{esc(ep['slug'])}" {"open" if index == 0 else ""}>
  <summary>
    <span class="ep-date">{esc(ep['date'])}</span>
    <span class="ep-main"><strong>{esc(ep['company'])}</strong>｜{esc(ep['angle'])}</span>
    <span class="ep-meta">{esc(ep['industry'])} · {esc(ep['code'])} · 第 {esc(ep.get('issue', ''))} 期</span>
    {_rating_chip(ep)}
  </summary>
  <div class="episode-body">{_daily_article_body(ep, base)}</div>
</details>"""
        )

    canonical = f"{base}/daily"
    body = f"""
<section class="hero compact">
  <p class="kicker">持续更新 · 每日一期 · 拆解 + 分发文案</p>
  <h1>上市公司 ESG 报告拆解</h1>
  <p class="lead">{esc(series['intro'])}</p>
  <dl class="hero-stats">
    <div><dt>{stats['episodes']}</dt><dd>期已发布</dd></div>
    <div><dt>{stats['industries']}</dt><dd>个行业</dd></div>
    <div><dt>{stats['agencies']}</dt><dd>家评级机构</dd></div>
    <div><dt>{stats['latest']}</dt><dd>最近更新</dd></div>
  </dl>
</section>

<details class="method-block">
  <summary>这套拆解怎么做（阅读方法）</summary>
  <ol class="steps method">{method}</ol>
</details>

<section class="section">
  <div class="section-head">
    <h2>全部拆解</h2>
    <p>每期的正文、阅读地图、评级表和分发文案都在这一个页面里；点开即可阅读和复制。</p>
    <a class="more" href="/daily/feed.xml">RSS 订阅 →</a>
  </div>
  <nav class="episode-toc" aria-label="往期目录">{toc}</nav>
  <div class="episode-list">{''.join(blocks) or '<p class="muted">内容尚未生成，请先运行 python -m v2.scripts.daily_update。</p>'}</div>
</section>

<section class="callout">
  <h2>数据与口径</h2>
  <p>{esc(series.get('disclaimer', ''))}</p>
</section>

<script>
function openHashEpisode() {{
  if (!location.hash) return;
  const node = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (node && node.tagName === "DETAILS") node.open = true;
}}
openHashEpisode();
window.addEventListener("hashchange", openHashEpisode);

document.addEventListener("click", async function (event) {{
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const node = document.getElementById(button.getAttribute("data-copy"));
  if (!node) return;
  const text = node.textContent;
  button.textContent = "已复制";
  setTimeout(function () {{ button.textContent = "复制"; }}, 8000);
  try {{
    await navigator.clipboard.writeText(text);
  }} catch (error) {{
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }}
}});
</script>
"""
    jsonld = [
        {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "上市公司 ESG 报告拆解",
            "description": _short(series["intro"], 200),
            "url": canonical,
            "inLanguage": "zh-Hans",
            "mainEntity": {
                "@type": "ItemList",
                "numberOfItems": len(episodes),
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": i + 1,
                        "url": f"{base}/daily#{e['slug']}",
                        "name": f"{e['company']}：{e['angle']}",
                    }
                    for i, e in enumerate(episodes)
                ],
            },
        }
    ]
    return layout(
        title=f"上市公司 ESG 报告拆解｜港股 ESG 报告与公开评级 · {SITE_NAME}",
        description=(
            "每日拆解一家港股上市公司的 ESG 报告与公开评级：报告结构、章节页码、评级快照，"
            "并附小红书/微博/朋友圈/LinkedIn 可直接复制的分发文案。"
        ),
        canonical=canonical,
        body=body,
        base=base,
        active="daily",
        jsonld=jsonld,
    )


def render_not_found(base: str) -> str:
    body = """
<section class="hero compact">
  <p class="kicker">404</p>
  <h1>这一页没有内容</h1>
  <p class="lead">可能是链接过期，或这一期还未发布。</p>
  <div class="hero-actions">
    <a class="btn primary" href="/daily">去报告拆解</a>
    <a class="btn ghost" href="/learn">去学习中心</a>
  </div>
</section>
"""
    return layout(
        title=f"页面不存在 · {SITE_NAME}",
        description="页面不存在，返回 ESG 智能报告系统首页。",
        canonical=f"{base}/learn",
        body=body,
        base=base,
        active="",
    )


def render_sitemap(base: str) -> str:
    latest = content.site_stats()["latest"] or date.today().isoformat()
    entries: list[tuple[str, str, str, str]] = [
        ("/", latest, "weekly", "1.0"),
        ("/learn", latest, "weekly", "0.9"),
        ("/daily", latest, "daily", "0.9"),
    ]
    for p in content.learn_paths():
        entries.append((f"/learn/{p['slug']}", latest, "monthly", "0.8"))
    urls = "\n".join(
        f"  <url><loc>{xml_escape(base + path)}</loc><lastmod>{lastmod}</lastmod>"
        f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        for path, lastmod, freq, priority in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n"
    )


def render_robots(base: str) -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /ui/index.html\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


def render_feed(base: str) -> str:
    series = content.series()
    episodes = content.daily_episodes()
    items = []
    for ep in episodes:
        try:
            dt = datetime.combine(date.fromisoformat(ep["date"]), time(9, 0), tzinfo=timezone.utc)
            pub = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except ValueError:
            pub = ""
        link = f"{base}/daily#{ep['slug']}"
        items.append(
            f"""    <item>
      <title>{xml_escape(ep['company'] + '：' + ep['angle'])}</title>
      <link>{xml_escape(link)}</link>
      <guid isPermaLink="false">{xml_escape(link)}</guid>
      <pubDate>{pub}</pubDate>
      <category>{xml_escape(ep['industry'])}</category>
      <description>{xml_escape(_short(ep['hook'], 220))}</description>
    </item>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{xml_escape('上市公司 ESG 报告拆解 · ' + SITE_NAME)}</title>
    <link>{xml_escape(base + '/daily')}</link>
    <description>{xml_escape(series['intro'])}</description>
    <language>zh-CN</language>
    <lastBuildDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
