"""V2 配置：独立端口与数据目录，避免与 V1 互相覆盖。

环境变量统一使用 V2_ 前缀，例如 V2_PORT=8020，
不会影响 V1 已有的 .env 配置。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

V2_ROOT = Path(__file__).resolve().parent.parent


class V2Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="V2_", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8020

    # 对外站点地址（用于 canonical / sitemap / RSS）；留空时按请求地址推导
    site_url: str = ""

    # V2 独立产出目录，不写 V1 的 data/cache
    data_dir: Path = V2_ROOT / "data"
    rules_dir: Path = Path(__file__).resolve().parent / "rules"

    def ensure_dirs(self) -> None:
        for sub in ("uploads", "reports", "carbon", "compliance"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


settings = V2Settings()
settings.ensure_dirs()
