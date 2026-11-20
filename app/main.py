"""FastAPI 入口：港股 ESG 智能报告系统 - 数据采集层骨架

启动：
    uvicorn app.main:app --reload --port 8000

文档：
    http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .collectors import (
    hkex_collector,
    hkgov_collector,
    nzdpu_collector,
    sbti_collector,
    sina_esg_collector,
    worldbank_collector,
    yfinance_collector,
)
from .config import settings
from .extractor import extract_kpis_from_pdf, save_kpis
from .reporter import generate_report, save_report
from .schemas import (
    ClientAnalysis,
    CompanyInfo,
    CountryEmission,
    ESGAggregate,
    ESGKPIs,
    ESGReport,
    ESGScore,
    EmissionRecord,
    HKEXReportMeta,
    HKGovIndicator,
    MultiAgencyESG,
    SBTiTarget,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
app = FastAPI(
    title="HK ESG Report System",
    version="0.2.0",
    description="港股 ESG 数据采集层 (yfinance / NZDPU / HKEX / DATA.GOV.HK / SBTi / 新浪ESG / World Bank)",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def demo_ui():
    """客户演示界面。"""
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="演示界面未部署（缺少 web/index.html）")
    return FileResponse(index)


def _collect_aggregate(
    ticker: str,
    company_name: str,
    stock_code: str,
    year: int,
    from_date: str,
    to_date: str,
    errors: Optional[list[str]] = None,
) -> ESGAggregate:
    """共享的多源采集逻辑（供 /collect 与 /report 复用，消除重复代码）。

    单源失败不阻塞其他源：Yahoo 限流 / NZDPU 无 token / SBTi 接口异常时
    对应字段返回 None。传入 errors 列表时，会把每个数据源的失败原因追加进去，
    便于在获客诊断接口里向前端暴露「哪个源不可达」。
    """
    def _fail(source: str, exc: Exception) -> None:
        logging.warning("%s 采集失败: %s", source, exc)
        if errors is not None:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")

    # HKEX 接口 toDate 超过今天会返回 result=None
    try:
        to_dt = date.fromisoformat(to_date)
        if to_dt > date.today():
            to_date = date.today().isoformat()
    except ValueError:
        pass

    # Yahoo 部分：失败时降级
    company = None
    yahoo_esg = None
    try:
        company = yfinance_collector.fetch_company_info(ticker)
        company.hkex_code = stock_code
    except Exception as e:
        logging.warning(f"yfinance company info 失败: {e}")
    try:
        yahoo_esg = yfinance_collector.fetch_esg_scores(ticker, year)
    except Exception as e:
        logging.warning(f"yfinance ESG 失败: {e}")
    if company is None:
        company = CompanyInfo(ticker=ticker, name=company_name, hkex_code=stock_code)

    # 下面三个源原本没有容错，任一抛出会让整次聚合失败（云上常见：海外 IP 访问港府数据超时）
    nzdpu_emis = None
    try:
        nzdpu_emis = nzdpu_collector.fetch_emissions(company_name, year)
    except Exception as e:
        _fail("NZDPU", e)

    hkex_reports = []
    try:
        hkex_reports = hkex_collector.search_esg_reports(stock_code, from_date, to_date)
    except Exception as e:
        _fail("HKEX", e)

    hk_benchmark = None
    try:
        hk_benchmark = hkgov_collector.fetch_ghg_emissions(year)
    except Exception as e:
        _fail("HK Gov", e)

    # 新增 ESG 数据源：均做容错，失败返回 None
    sbti_target = None
    try:
        sbti_target = sbti_collector.fetch_target(company_name, year)
    except Exception as e:
        _fail("SBTi", e)

    sina_esg = None
    try:
        sina_esg = sina_esg_collector.fetch_ratings(stock_code, company_name)
    except Exception as e:
        _fail("Sina ESG", e)

    country_emission = None
    try:
        # 港股公司默认取中国排放基准
        country_emission = worldbank_collector.fetch_country_emissions("China", year)
    except Exception as e:
        _fail("World Bank", e)

    return ESGAggregate(
        company=company,
        yahoo_esg=yahoo_esg,
        nzdpu_emissions=nzdpu_emis,
        hkex_reports=hkex_reports,
        hk_benchmark=hk_benchmark,
        sbti_target=sbti_target,
        sina_esg=sina_esg,
        country_emission=country_emission,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# ----- Yahoo Finance -----
@app.get("/yahoo/company/{ticker}", response_model=CompanyInfo, tags=["yfinance"])
def get_company_info(ticker: str):
    """获取公司基本信息（行业、板块等）。"""
    try:
        return yfinance_collector.fetch_company_info(ticker)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/yahoo/esg/{ticker}", response_model=ESGScore, tags=["yfinance"])
def get_yahoo_esg(ticker: str, year: int = Query(default=date.today().year)):
    """获取 Yahoo/Sustainalytics ESG 评分。"""
    try:
        return yfinance_collector.fetch_esg_scores(ticker, year)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


# ----- NZDPU -----
@app.get("/nzdpu/emissions", response_model=EmissionRecord, tags=["nzdpu"])
def get_nzdpu_emissions(
    company_name: str,
    year: int = Query(default=date.today().year),
):
    """获取 NZDPU 公司级温室气体排放。"""
    return nzdpu_collector.fetch_emissions(company_name, year)


# ----- HKEX ESG Reports -----
@app.get("/hkex/reports", response_model=list[HKEXReportMeta], tags=["hkex"])
def search_hkex_reports(
    stock_code: str = Query(..., description="港交所代码，如 09988"),
    from_date: str = Query(..., description="YYYY-MM-DD"),
    to_date: str = Query(..., description="YYYY-MM-DD"),
):
    """搜索港交所披露的 ESG 报告元数据。"""
    return hkex_collector.search_esg_reports(stock_code, from_date, to_date)


@app.post("/hkex/reports/download", response_model=HKEXReportMeta, tags=["hkex"])
def download_hkex_report(meta: HKEXReportMeta):
    """下载 HKEX ESG PDF 到本地缓存目录。"""
    try:
        hkex_collector.download_report(meta)
        return meta
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"download error: {e}")


# ----- HK Government Open Data -----
@app.get("/hkgov/ghg", response_model=Optional[HKGovIndicator], tags=["hkgov"])
def get_hk_ghg_emissions(year: int = Query(default=date.today().year)):
    """获取香港温室气体排放数据集（行业基准因子）。"""
    return hkgov_collector.fetch_ghg_emissions(year)


# ----- SBTi（科学碳目标）-----
@app.get("/sbti/target", response_model=SBTiTarget, tags=["sbti"])
def get_sbti_target(
    company_name: str = Query(..., description="公司英文全称"),
    year: int = Query(default=date.today().year),
):
    """查询公司是否设定科学碳目标（SBTi）。免费数据，无需 token。"""
    return sbti_collector.fetch_target(company_name, year)


# ----- 新浪财经 ESG 评级（多机构）-----
@app.get("/sina-esg/ratings", response_model=MultiAgencyESG, tags=["sina_esg"])
def get_sina_esg_ratings(
    stock_code: str = Query(..., description="港交所代码如 09988，或新浪格式如 hk09988/sh600519"),
    company_name: str = Query("", description="公司名称（可选，仅用于记录）"),
):
    """查询多机构 ESG 评级（MSCI/标普/中证指数/国证/商道融绿等）。免费无 token。"""
    return sina_esg_collector.fetch_ratings(stock_code, company_name)


# ----- World Bank（国家级排放基准）-----
@app.get("/worldbank/emissions", response_model=Optional[CountryEmission], tags=["worldbank"])
def get_worldbank_emissions(
    country: str = Query("China", description="国家名（英文），如 China / Hong Kong / USA"),
    year: Optional[int] = Query(None, description="目标年份，不传取最新"),
):
    """获取国家级温室气体排放基准（World Bank Open Data，免费无 token）。"""
    return worldbank_collector.fetch_country_emissions(country, year)


# ----- 综合采集 -----
@app.get("/collect/esg/{ticker}", response_model=ESGAggregate, tags=["aggregate"])
def collect_esg(
    ticker: str,
    company_name: str = Query(..., description="公司全称，用于 NZDPU/SBTi/CDP 查询"),
    stock_code: str = Query(..., description="港交所代码，如 09988"),
    year: int = Query(default=date.today().year),
    from_date: str = Query(default=None, description="YYYY-MM-DD，默认当年 1-1"),
    to_date: str = Query(default=None, description="YYYY-MM-DD，默认今日"),
):
    """一次性采集多源 ESG 数据，返回合并视图，供下游报告生成层使用。

    包含：Yahoo ESG / NZDPU 排放 / HKEX 报告 / HK 行业基准 /
    SBTi 目标 / CDP 评分 / ClimateWatch 国家排放基准。
    单源失败不阻塞其他源。
    """
    if not from_date:
        from_date = f"{year}-01-01"
    if not to_date:
        to_date = min(date(year, 12, 31), date.today()).isoformat()
    return _collect_aggregate(ticker, company_name, stock_code, year, from_date, to_date)


# ----- LLM 抽取 -----
@app.post("/extract", response_model=ESGKPIs, tags=["extract"])
def extract_kpis(
    pdf_path: str = Query(..., description="本地 PDF 路径，如 data/hkex_reports/00005_2024-03-22.pdf"),
    company_name: str = Query(..., description="公司名称"),
    stock_code: str = Query(..., description="港交所代码，如 00005"),
    report_year: int = Query(..., description="报告年份"),
    save: bool = Query(True, description="是否落盘到 data/cache/kpis/"),
):
    """从已下载的 HKEX ESG PDF 中抽取结构化 KPI。

    需要在 .env 配置 LLM_API_KEY / LLM_API_BASE / LLM_MODEL。
    支持 OpenAI / DeepSeek / Ollama / vLLM 等任意 OpenAI 兼容协议。
    """
    try:
        kpis = extract_kpis_from_pdf(
            pdf_path=pdf_path,
            company_name=company_name,
            stock_code=stock_code,
            report_year=report_year,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if save:
        save_kpis(kpis)
    return kpis


# ----- 报告生成 -----
@app.post("/report", response_model=ESGReport, tags=["report"])
def build_report(
    ticker: str = Query(..., description="Yahoo ticker，如 0005.HK"),
    company_name: str = Query(..., description="公司全称，用于 NZDPU 和报告标题"),
    stock_code: str = Query(..., description="港交所代码，如 00005"),
    year: int = Query(..., description="报告年份"),
    pdf_path: str = Query(..., description="HKEX ESG PDF 路径，用于 LLM 抽取"),
    use_llm_narrative: bool = Query(True, description="是否调 LLM 生成叙述章节"),
    save: bool = Query(True, description="是否落盘到 data/cache/reports/"),
):
    """端到端：collect (多源采集) + extract (LLM 抽取) + reporter (生成 Markdown)。

    流程：
    1. 调 _collect_aggregate 拿 ESGAggregate（含 Yahoo/NZDPU/HKEX/HKGov/SBTi/CDP/ClimateWatch）
    2. 调 extract_kpis_from_pdf 拿 ESGKPIs
    3. 调 generate_report 合并生成 ESGReport
    """
    from datetime import date as _date
    from_date = f"{year}-01-01"
    to_date = min(_date(year, 12, 31), _date.today()).isoformat()

    # 复用共享采集逻辑（消除与 /collect 端点的重复代码）
    aggregate = _collect_aggregate(
        ticker, company_name, stock_code, year, from_date, to_date
    )

    try:
        kpis = extract_kpis_from_pdf(
            pdf_path=pdf_path,
            company_name=company_name,
            stock_code=stock_code,
            report_year=year,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    report = generate_report(
        aggregate=aggregate,
        kpis=kpis,
        use_llm_narrative=use_llm_narrative,
    )
    if save:
        save_report(report)
    return report


# ----- Peer-based 报告（非上市公司）-----
@app.get("/peers/industries", tags=["peers"])
def list_industries():
    """列出所有支持的行业（用于 peer-based 报告选行业）。"""
    from .industry_peers import PEER_LIBRARY, INDUSTRY_ALIASES
    return {
        "industries": list(PEER_LIBRARY.keys()),
        "aliases": list(INDUSTRY_ALIASES.keys()),
    }


@app.get("/peers/{industry}", tags=["peers"])
def list_peers(industry: str):
    """列出某行业的标杆公司。"""
    from .industry_peers import get_peers, resolve_industry
    std = resolve_industry(industry)
    if not std:
        raise HTTPException(status_code=404, detail=f"不支持的行业: {industry}")
    peers = get_peers(std)
    return [p.__dict__ for p in peers]


@app.post("/report/peer", response_model=ESGReport, tags=["report"])
def build_peer_report(
    company_name: str = Query(..., description="目标非上市公司名称"),
    industry: str = Query(..., description="行业，支持中文：银行/保险/地产/科技/公用事业/能源/零售/物流/医疗"),
    year: int = Query(..., description="报告年份"),
    peer_index: int = Query(0, description="标杆公司索引（默认 0，即行业列表第一个）"),
    peer_kpis_path: Optional[str] = Query(None, description="已抽取的标杆 KPI JSON 路径；若不传，自动从标杆 HKEX 报告抽取"),
    self_kpis_path: Optional[str] = Query(None, description="目标公司自报 KPI JSON 路径（可选）"),
    use_llm_narrative: bool = Query(True, description="是否调 LLM 生成叙述章节"),
    save: bool = Query(True, description="是否落盘到 data/cache/reports/"),
):
    """为非上市公司基于行业标杆生成 ESG 报告。

    流程：
    1. 行业 → 标杆公司（industry_peers）
    2. 取标杆 ESGKPIs：若 peer_kpis_path 已提供则直接读取，否则搜索并下载标杆最新 HKEX ESG PDF → LLM 抽取
    3. 调 generate_peer_report 生成报告
    """
    import json
    from pathlib import Path
    from .extractor import extract_kpis_from_pdf, save_kpis
    from .industry_peers import get_peers, resolve_industry
    from .reporter import generate_peer_report, save_report

    std = resolve_industry(industry)
    if not std:
        raise HTTPException(
            status_code=404,
            detail=f"不支持的行业: {industry}，调用 /peers/industries 查看支持的行业"
        )
    peers = get_peers(std)
    if peer_index >= len(peers):
        raise HTTPException(
            status_code=400,
            detail=f"peer_index {peer_index} 超出范围（行业 {std} 只有 {len(peers)} 个标杆）"
        )
    peer = peers[peer_index]

    # 获取标杆 ESGKPIs
    if peer_kpis_path:
        kpi_file = Path(peer_kpis_path)
        if not kpi_file.exists():
            raise HTTPException(status_code=404, detail=f"KPI 文件不存在: {peer_kpis_path}")
        peer_kpis = ESGKPIs.model_validate_json(kpi_file.read_text(encoding="utf-8"))
    else:
        # 自动搜索并抽取标杆最新 ESG 报告
        from datetime import date as _date
        from_date = f"{year - 1}-01-01"  # 取上一年报告（X 财年报告在 X+1 年发布）
        to_date = min(_date(year, 12, 31), _date.today()).isoformat()
        reports = hkex_collector.search_esg_reports(peer.stock_code, from_date, to_date)
        if not reports:
            raise HTTPException(
                status_code=404,
                detail=f"未找到标杆 {peer.name}（{peer.stock_code}）在 {year} 年附近的 HKEX ESG 报告"
            )
        # 选最新一份
        latest = max(reports, key=lambda r: r.release_date)
        downloaded = hkex_collector.download_report(latest)
        try:
            peer_kpis = extract_kpis_from_pdf(
                pdf_path=downloaded,
                company_name=peer.name,
                stock_code=peer.stock_code,
                report_year=year,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"标杆 KPI 抽取失败: {e}")

    # 目标公司自报 KPI（可选）
    self_kpis = None
    if self_kpis_path:
        f = Path(self_kpis_path)
        if f.exists():
            self_kpis = ESGKPIs.model_validate_json(f.read_text(encoding="utf-8"))

    # 香港行业基准
    hk_benchmark = hkgov_collector.fetch_ghg_emissions(year)

    report = generate_peer_report(
        company_name=company_name,
        industry=industry,
        report_year=year,
        peer_kpis=peer_kpis,
        peer_company_name=peer.name,
        peer_stock_code=peer.stock_code,
        peer_notes=peer.notes,
        self_kpis=self_kpis,
        hk_benchmark=hk_benchmark,
        use_llm_narrative=use_llm_narrative,
    )
    if save:
        save_report(report)
    return report


# ----- 非上市企业：客户填报数据 → 对标 → 定制报告 -----
@app.post("/client/analyze", response_model=ClientAnalysis, tags=["client"])
def analyze_client_data(
    excel_path: str = Query(..., description="客户填报的采集表路径（xlsx）"),
    industry: Optional[str] = Query(None, description="覆盖表中的行业，如 制造业/电子科技/金融"),
):
    """解析客户采集表并做行业对标，返回结构化差距分析。"""
    from .benchmark import analyze, normalize_industry
    from .client_data import derive_metrics, load_client_excel

    try:
        client = load_client_excel(excel_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    key = normalize_industry(industry or client.profile.industry)
    return analyze(client, key, derive_metrics(client))


@app.post("/client/report", response_model=ESGReport, tags=["client"])
def build_client_report_endpoint(
    excel_path: str = Query(..., description="客户填报的采集表路径（xlsx）"),
    industry: Optional[str] = Query(None, description="覆盖表中的行业"),
    use_llm_narrative: bool = Query(
        True, description="是否调用 LLM 生成第3、7章叙述；失败自动回落为确定性文本"
    ),
    target_rating: Optional[str] = Query(
        None, description="目标评级（A/AA）；填写后在报告中新增评级提升路径章节"
    ),
    save: bool = Query(True, description="是否落盘到 data/cache/reports/"),
):
    """基于客户填报数据生成定制化 ESG 报告。

    数据与对标为确定性计算；use_llm_narrative=True 时额外生成两段叙述文字，
    LLM 不可用或输出异常时自动降级，不影响报告产出。
    """
    from .benchmark import analyze, normalize_industry
    from .client_data import derive_metrics, load_client_excel
    from .client_report import build_client_report, save_client_report

    try:
        client = load_client_excel(excel_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    key = normalize_industry(industry or client.profile.industry)
    analysis = analyze(client, key, derive_metrics(client))
    report = build_client_report(
        analysis, use_llm_narrative=use_llm_narrative, target_rating=target_rating
    )
    if save:
        save_client_report(report, analysis)
    return report


@app.get("/client/template", tags=["client"])
def download_client_template():
    """下载空白数据采集表模板。"""
    tpl = Path(__file__).resolve().parent.parent / "docs" / "非上市企业ESG数据采集表.xlsx"
    if not tpl.exists():
        raise HTTPException(status_code=404, detail="模板文件不存在")
    return FileResponse(
        tpl,
        filename="非上市企业ESG数据采集表.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@app.post("/client/report/upload", response_model=ESGReport, tags=["client"])
async def build_client_report_upload(
    file: UploadFile = File(..., description="填写完成的采集表（.xlsx）"),
    industry: Optional[str] = Form(None, description="覆盖表中的行业"),
    target_rating: Optional[str] = Form(None, description="目标评级（A/AA）"),
    use_llm_narrative: bool = Form(True),
    save: bool = Form(True),
):
    """上传采集表 → 生成定制报告（演示界面使用）。"""
    import uuid

    from .benchmark import analyze, normalize_industry
    from .client_data import derive_metrics, load_client_excel
    from .client_report import build_client_report, save_client_report

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm 格式的采集表")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    upload_dir = settings.cache_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)

    try:
        client = load_client_excel(target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    key = normalize_industry(industry or client.profile.industry)
    analysis = analyze(client, key, derive_metrics(client))
    report = build_client_report(
        analysis, use_llm_narrative=use_llm_narrative, target_rating=target_rating
    )
    if save:
        save_client_report(report, analysis)
    return report


# ========================================================
# 获客漏斗：留资 + 快速诊断（不跑完整报告、不调 LLM）
# 原有端点保持不动，获客版 UI 调下面三个端点
# ========================================================

import json as _json  # noqa: E402
from datetime import datetime  # noqa: E402


@app.post("/api/leads", tags=["leads"])
async def save_lead(
    company: str = Form(..., description="公司全称"),
    contact: str = Form("", description="联系人姓名"),
    email: str = Form("", description="邮箱（或电话/微信）"),
    stock_code: str = Form("", description="港交所代码（上市公司填，如 00700；非上市留空）"),
    industry: str = Form("", description="所属行业"),
    source: str = Form("web", description="来源渠道"),
    note: str = Form("", description="备注或问题"),
):
    """保存留资信息到 /tmp/esg/leads/（云环境唯一可写目录）。

    每条记录一个 JSON 文件；定期导出就是潜在客户列表。
    Render 重启后自动清理，适合轻量获客场景。
    """
    from .config import settings

    lead = {
        "company": company.strip(),
        "contact": contact.strip(),
        "email": email.strip(),
        "stock_code": stock_code.strip(),
        "industry": industry.strip(),
        "source": source.strip(),
        "note": note.strip(),
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    fname = f"lead_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.json"
    (settings.lead_dir / fname).write_text(
        _json.dumps(lead, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logging.info("Lead saved: %s (%s)", company, email)

    # webhook 转发：/tmp 会在重新部署时清空，转发到飞书/钉钉/n8n 才是线索不丢的可靠方式
    if settings.lead_webhook_url:
        _notify_lead_webhook(settings.lead_webhook_url, lead)

    return {"status": "ok", "message": "留资成功，24 小时内会有专人联系"}


def _notify_lead_webhook(url: str, lead: dict) -> None:
    """把留资 goroutine 之外地推到 webhook。失败只记日志，不影响用户提交。"""
    text = (
        "【ESG 新留资】\n"
        f"公司：{lead['company']}\n"
        f"联系人：{lead['contact'] or '—'}\n"
        f"联系方式：{lead['email']}\n"
        f"港股代码：{lead['stock_code'] or '—'}\n"
        f"行业：{lead['industry'] or '—'}\n"
        f"来源：{lead['source']}\n"
        f"备注：{lead['note'] or '—'}\n"
        f"时间：{lead['created_at']}"
    )
    try:
        import httpx as _httpx

        with _httpx.Client(timeout=8.0, follow_redirects=True) as c:
            # 飞书/钉钉/企业微信机器人普遍接受 {"text": ...}；其他端可按需改成原始 JSON
            c.post(url, json={"text": text, "msgtype": "text", "lead": lead})
    except Exception as e:  # noqa: BLE001
        logging.warning("留资 webhook 推送失败: %s", e)


def _require_admin(token: str) -> None:
    """校验管理口令；未配置 ADMIN_TOKEN 时整体禁用。"""
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="未配置 ADMIN_TOKEN，管理页已禁用")
    if token != settings.admin_token:
        raise HTTPException(status_code=403, detail="口令错误")


@app.get("/admin/leads", tags=["leads"])
def admin_leads(token: str = Query("", description="管理口令（ADMIN_TOKEN）")):
    """查看已收集的留资线索（HTML 表格）。"""
    from .config import settings

    _require_admin(token)

    rows: list[dict] = []
    for f in sorted(settings.lead_dir.glob("lead_*.json"), reverse=True):
        try:
            rows.append(_json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue

    def esc(v: object) -> str:
        return (
            str(v if v is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    body = "".join(
        "<tr>"
        f"<td>{esc(r.get('created_at'))}</td>"
        f"<td><b>{esc(r.get('company'))}</b></td>"
        f"<td>{esc(r.get('contact'))}</td>"
        f"<td>{esc(r.get('email'))}</td>"
        f"<td>{esc(r.get('stock_code'))}</td>"
        f"<td>{esc(r.get('industry'))}</td>"
        f"<td>{esc(r.get('source'))}</td>"
        f"<td>{esc(r.get('note'))}</td>"
        "</tr>"
        for r in rows
    ) or "<tr><td colspan='8'>暂无留资</td></tr>"

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>留资线索（{len(rows)}）</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#161A17;background:#FBFBF7}}
h1{{font-size:18px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px;background:#fff}}
th,td{{border:1px solid #E2E3D8;padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#F7F7F1;color:#6B726C;font-weight:600}}
.note{{color:#6B726C;font-size:12px;margin-bottom:12px}}
</style></head><body>
<h1>留资线索 · 共 {len(rows)} 条</h1>
<p class="note">数据存于容器 /tmp，重新部署会清空。请配置 LEAD_WEBHOOK_URL 做持久化转发。</p>
<table><thead><tr>
<th>时间</th><th>公司</th><th>联系人</th><th>联系方式</th>
<th>港股代码</th><th>行业</th><th>来源</th><th>备注</th>
</tr></thead><tbody>{body}</tbody></table>
</body></html>"""
    return HTMLResponse(content=html)


_ADMIN_CSS = """
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#FBFBF7;color:#161A17}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0 0 6px}
.note{color:#6B726C;font-size:12px;margin:0 0 18px}
.card{background:#fff;border:1px solid #E2E3D8;border-radius:10px;padding:18px;margin-bottom:18px}
label{display:block;font-size:12px;color:#6B726C;margin-bottom:5px}
input[type=text],input[type=number]{width:100%;padding:9px 11px;border:1px solid #E2E3D8;
  border-radius:7px;font-size:13px;font-family:inherit}
.row{display:flex;gap:12px;flex-wrap:wrap}
.row>div{flex:1;min-width:160px}
button{background:#0F5132;color:#fff;border:0;border-radius:7px;padding:10px 18px;
  font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
button:hover{background:#1F6B4C}
.btn2{background:#fff;color:#0F5132;border:1px solid #C9DCD2}
.warn{background:#FAF4F0;border-left:3px solid #A8542A;padding:10px 12px;
  border-radius:7px;font-size:12.5px;color:#6B4432;margin-bottom:16px}
.info{background:#F2F6F4;border-left:3px solid #0F5132;padding:10px 12px;
  border-radius:7px;font-size:12.5px;color:#28453A;margin-bottom:16px}
pre{white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid #E2E3D8;
  border-radius:10px;padding:20px;font-size:13px;line-height:1.75;
  font-family:"Microsoft YaHei",Consolas,monospace}
a{color:#0F5132}
"""


def _admin_report_form(token: str, message: str = "") -> str:
    import html as _html

    msg = (
        f'<div class="warn">{_html.escape(message)}</div>' if message else ""
    )
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>生成完整报告</title><style>{_ADMIN_CSS}</style></head><body><div class="wrap">
<h1>生成完整 ESG 报告</h1>
<p class="note">输入港股代码即可：自动检索港交所 ESG 报告 → 下载 PDF → 抽取 KPI → 生成八章报告。
首次生成约 1–3 分钟；勾选 LLM 叙述会更慢，且需要配置 LLM_API_KEY。</p>
{msg}
<form class="card" method="get" action="/admin/report">
  <input type="hidden" name="token" value="{_html.escape(token)}">
  <div class="row">
    <div><label>港股代码</label>
      <input type="text" name="stock_code" placeholder="如 06893" required></div>
    <div><label>公司名称（可留空）</label>
      <input type="text" name="company_name" placeholder="如 中国水务"></div>
    <div><label>报告年度</label>
      <input type="number" name="year" placeholder="留空取上一年"></div>
    <div><label>LLM 叙述（1=开，0=关）</label>
      <input type="number" name="llm" value="0" min="0" max="1"></div>
  </div>
  <div style="margin-top:12px"><label>PDF 直链（可选；HKEX 自动检索失败时填写）</label>
    <input type="text" name="pdf_url"
           placeholder="https://www1.hkexnews.hk/listedco/listconews/sehk/2025/xxxx/xxxx.pdf"></div>
  <div style="margin-top:12px;font-size:12.5px;color:#3A403B">
    <label style="display:flex;gap:8px;align-items:center;color:#3A403B">
      <input type="checkbox" name="public_only" value="1" style="width:auto">
      只看公开数据（跳过港交所 PDF 检索与抽取，约 10 秒出报告）
    </label>
  </div>
  <div style="margin-top:16px"><button type="submit">生成报告</button></div>
</form>
<p class="note"><a href="/admin/leads?token={_html.escape(token)}">← 返回留资线索</a></p>
</div></body></html>"""


@app.get("/admin/report", tags=["admin"])
def admin_report(
    token: str = Query("", description="管理口令（ADMIN_TOKEN）"),
    stock_code: str = Query("", description="港股代码，如 06893"),
    company_name: str = Query("", description="公司名称"),
    year: int = Query(0, description="报告年度，0 表示取上一年"),
    llm: int = Query(0, description="1=生成 LLM 叙述"),
    pdf_url: str = Query("", description="可选：直接指定 ESG 报告 PDF 链接"),
    public_only: int = Query(0, description="1=只跑公开数据，跳过 PDF 检索与抽取"),
):
    """管理端：一键生成某只港股的完整 ESG 报告（HTML 预览 + 下载 Markdown）。"""
    import html as _html

    _require_admin(token)

    if not stock_code.strip():
        return HTMLResponse(_admin_report_form(token))

    code = stock_code.strip()
    y = year
    if not y or y < 2000 or y > date.today().year + 1:
        y = date.today().year - 1
    ticker = f"{code.lstrip('0').zfill(5)}.HK"
    name = company_name.strip() or code
    from_date = f"{y - 1}-01-01"
    to_date = min(date(y, 12, 31), date.today()).isoformat()

    steps: list[str] = []
    problems: list[str] = []

    # 1) 检索港交所 ESG 报告（公开数据模式下整段跳过）
    latest = None
    if public_only:
        steps.append("公开数据模式：已跳过 PDF 下载与正文抽取（仅查报告元数据）")
    else:
        try:
            metas = hkex_collector.search_esg_reports(code, from_date, to_date)
        except Exception as e:  # noqa: BLE001
            metas = []
            problems.append(f"HKEX 检索失败：{type(e).__name__}: {e}")
        if metas:
            latest = max(metas, key=lambda m: m.release_date)
            steps.append(
                f"检索到 {len(metas)} 份 ESG 报告，取最新："
                f"{latest.release_date} {latest.report_title}"
            )
        else:
            problems.append("未检索到该公司的 ESG 报告，报告将只包含公开数据部分")

    # 2) 下载 PDF：优先用显式传入的链接，其次用 HKEX 检索结果
    pdf_path = None
    if public_only:
        pass
    elif pdf_url.strip():
        try:
            import httpx as _httpx

            with _httpx.Client(
                headers={"User-Agent": settings.user_agent},
                follow_redirects=True,
                timeout=120.0,
            ) as c:
                resp = c.get(pdf_url.strip())
                resp.raise_for_status()
            pdf_dir = settings.hkex_download_dir
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = pdf_dir / f"{code}_{y}_manual.pdf"
            pdf_path.write_bytes(resp.content)
            steps.append(f"已下载指定 PDF（{len(resp.content) // 1024} KB）")
        except Exception as e:  # noqa: BLE001
            problems.append(f"指定 PDF 下载失败：{type(e).__name__}: {e}")
    elif latest is not None:
        try:
            pdf_path = hkex_collector.download_report(latest)
            steps.append(f"PDF 已就绪：{pdf_path.name}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"PDF 下载失败：{type(e).__name__}: {e}")

    # 3) 抽取 KPI（失败则回退空 KPI，保证报告仍可产出）
    kpis = None
    if pdf_path is not None:
        try:
            kpis = extract_kpis_from_pdf(
                pdf_path=pdf_path, company_name=name, stock_code=code, report_year=y
            )
            steps.append(
                f"PDF 抽取完成，置信度 {kpis.extraction_confidence:.2f}，"
                f"备注 {len(kpis.notes)} 条"
            )
        except Exception as e:  # noqa: BLE001
            problems.append(f"PDF 抽取失败（已回退为空 KPI）：{type(e).__name__}: {e}")
    if kpis is None:
        kpis = ESGKPIs(
            company_name=name,
            stock_code=code,
            report_year=y,
            source_pdf=str(pdf_path) if pdf_path else "",
            notes=["kpi extraction skipped or failed"],
        )

    # 4) 多源采集 + 生成报告
    try:
        aggregate = _collect_aggregate(ticker, name, code, y, from_date, to_date)
        report = generate_report(
            aggregate=aggregate, kpis=kpis, use_llm_narrative=bool(llm)
        )
        try:
            save_report(report)
        except Exception:  # noqa: BLE001
            pass
        steps.append(f"报告生成完成，数据质量 {report.overall_data_quality:.0%}，"
                     f"章节 {len(report.sections)} 个")
    except Exception as e:  # noqa: BLE001
        logging.exception("admin_report 生成失败")
        err = f"{type(e).__name__}: {e}"
        return HTMLResponse(
            _admin_report_form(token, f"报告生成失败：{err}"), status_code=500
        )

    step_html = "".join(f"<div>· {_html.escape(s)}</div>" for s in steps)
    info_html = (
        '<div class="info"><b>公开数据版</b>'
        "<div>· 本报告仅含公开数据：公司信息、Yahoo 评级、SBTi 目标、"
        "多机构评级、国家排放基准</div>"
        "<div>· 不含港交所报告正文中的排放、能耗、员工等数字</div>"
        "<div>· 如需完整版，请在首页填写该公司的 ESG 报告 PDF 直链后重新生成</div>"
        "</div>"
        if public_only
        else ""
    )
    warn_html = (
        '<div class="warn"><b>需要注意</b>'
        + "".join(f"<div>· {_html.escape(p)}</div>" for p in problems)
        + "</div>"
        if problems
        else ""
    )
    md = report.markdown

    return HTMLResponse(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{_html.escape(report.title)}</title><style>{_ADMIN_CSS}</style></head><body><div class="wrap">
<h1>{_html.escape(report.title)}</h1>
<p class="note">{_html.escape(report.company_name)} · {report.stock_code} · {report.report_year} ·
生成于 {report.generated_at}</p>
<div class="card" style="font-size:12.5px;color:#3A403B">{step_html}</div>
{info_html}
{warn_html}
<div style="margin-bottom:12px">
  <button onclick="dlMd()">下载 Markdown</button>
  <a href="/admin/report?token={_html.escape(token)}" class="btn2"
     style="display:inline-block;padding:10px 18px;border-radius:7px;text-decoration:none;
            font-size:13px;margin-left:8px">再生成一份</a>
  <a href="/admin/leads?token={_html.escape(token)}"
     style="margin-left:14px;font-size:13px">留资线索 →</a>
</div>
<pre id="md">{_html.escape(md)}</pre>
<script>
function dlMd() {{
  const t = document.getElementById('md').textContent;
  const b = new Blob([t], {{type: 'text/markdown;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b);
  a.download = '{report.stock_code}_{report.report_year}_ESG报告.md';
  a.click();
}}
</script>
</div></body></html>""")


@app.get("/api/diagnose/listed", tags=["diagnose"])
def diagnose_listed_company(
    ticker: str = Query(..., description="Yahoo ticker，如 00700.HK"),
    company_name: str = Query(..., description="公司全称（中文或英文）"),
    stock_code: str = Query(..., description="港交所代码，如 00700"),
    year: int = Query(default=date.today().year),
):
    """上市公司快速诊断（获客钩子）。

    只聚合公开免费数据：Yahoo ESG 评分、SBTi 目标、新浪多机构评级、
    World Bank 国家基准、HKEX 最新 ESG 报告元数据。
    不下载 PDF、不调 LLM——1-3 秒完成。
    完整报告（对标差距 + 路线图 + LLM 叙述）需留资后人工生成。
    """
    errors: list[str] = []
    try:
        to_date = min(date(year, 12, 31), date.today()).isoformat()
        aggregate = _collect_aggregate(
            ticker, company_name, stock_code, year, f"{year - 1}-01-01", to_date,
            errors=errors,
        )
    except Exception as e:
        logging.warning("diagnose_listed 聚合失败: %s", e)
        errors.append(f"aggregate: {type(e).__name__}: {e}")
        aggregate = None

    summary = {
        "company": company_name,
        "ticker": ticker,
        "stock_code": stock_code,
        "year": year,
        "diagnosed_at": datetime.utcnow().isoformat() + "Z",
    }

    if aggregate:
        if aggregate.company:
            summary["industry"] = aggregate.company.industry or ""
            summary["sector"] = aggregate.company.sector or ""

        if aggregate.yahoo_esg:
            summary["yahoo_esg"] = {
                "environmental_score": aggregate.yahoo_esg.environmental_score,
                "social_score": aggregate.yahoo_esg.social_score,
                "governance_score": aggregate.yahoo_esg.governance_score,
                "total_score": aggregate.yahoo_esg.total_score,
                "rating": aggregate.yahoo_esg.rating,
            }

        if aggregate.sbti_target:
            summary["sbti"] = {
                "near_term_target_set": aggregate.sbti_target.near_term_target_set,
                "net_zero_target_set": aggregate.sbti_target.net_zero_target_set,
                "target_year_near_term": aggregate.sbti_target.target_year_near_term,
                "target_year_net_zero": aggregate.sbti_target.target_year_net_zero,
                "reduction_pct_near_term": aggregate.sbti_target.reduction_pct_near_term,
                "scope": aggregate.sbti_target.scope,
            }

        if aggregate.sina_esg:
            ratings = []
            for r in aggregate.sina_esg.ratings or []:
                ratings.append({
                    "agency": r.agency_name or "",
                    "rating": r.rating or "",
                    "date": r.rating_date or "",
                })
            summary["ratings"] = ratings

        if aggregate.country_emission and aggregate.country_emission.total_ghg:
            # World Bank total_ghg 单位是 MtCO2e，转为 kt
            summary["china_benchmark_kt"] = round(aggregate.country_emission.total_ghg * 1000, 1)

        if aggregate.hkex_reports:
            latest = max(aggregate.hkex_reports, key=lambda r: r.release_date)
            summary["latest_hkex_report"] = {
                "title": latest.report_title or "",
                "release_date": str(latest.release_date),
            }

        if aggregate.nzdpu_emissions:
            summary["nzdpu_scope"] = {
                "scope1": aggregate.nzdpu_emissions.scope1,
                "scope2": aggregate.nzdpu_emissions.scope2,
            }

    summary["next_step"] = "查看完整对标分析与改进路线图？留资即可获取"
    if errors:
        summary["source_errors"] = errors
    return summary


@app.get("/api/report/preview", tags=["diagnose"])
def report_preview(
    ticker: str = Query(..., description="Yahoo ticker，如 00700.HK"),
    company_name: str = Query("", description="公司名称"),
    stock_code: str = Query("", description="港交所代码，如 00700"),
    year: int = Query(0, description="报告年度，0 或非法值取上一年"),
):
    """上市公司报告「试读版」：只返回前两章，其余章节只给标题。

    公开数据生成，不下载 PDF、不调 LLM，约 3–5 秒；完整版走留资。
    """
    from .reporter import split_markdown_sections

    code = (stock_code or ticker.split(".")[0]).strip()
    y = year
    if not y or y < 2000 or y > date.today().year + 1:
        y = date.today().year - 1
    name = company_name.strip() or f"{code}.HK"

    aggregate = _collect_aggregate(ticker, name, code, y, f"{y - 1}-01-01", f"{y}-12-31")
    kpis = ESGKPIs(
        company_name=name,
        stock_code=code,
        report_year=y,
        source_pdf="",
        notes=["preview mode: public data only"],
    )
    report = generate_report(aggregate=aggregate, kpis=kpis, use_llm_narrative=False)

    sections = split_markdown_sections(report.markdown)
    # sections[0] 是标题与元信息，sections[1:] 才是正文章节；试读给「头部 + 前两章」
    free_count = min(3, len(sections))
    preview_md = "\n\n".join(sections[:free_count])
    locked = [s.split("\n", 1)[0].lstrip("# ").strip() for s in sections[free_count:]]

    return {
        "title": report.title,
        "company_name": report.company_name,
        "stock_code": report.stock_code,
        "report_year": report.report_year,
        "data_quality": report.overall_data_quality,
        "preview_markdown": preview_md,
        "locked_sections": locked,
        "free_sections": free_count,
        "total_sections": len(sections),
        "disclaimer": "试读版基于公开数据，未包含披露正文数字；完整版需人工复核。",
    }


@app.post("/api/diagnose/upload", tags=["diagnose"])
async def diagnose_uploaded_excel(
    file: UploadFile = File(..., description="填写完成的采集表（.xlsx）"),
):
    """非上市企业快速诊断（获客钩子）。

    只做：解析 Excel → 计算完整度 → 列出必填缺失 → 行业分位概览。
    不调 LLM、不生成完整报告——1-2 秒完成。
    """
    import uuid

    from .benchmark import analyze, normalize_industry
    from .client_data import derive_metrics, load_client_excel

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm 格式")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB")

    from .config import settings
    upload_dir = settings.cache_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)

    try:
        client = load_client_excel(target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    key = normalize_industry(client.profile.industry) if client.profile.industry else None
    analysis = analyze(client, key, derive_metrics(client)) if key else None

    summary = {
        "company": client.profile.company_name,
        "industry": client.profile.industry,
        "report_year": client.profile.report_year,
        "employees": client.profile.employees,
        "revenue_million": client.profile.revenue_million,
        "diagnosed_at": datetime.utcnow().isoformat() + "Z",
    }

    if analysis:
        # 复用 benchmark.analyze 已算出的完整度和缺失
        summary["completeness_pct"] = round(analysis.completeness * 100, 1)
        summary["missing_required_count"] = len(analysis.missing_required)
        summary["missing_required_codes"] = analysis.missing_required[:10]
        # 算 lagging 项：gap_pct 为负值（本企业落后于中位）
        lagging = [g for g in analysis.gaps if g.gap_pct is not None and g.gap_pct < 0]
        summary["gap_overview"] = {
            "total_metrics": len(analysis.gaps),
            "lagging_count": len(lagging),
            "worst_5": [
                {"code": g.code, "gap_pct": round(g.gap_pct, 1)}
                for g in sorted(lagging, key=lambda x: x.gap_pct)[:5]
            ],
        }

    summary["next_step"] = "查看 3 家同行业对标 + 详细差距分析 + A 级改进路线图？留资即可获取"
    return summary
