#!/usr/bin/env python3
"""
IAP Platform — Web Search MCP (stdio).

Tools:
  - web_search: 依關鍵字搜尋網路並回傳標題、URL、摘要

Cursor 設定見專案根目錄 `.cursor/mcp.json`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 讓 MCP 能 import iap_backend.common
_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "iap_backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from common.web_search import web_search  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "mcp package required: pip install mcp duckduckgo-search",
        file=sys.stderr,
    )
    raise

mcp = FastMCP("iap-web-search")


@mcp.tool()
def search_web(query: str, max_results: int = 8) -> str:
    """Search the public web for a query. Returns JSON array of title, url, snippet."""
    items = web_search(query, max_results=max_results)
    return json.dumps(items, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
