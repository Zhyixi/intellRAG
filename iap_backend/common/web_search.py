"""網路搜尋（DuckDuckGo），供 API 與 MCP 共用。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def web_search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """
    使用 DuckDuckGo 搜尋並回傳結構化結果。
    每筆: title, url, snippet
    """
    query = (query or "").strip()
    if not query:
        return []

    try:
        from duckduckgo_search import DDGS
    except ImportError as exc:
        raise RuntimeError(
            "duckduckgo-search is not installed. Add it to requirements.in"
        ) from exc

    results: list[dict[str, Any]] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title": item.get("title") or "",
                        "url": item.get("href") or item.get("link") or "",
                        "snippet": item.get("body") or item.get("snippet") or "",
                    }
                )
    except Exception as exc:
        logger.exception("web_search failed for query=%r: %s", query, exc)
        raise

    return results
