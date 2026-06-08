#!/usr/bin/env python3
"""Web Search MCP (stdio) — runs inside iap_backend container or locally with deps."""
from __future__ import annotations

import json

from common.web_search import web_search

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit(
        "mcp package required: pip install mcp duckduckgo-search"
    ) from exc

mcp = FastMCP("iap-web-search")


@mcp.tool()
def search_web(query: str, max_results: int = 8) -> str:
    """Search the public web for a query. Returns JSON array of title, url, snippet."""
    items = web_search(query, max_results=max_results)
    return json.dumps(items, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")