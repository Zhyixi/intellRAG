"""網路搜尋（SearXNG → DuckDuckGo fallback），供 API 與 MCP 共用。"""
from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SEARCH_BACKENDS = ("html", "duckduckgo", "lite")
_DEFAULT_PROVIDER = "chain"
_DEFAULT_SEARXNG_URL = "http://iap_searxng:8080"
_DEFAULT_TIMEOUT = 15.0


def _web_search_provider() -> str:
    return (os.getenv("WEB_SEARCH_PROVIDER") or _DEFAULT_PROVIDER).strip().lower()


def _searxng_url() -> str:
    return (os.getenv("SEARXNG_URL") or _DEFAULT_SEARXNG_URL).rstrip("/")


def _web_search_timeout() -> float:
    raw = os.getenv("WEB_SEARCH_TIMEOUT", str(_DEFAULT_TIMEOUT))
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def _normalize_item(item: dict[str, Any]) -> dict[str, str]:
    return {
        "title": item.get("title") or "",
        "url": item.get("href") or item.get("link") or item.get("url") or "",
        "snippet": item.get("body") or item.get("snippet") or item.get("content") or "",
    }


def _search_with_searxng(query: str, max_results: int) -> list[dict[str, Any]]:
    url = f"{_searxng_url()}/search"
    timeout = _web_search_timeout()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(
            url,
            params={"q": query, "format": "json", "categories": "general"},
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[dict[str, Any]] = []
    for row in (data.get("results") or [])[:max_results]:
        normalized = _normalize_item(row)
        if normalized["url"]:
            results.append(normalized)
    return results


def _search_with_ddgs(query: str, max_results: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]

    last_error: Exception | None = None
    for backend in _SEARCH_BACKENDS:
        try:
            with DDGS() as ddgs:
                rows = ddgs.text(query, max_results=max_results, backend=backend)
            results = [_normalize_item(row) for row in rows]
            if results:
                return results
        except TypeError:
            try:
                with DDGS() as ddgs:
                    rows = ddgs.text(query, max_results=max_results)
                results = [_normalize_item(row) for row in rows]
                if results:
                    return results
            except Exception as exc:
                last_error = exc
        except Exception as exc:
            last_error = exc
            logger.warning("web_search backend=%s failed: %s", backend, exc)
    if last_error:
        raise last_error
    return []


def web_search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """
    搜尋公開網路並回傳結構化結果。
    每筆: title, url, snippet

    Provider（WEB_SEARCH_PROVIDER）:
    - chain（預設）: SearXNG → DuckDuckGo fallback
    - searxng: 僅 SearXNG
    - ddgs: 僅 DuckDuckGo
    """
    query = (query or "").strip()
    if not query:
        return []

    max_results = max(1, min(max_results, 20))
    provider = _web_search_provider()

    try:
        if provider == "ddgs":
            return _search_with_ddgs(query, max_results)
        if provider == "searxng":
            return _search_with_searxng(query, max_results)

        try:
            results = _search_with_searxng(query, max_results)
            if results:
                return results
            logger.info(
                "web_search searxng returned empty for query=%r, trying ddgs",
                query,
            )
        except Exception as exc:
            logger.warning("web_search searxng failed, falling back to ddgs: %s", exc)

        return _search_with_ddgs(query, max_results)
    except ImportError as exc:
        raise RuntimeError(
            "duckduckgo-search (or ddgs) is not installed. Add it to requirements.in"
        ) from exc
    except Exception as exc:
        logger.exception("web_search failed for query=%r: %s", query, exc)
        raise


def mcp_search_web(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """
    Invoke the same logic as MCP server tool ``search_web`` (iap-web-search).
    Emits a Langfuse tool span when tracing is enabled.
    """
    try:
        from common.langfuse_tracing import langfuse_configured, observe_tool_span
    except ImportError:
        langfuse_configured = lambda: False  # type: ignore[misc, assignment]
        observe_tool_span = nullcontext  # type: ignore[assignment]

    ctx = (
        observe_tool_span(
            name="search_web",
            input={"query": query, "max_results": max_results},
            metadata={"source": "iap-web-search-mcp"},
        )
        if langfuse_configured()
        else nullcontext()
    )
    with ctx as observation:
        results = web_search(query, max_results=max_results)
        if observation is not None:
            observation.update(output={"result_count": len(results)})
        return results
