#!/usr/bin/env python3
"""
IAP Platform — Web Search MCP launcher (stdio).

Preferred path: Docker container iap_backend_dev → /app/mcp/web_search_server.py
Fallback: local Python with mcp + duckduckgo-search installed.

Cursor 設定見專案根目錄 `.cursor/mcp.json`。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent / "run_web_search_mcp.py"
sys.argv[0] = str(_RUNNER)
runpy.run_path(str(_RUNNER), run_name="__main__")
