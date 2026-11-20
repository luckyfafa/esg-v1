"""全局配置：从 .env 或环境变量读取，并提供默认值。

Render/云部署注意：
  - PORT 由 Render 注入；本地默认 8000
  - 临时文件缓存落到 /tmp/esg/（Render 免费层除 /tmp 外只读）
  - 凭证通过 RENDER 和本地 .env 注入，禁止写入明文
"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _tmp_base() -> Path:
    """优先用 RENDER_TMPDIR，其次系统 tmp，最后回退 ./data。"""
    render_tmp = os.environ.get("RENDER_TMPDIR")
    if render_tmp:
        return Path(render_tmp) / "esg"
    system_tmp = os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp"
    return Path(system_tmp) / "esg"


_TMP = _tmp_base()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 服务端口（Render 自动注入 PORT；本地默认 8000）
    port: int = int(os.environ.get("PORT", "8000"))

    # 凭证
    nzdpu_api_token: str = ""

    # LLM（OpenAI 兼容协议）
    llm_api_base: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # 路径（云环境全部落到 /tmp）
    cache_dir: Path = _TMP / "cache"
    hkex_download_dir: Path = _TMP / "hkex_reports"
    lead_dir: Path = _TMP / "leads"

    # 留资查看与转发
    # admin_token：访问 /admin/leads 的口令，未设置时该页面直接 403
    admin_token: str = ""
    # lead_webhook_url：收到留资时 POST 到该地址（飞书/钉钉/企业微信群机器人、
    # Zapier、n8n 等均可）。/tmp 会被清空，webhook 是线索不丢失的可靠方式。
    lead_webhook_url: str = ""

    # HTTP
    user_agent: str = "ESG-Report-Bot/0.1 contact@example.com"

    # 端点
    nzdpu_base_url: str = "https://api.nzdpu.com/v1"
    data_gov_hk_base_url: str = "https://app.data.gov.hk/v1"
    hkex_search_url: str = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
    sbti_base_url: str = "https://files.sciencebasedtargets.org/production/files/companies-excel.xlsx"
    sina_esg_base_url: str = "https://global.finance.sina.com.cn/api/openapi.php"
    worldbank_base_url: str = "https://api.worldbank.org/v2"

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hkex_download_dir.mkdir(parents=True, exist_ok=True)
        self.lead_dir.mkdir(parents=True, exist_ok=True)
        yf_cache = self.cache_dir / "yfinance"
        yf_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YF_CACHE_DIR", str(yf_cache))


settings = Settings()
settings.ensure_dirs()
