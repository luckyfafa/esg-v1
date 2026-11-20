"""启动入口：同时支持本地 uvicorn 和 Render/Fly 等云平台。

Render 会注入 PORT 环境变量；本地默认 8000。
启动：python start.py
"""
import os
import sys

import uvicorn

if __name__ == "__main__":
    from app.config import settings

    port = int(os.environ.get("PORT", settings.port))
    host = os.environ.get("HOST", "0.0.0.0")
    sys.stderr.write(f"ESG V1 starting on http://{host}:{port}\n")
    uvicorn.run("app.main:app", host=host, port=port)
