"""ESG 数据采集器：yfinance / NZDPU / HKEX / DATA.GOV.HK / SBTi / CDP / ClimateWatch

每个采集器带带 TTL 的文件缓存，避免重复请求。
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .schemas import (
    AgencyESGRating,
    CompanyInfo,
    CountryEmission,
    ESGScore,
    EmissionRecord,
    HKEXReportMeta,
    HKGovIndicator,
    MultiAgencyESG,
    SBTiTarget,
)

logger = logging.getLogger(__name__)


class BaseCollector:
    """采集器基类：带 TTL 的文件缓存 + URL 标准化 + 安全 HTTP。"""

    name: str = "base"
    # 默认缓存 TTL（秒）：ESG 数据年度更新，1 天足够；子类可覆盖
    cache_ttl: int = 86400

    def __init__(self) -> None:
        self.cache_dir: Path = settings.cache_dir / self.name
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------
    # 缓存层（带 TTL）
    # ----------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(":", "-")
        return self.cache_dir / f"{safe_key}.json"

    def _read_cache(self, key: str, ttl: Optional[int] = None) -> dict | list | None:
        """读缓存；若文件 mtime 超过 ttl 则视为过期返回 None。"""
        path = self._cache_path(key)
        if not path.exists():
            return None
        ttl = self.cache_ttl if ttl is None else ttl
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            logger.debug(f"缓存过期({age:.0f}s > {ttl}s): {path.name}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _write_cache(self, key: str, data: Any) -> None:
        # 目录可能被外部清掉，每次写前确保存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(key)
        path.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8"
        )

    # ----------------------------------------------------------
    # URL 标准化
    # ----------------------------------------------------------
    @staticmethod
    def _normalize_url(url: str) -> str:
        """标准化 URL：去首尾空白、校验协议。

        遵循经验：多接口聚合时必须对配置 URL 做归一，避免空白/包裹符导致请求失败。
        """
        url = (url or "").strip().strip('"').strip("'")
        if not url:
            return url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"非 http(s) URL: {url}")
        return url

    # ----------------------------------------------------------
    # 安全 HTTP（带结构化诊断）
    # ----------------------------------------------------------
    @staticmethod
    def _safe_get(
        client: httpx.Client, url: str, *, timeout: float = 30.0, **kwargs
    ) -> dict | list | None:
        """带结构化错误信息的 GET。失败时记录接口标识、状态码、响应片段。"""
        url = BaseCollector._normalize_url(url)
        try:
            r = client.get(url, timeout=timeout, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            snippet = (e.response.text or "")[:200]
            logger.warning(
                f"GET {url} 状态码 {e.response.status_code}: {snippet}"
            )
            return None
        except Exception as e:
            logger.warning(f"GET {url} 失败: {e}")
            return None

    @staticmethod
    def _safe_post(
        client: httpx.Client,
        url: str,
        *,
        json_body: Optional[dict] = None,
        timeout: float = 30.0,
        **kwargs,
    ) -> dict | list | None:
        """带结构化错误信息的 POST（JSON body）。"""
        url = BaseCollector._normalize_url(url)
        try:
            r = client.post(url, json=json_body, timeout=timeout, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            snippet = (e.response.text or "")[:200]
            logger.warning(
                f"POST {url} 状态码 {e.response.status_code}: {snippet}"
            )
            return None
        except Exception as e:
            logger.warning(f"POST {url} 失败: {e}")
            return None


class YFinanceCollector(BaseCollector):
    """Yahoo Finance ESG 分数（免费、无 key）。"""

    name = "yahoo"

    def fetch_company_info(self, ticker: str) -> CompanyInfo:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return CompanyInfo(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName") or ticker,
            industry=info.get("industry"),
            sector=info.get("sector"),
        )

    def fetch_esg_scores(self, ticker: str, year: int) -> ESGScore:
        cache_key = f"{ticker}_{year}"
        cached = self._read_cache(cache_key)
        if cached:
            return ESGScore(**cached)

        t = yf.Ticker(ticker)
        sus = t.sustainability
        flat: dict = {}
        if isinstance(sus, pd.DataFrame) and not sus.empty:
            flat = {k: (None if pd.isna(v) else v) for k, v in sus.iloc[:, 0].items()}

        score = ESGScore(
            source="yahoo",
            environmental_score=flat.get("environmentScore"),
            social_score=flat.get("socialScore"),
            governance_score=flat.get("governanceScore"),
            total_score=flat.get("totalEsg") or flat.get("esgRiskScore"),
            year=year,
            raw=flat,
        )
        self._write_cache(cache_key, score.model_dump())
        return score


class NZDPUCollector(BaseCollector):
    """净零数据公共事业 API：公司级温室气体排放（免费 token）。"""

    name = "nzdpu"

    def __init__(self) -> None:
        super().__init__()
        self.token = settings.nzdpu_api_token
        self.base_url = settings.nzdpu_base_url

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=4))
    def _get(self, path: str, params: dict | None = None) -> dict | None:
        if not self.token:
            logger.warning("NZDPU_API_TOKEN 未设置，跳过 NZDPU 请求")
            return None
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers) as c:
            return self._safe_get(c, f"{self.base_url}{path}", params=params)

    def fetch_company(self, company_name: str, year: int) -> list[dict]:
        cache_key = f"companies_{company_name}_{year}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached
        data = self._get("/companies", params={"name": company_name, "year": year})
        companies = (data or {}).get("data", []) if isinstance(data, dict) else []
        self._write_cache(cache_key, companies)
        return companies

    def fetch_emissions(self, company_name: str, year: int) -> EmissionRecord:
        companies = self.fetch_company(company_name, year)
        if not companies:
            return EmissionRecord(source="nzdpu", year=year)
        first = companies[0] if isinstance(companies[0], dict) else {}
        emissions = first.get("emissions", {}) if isinstance(first, dict) else {}
        return EmissionRecord(
            source="nzdpu",
            scope1=emissions.get("scope1"),
            scope2=emissions.get("scope2_location_based") or emissions.get("scope2"),
            scope3=emissions.get("scope3"),
            year=year,
            raw=first,
        )


class HKEXCollector(BaseCollector):
    """港交所 ESG 报告抓取（基于 hkexnews titleSearchServlet.do 接口）。

    说明：HKEX 的 `stockId` 参数是内部数字 ID，不是股票代码；
    这里用 `stockId=-1` 拉全部，再按 `STOCK_CODE` 字段本地过滤。
    `t1code=40000 / t2code=40400` 对应 Financial Statements/ESG Information
    大类下的 ESG Information/Report 子类。
    """

    name = "hkex"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=4))
    def search_esg_reports(
        self, stock_code: str, from_date: str, to_date: str
    ) -> list[HKEXReportMeta]:
        """搜索指定港股代码的 ESG 报告。

        stock_code: 港交所原始代码，如 "09988" / "00005"
        from_date / to_date: "YYYY-MM-DD"
        """
        # HKEX STOCK_CODE 字段固定为 5 位带前导 0
        target_code = stock_code.lstrip("0").zfill(5)
        # HKEX 接口对 toDate > today 会返回 result=None，这里截断
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d").date()
            if to_dt > date.today():
                to_date = date.today().isoformat()
        except ValueError:
            pass
        cache_key = f"search_{target_code}_{from_date}_{to_date}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            return [HKEXReportMeta(**m) for m in cached]

        # HKEX titleSearchServlet 的 t1code=40000 + t2code=-2 能拉到 ESG 大类下
        # 所有子类的全部披露（ESG Report + Sustainability Report + CSR Report 等）。
        # 单独按 t2code=40400 抓会漏掉归在其他子类下的 Sustainability Report，
        # 所以这里固定 t2code=-2 拉全量大类数据，再本地按 STOCK_CODE + 标题关键词过滤。
        headers = {
            # HKEX 反爬识别 "ESG-Report-Bot" 会返回空 result，改用常见浏览器 UA
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            # Referer 必须用高级搜索页 URL，否则某些股票返回空
            "Referer": "https://www1.hkexnews.hk/listedco/listconews/advancedsearch/search_active_main.aspx",
            "Origin": "https://www1.hkexnews.hk",
            "Accept-Language": "en-US,en;q=0.9",
        }
        params = {
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": "-1",
            "documentType": "-1",
            "fromDate": from_date.replace("-", ""),
            "toDate": to_date.replace("-", ""),
            "title": "",
            "searchType": "1",
            "t1code": "40000",   # Financial Statements/ESG Information 大类
            "t2Gcode": "-2",
            "t2code": "-2",      # 大类下所有子类
            "rowRange": "1-2500",
        }
        results: list[HKEXReportMeta] = []
        try:
            with httpx.Client(headers=headers) as c:
                r = c.get(settings.hkex_search_url, params=params, timeout=60.0)
                r.raise_for_status()
                data = r.json()
                raw = data.get("result", "[]")
                if raw is None:
                    raw = []
                elif isinstance(raw, str):
                    raw = json.loads(raw) if raw else []
                for item in raw or []:
                    # HKEX 的 STOCK_CODE 可能是单代码 "00700"，也可能多代码
                    # 用 "<br/>" 连接（同一发行人多只衍生品）。
                    stock_codes = re.split(
                        r"<\s*br\s*/?\s*>",
                        item.get("STOCK_CODE") or "",
                        flags=re.IGNORECASE,
                    )
                    stock_codes = [s.strip() for s in stock_codes if s.strip()]
                    if target_code not in stock_codes:
                        continue
                    # 标题关键词过滤：只保留 ESG/Sustainability/CSR/Governance 报告
                    title_lower = (item.get("TITLE") or "").lower()
                    if not any(
                        kw in title_lower
                        for kw in (
                            "esg", "sustainability", "csr",
                            "environmental, social",
                            "environmental social",
                            "governance report",
                        )
                    ):
                        continue
                    file_url = (
                        f"https://www1.hkexnews.hk{item.get('FILE_LINK', '')}"
                    )
                    # DATE_TIME 形如 "22/03/2024 07:30"
                    try:
                        release = datetime.strptime(
                            item.get("DATE_TIME", "").split()[0], "%d/%m/%Y"
                        ).date()
                    except (ValueError, IndexError):
                        release = date.today()
                    # STOCK_NAME 同样可能是 "<br/>" 拼接，取第一个
                    raw_name = item.get("STOCK_NAME", "")
                    first_name = re.split(
                        r"<\s*br\s*/?\s*>", raw_name, flags=re.IGNORECASE
                    )[0].strip()
                    results.append(
                        HKEXReportMeta(
                            stock_code=target_code,
                            stock_name=html_lib.unescape(first_name),
                            report_title=html_lib.unescape(item.get("TITLE", "")),
                            release_date=release,
                            file_url=file_url,
                        )
                    )
        except Exception as e:
            logger.warning(f"HKEX 搜索失败: {e}")
        self._write_cache(cache_key, [m.model_dump() for m in results])
        return results

    def download_report(self, meta: HKEXReportMeta) -> Path:
        target = (
            settings.hkex_download_dir
            / f"{meta.stock_code}_{meta.release_date}.pdf"
        )
        if target.exists():
            meta.file_path = str(target)
            return target
        headers = {"User-Agent": settings.user_agent}
        with httpx.Client(headers=headers, follow_redirects=True) as c:
            with c.stream("GET", meta.file_url) as r:
                r.raise_for_status()
                with target.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        meta.file_path = str(target)
        return target


class HKGovCollector(BaseCollector):
    """香港政府开放数据 data.gov.hk。

    走 CKAN API `package_show` 拿 dataset 元数据 → 取 resources[0].url → 下 JSON。
    HK EPD 的 GHG JSON 末尾常缺一个 `}`，解析时容错。
    """

    name = "hkgov"
    ckan_base = "https://data.gov.hk/en-data/api/3/action"

    def get_dataset_resources(self, dataset_id: str) -> list[dict]:
        cache_key = f"pkg_{dataset_id}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached
        with httpx.Client() as c:
            data = self._safe_get(
                c, f"{self.ckan_base}/package_show", params={"id": dataset_id}
            )
        rs = (data or {}).get("result", {}).get("resources", []) if data else []
        self._write_cache(cache_key, rs)
        return rs

    @staticmethod
    def _parse_lenient_json(text: str) -> Any:
        """容错解析 JSON：补齐末尾缺失的 `}` / `]`。

        EPD 的 GHG JSON 末尾形如 `...0.011\r\n]`，
        最后一个对象缺闭合 `}`，需在最后的 `]` 前插入 `}`。
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            s = text.rstrip()
            # 如果以 `]` 结尾但 `]` 之前不是 `}`，则插入 `}`
            if s.endswith("]") and not s[:-1].rstrip().endswith("}"):
                s = s[:-1].rstrip() + "}]"
            elif not s.endswith("]") and not s.endswith("}"):
                s = s + "}"
            return json.loads(s)

    def fetch_ghg_emissions(self, year: int) -> HKGovIndicator | None:
        """获取香港温室气体排放数据集（行业基准因子）。

        返回 dataset 中匹配指定年份的记录；找不到时返回最近一年。
        """
        dataset_id = (
            "hk-epd-climatechg-greenhouse-gas-emissions-and-carbon-intensity"
        )
        resources = self.get_dataset_resources(dataset_id)
        if not resources:
            return None
        url = resources[0].get("url")
        if not url:
            return None
        with httpx.Client(follow_redirects=True) as c:
            r = c.get(url, timeout=30.0)
            r.raise_for_status()
            rows = self._parse_lenient_json(r.text)
        if not isinstance(rows, list):
            rows = [rows]
        # 找匹配年份；找不到则取最后一条（最近年）
        target = next(
            (row for row in rows if str(row.get("Report_Year")) == str(year)),
            rows[-1] if rows else None,
        )
        if not target:
            return None
        return HKGovIndicator(
            dataset=dataset_id,
            indicator="total_ghg_emissions",
            year=int(target.get("Report_Year", year)),
            value=float(target.get("Total_GHG_emissions", 0)),
            unit="ktCO2e",
            raw=target,
        )


# ============================================================
# SBTi（科学碳目标倡议）
# ============================================================

class SBTiCollector(BaseCollector):
    """科学碳目标倡议（Science Based Targets initiative）数据采集。

    SBTi 是判断公司是否设定可信减排路径的关键 ESG 指标。
    数据来源：SBTi 官方每周更新的公司目标 Excel 清单（无需 token）。
    下载地址: https://files.sciencebasedtargets.org/production/files/companies-excel.xlsx

    说明：数据每周四更新，本采集器做了容错——若下载失败
    返回 targets_committed=False 的空记录，不阻塞主流程。
    """

    name = "sbti"
    cache_ttl = 86400 * 7  # SBTi 目标很少变动，缓存 7 天

    def __init__(self) -> None:
        super().__init__()
        self.base_url = settings.sbti_base_url

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=3))
    def _fetch_companies(self) -> list[dict]:
        """拉取 SBTi 全量公司目标清单并缓存。

        官方提供 xlsx，用 pandas 读取为 dict 列表；字段名做小写化归一。
        """
        cache_key = "companies_full"
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached if isinstance(cached, list) else []
        url = self._normalize_url(self.base_url)
        headers = {"User-Agent": settings.user_agent}
        companies: list[dict] = []
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=45.0) as c:
                r = c.get(url)
                r.raise_for_status()
            # 写入临时 xlsx，pandas 读取
            tmp = self.cache_dir / "companies.xlsx"
            tmp.write_bytes(r.content)
            df = pd.read_excel(tmp, sheet_name=0)
            # 列名归一：去空白、转小写、空格转下划线
            df.columns = [
                re.sub(r"\s+", "_", str(c).strip().lower()) for c in df.columns
            ]
            # 逐条把 NaN 转 None（不用 DataFrame.where(..., None)，pandas 3 行为不一致）
            companies = [
                {k: (None if (isinstance(v, float) and pd.isna(v)) else v)
                 for k, v in rec.items()}
                for rec in df.to_dict(orient="records")
            ]
        except Exception as e:
            logger.warning(f"SBTi Excel 下载/解析失败: {e}")
            companies = []
        # 只有拿到数据才写缓存，避免一次网络抖动把空结果缓存 7 天
        if companies:
            self._write_cache(cache_key, companies)
        return companies

    @staticmethod
    def _name_match(a: str, b: str) -> bool:
        """公司名宽松匹配：去标点、转小写、子串包含。"""
        def norm(s: str) -> str:
            return re.sub(r"[^\w\s]", "", s or "").lower().strip()
        na, nb = norm(a), norm(b)
        if not na or not nb:
            return False
        # 互相包含即视为匹配（处理公司全称/简称差异）
        return na in nb or nb in na

    def fetch_target(self, company_name: str, year: int) -> SBTiTarget:
        """查询指定公司的 SBTi 目标状态。"""
        target = SBTiTarget(company_name=company_name)
        try:
            companies = self._fetch_companies()
        except Exception as e:
            logger.warning(f"SBTi 拉取失败: {e}")
            return target

        # 字段路径映射：SBTi Excel 字段名做了归一，多路径兜底
        def pick(row: dict, *keys: str) -> Any:
            for k in keys:
                if k in row and row[k] not in (None, "", []):
                    return row[k]
            return None

        for row in companies:
            name = pick(row, "company_name", "name", "organization", "organisation") or ""
            if not self._name_match(str(name), company_name):
                continue
            # 实际 SBTi Excel 字段：
            #   near_term_status: "Validated Targets" / "Commitment to Set Targets" / NaN
            #   net_zero_status: 已设定净零目标的状态文本 / NaN
            near_status = str(pick(row, "near_term_status") or "").lower()
            netzero_status = pick(row, "net_zero_status")
            near_validated = "validated" in near_status
            near_committed = "commit" in near_status
            has_target = near_validated or near_committed or bool(netzero_status)
            target.targets_committed = has_target
            target.near_term_target_set = near_validated
            target.net_zero_target_set = bool(netzero_status)
            # 目标年份
            ty = pick(row, "near_term_target_year", "target_year")
            if isinstance(ty, (int, float)) and not pd.isna(ty):
                target.target_year_near_term = int(ty)
            nzy = pick(row, "net_zero_year", "long_term_target_year")
            if isinstance(nzy, (int, float)) and not pd.isna(nzy):
                target.target_year_net_zero = int(nzy)
            # 减排百分比：从 full_target_language 文本里解析 "... X% ..." 形式
            lang = str(pick(row, "full_target_language") or "")
            m = re.search(r"reduce[^%]*?([\d.]+)\s*%", lang, re.IGNORECASE)
            if m:
                target.reduction_pct_near_term = float(m.group(1))
            # 范围：从 full_target_language 提取 scope 1/2/3
            scopes = []
            if re.search(r"scope 1", lang, re.IGNORECASE):
                scopes.append("1")
            if re.search(r"scope 2", lang, re.IGNORECASE):
                scopes.append("2")
            if re.search(r"scope 3", lang, re.IGNORECASE):
                scopes.append("3")
            if scopes:
                target.scope = "+".join(scopes)
            target.raw = row if isinstance(row, dict) else {}
            break
        return target


# ============================================================
# 新浪财经 ESG 评级中心（多机构 ESG 评级，替代 CDP）
# ============================================================

class SinaESGCollector(BaseCollector):
    """新浪财经 ESG 评级中心数据采集。

    免费、无需 token，支持港股(hkXXXXXX)与 A 股(sh/szXXXXXX)。
    聚合 MSCI、标普、中证指数、国证指数、中国国新、中债、商道融绿、
    华证指数、秩鼎、妙盈、盟浪、晨星等多家机构的 ESG 评级，
    部分机构还提供 E/S/G 分项得分。

    数据来源：https://finance.sina.com.cn/esg/grade.shtml
    API: https://global.finance.sina.com.cn/api/openapi.php/EsgService.getEsgStockInfo
    """

    name = "sina_esg"
    cache_ttl = 86400  # ESG 评级更新频率不一，缓存 1 天

    def __init__(self) -> None:
        super().__init__()
        self.base_url = settings.sina_esg_base_url

    @staticmethod
    def to_sina_symbol(stock_code: str) -> str:
        """将港交所/A股代码转为新浪格式。

        规则：
        - 已是新浪格式(hk/sh/sz开头)直接返回
        - 5位数字 -> 港股 hkXXXXX（如 09988 -> hk09988, 00700 -> hk00700）
        - 6位数字 6/9 开头 -> 沪市 shXXXXXX（如 600519 -> sh600519）
        - 6位数字 0/3 开头 -> 深市 szXXXXXX（如 000001 -> sz000001, 300750 -> sz300750）
        - 其他 -> 按港股处理
        """
        code = stock_code.strip().lower()
        # 已经是新浪格式
        if code.startswith(("hk", "sh", "sz")):
            return code
        # 港股代码为 5 位（含前导零），A 股为 6 位
        if len(code) == 5 and code.isdigit():
            return f"hk{code}"
        if len(code) == 6 and code.isdigit():
            if code.startswith(("6", "9")):
                return f"sh{code}"
            if code.startswith(("0", "3")):
                return f"sz{code}"
        # 兜底：按港股处理
        return f"hk{code.zfill(5)}"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=3))
    def fetch_ratings(self, stock_code: str, company_name: str = "") -> MultiAgencyESG:
        """查询指定股票的多机构 ESG 评级。

        stock_code: 港交所代码(如 09988)或新浪格式(如 hk09988/sh600519)
        """
        symbol = self.to_sina_symbol(stock_code)
        result = MultiAgencyESG(
            company_name=company_name,
            stock_symbol=symbol,
        )
        cache_key = symbol
        cached = self._read_cache(cache_key)
        if cached:
            return MultiAgencyESG(**cached)

        url = self._normalize_url(f"{self.base_url}/EsgService.getEsgStockInfo")
        params = {"symbol": symbol}
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://finance.sina.com.cn/",
        }
        try:
            with httpx.Client(headers=headers, follow_redirects=True) as c:
                data = self._safe_get(c, url, timeout=15.0, params=params)
        except Exception as e:
            logger.warning(f"新浪ESG查询失败: {e}")
            return result

        # 解析响应: {result: {status: {code:0}, data: {code:200000, info:[...]}}}
        info: list[dict] = []
        if isinstance(data, dict):
            inner = data.get("result") or {}
            if isinstance(inner, dict):
                data_block = inner.get("data") or {}
                if isinstance(data_block, dict):
                    info = data_block.get("info") or []
        for item in info:
            if not isinstance(item, dict):
                continue
            agency_name = str(item.get("agency_name") or "").strip()
            raw_score = str(item.get("esg_score") or "").strip()
            raw_level = str(item.get("esg_level") or "").strip()
            rating = raw_level if raw_level and raw_level != "-" else (
                raw_score if raw_score and raw_score != "-" else None
            )
            # 解析 E/S/G 分项
            e_score = s_score = g_score = None
            for d in item.get("detail") or []:
                if not isinstance(d, dict):
                    continue
                name = str(d.get("name") or "")
                val = d.get("score")
                if val in (None, "-", ""):
                    continue
                try:
                    fval = float(val)
                except (ValueError, TypeError):
                    continue
                if "环境" in name:
                    e_score = fval
                elif "社会" in name or "责任" in name:
                    s_score = fval
                elif "治理" in name:
                    g_score = fval
            result.ratings.append(AgencyESGRating(
                agency_name=agency_name,
                rating=rating,
                esg_score=raw_score if raw_score != "-" else None,
                environmental_score=e_score,
                social_score=s_score,
                governance_score=g_score,
                rating_date=str(item.get("esg_dt")) if item.get("esg_dt") not in (None, "-") else None,
                raw=item,
            ))
        self._write_cache(cache_key, result.model_dump())
        return result


# ============================================================
# World Bank（国家级排放基准）
# ============================================================

class WorldBankCollector(BaseCollector):
    """世界银行气候指标采集（国家级温室气体排放）。

    使用 World Bank Open Data API 的 EN.ATM.GHGT.KT.CE 指标
    （全行业温室气体排放总量，单位 ktCO2e）。
    免费开放数据，无需 token，用于为公司提供所在国/地区的宏观排放基准。
    """

    name = "worldbank"
    cache_ttl = 86400 * 30  # 国家级排放数据年度更新，缓存 30 天

    def __init__(self) -> None:
        super().__init__()
        self.base_url = settings.worldbank_base_url

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=3))
    def fetch_country_emissions(
        self, country: str = "China", year: int | None = None
    ) -> CountryEmission | None:
        """获取指定国家的温室气体排放数据。

        country: 国家名（英文），如 "China" / "Hong Kong" / "USA"
        year: 目标年份；None 时取最新可用年份
        """
        cache_key = f"{country}_{year or 'latest'}"
        cached = self._read_cache(cache_key)
        if cached:
            return CountryEmission(**cached)
        # 国家名 → ISO2（World Bank 用 ISO2）
        iso2_map = {
            "china": "CN", "cn": "CN",
            "hong kong": "HK", "hongkong": "HK", "hk": "HK",
            "united states": "US", "usa": "US", "us": "US",
            "japan": "JP", "jp": "JP",
            "european union": "EU", "eu": "EU",
            "india": "IN", "in": "IN",
        }
        iso2 = iso2_map.get(country.lower().strip(), country.upper().strip()[:2])
        # CC.GHG.EMSE.IL = Total GHG emissions (Mt CO2 eq, excluding LUCF)
        indicator = "CC.GHG.EMSE.IL"
        url = self._normalize_url(
            f"{self.base_url}/country/{iso2}/indicator/{indicator}"
        )
        # 限定 1990-2025，避免拉取大量未来空值年份
        params = {"format": "json", "per_page": 100, "date": "1990:2025"}
        headers = {"Accept": "application/json", "User-Agent": settings.user_agent}
        try:
            with httpx.Client(headers=headers, follow_redirects=True) as c:
                data = self._safe_get(c, url, timeout=30.0, params=params)
        except Exception as e:
            logger.warning(f"World Bank 查询失败: {e}")
            return None
        # World Bank 返回 [meta, [{indicator, country, date, value, ...}]]
        rows: list[dict] = []
        if isinstance(data, list) and len(data) >= 2:
            rows = data[1] if isinstance(data[1], list) else []
        elif isinstance(data, dict):
            rows = data.get("data") or []
        if not rows:
            return None
        # 构建 year -> value 映射（单位 ktCO2e）
        emissions: dict[int, float] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            y = item.get("date")
            v = item.get("value")
            if isinstance(y, (int, str)) and isinstance(v, (int, float)):
                try:
                    emissions[int(y)] = float(v)
                except (ValueError, TypeError):
                    pass
        if not emissions:
            return None
        years = sorted(emissions.keys(), reverse=True)
        target_year = year if year in emissions else years[0]
        result = CountryEmission(
            country=country,
            year=target_year,
            total_ghg=emissions.get(target_year),
            raw={"iso2": iso2, "indicator": indicator, "unit": "MtCO2e", "emissions": emissions},
        )
        self._write_cache(cache_key, result.model_dump())
        return result


# 单例
yfinance_collector = YFinanceCollector()
nzdpu_collector = NZDPUCollector()
hkex_collector = HKEXCollector()
hkgov_collector = HKGovCollector()
sbti_collector = SBTiCollector()
sina_esg_collector = SinaESGCollector()
worldbank_collector = WorldBankCollector()
