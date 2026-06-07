"""CORS 設定：由環境變數控制，預設涵蓋常見本機前端 port，避免使用 '*' 搭配 credentials。"""
from __future__ import annotations

import os


def build_cors_origins() -> list[str]:
    """
    讀取 CORS_ORIGINS（逗號分隔）。未設定時使用 dev/prod 常見前端位址。

    注意：不可在 allow_credentials=True 時使用 '*'，否則瀏覽器會拒絕帶 cookie 的跨域請求。
    若前端網域不在清單內，請在 dev.env / prod.env 加上，例如：
      CORS_ORIGINS=http://10.x.x.x:9504,https://faca.example.com
    """
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # 預設：Angular/常見本機開發 + docker-compose 內 FRONTEND_PORT（dev 9504 / prod 8504）
    return [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:9504",
        "http://127.0.0.1:9504",
        "http://localhost:8504",
        "http://127.0.0.1:8504",
    ]
