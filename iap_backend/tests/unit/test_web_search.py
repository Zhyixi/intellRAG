from __future__ import annotations

import pytest

from common import web_search as ws

pytestmark = pytest.mark.unit


def test_web_search_empty_query():
    assert ws.web_search("") == []
    assert ws.web_search("   ") == []


def test_web_search_chain_prefers_searxng(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "chain")
    calls: list[str] = []

    def fake_searxng(query: str, max_results: int):
        calls.append("searxng")
        return [{"title": "A", "url": "http://a.example", "snippet": "snippet a"}]

    def fake_ddgs(query: str, max_results: int):
        calls.append("ddgs")
        return [{"title": "B", "url": "http://b.example", "snippet": "snippet b"}]

    monkeypatch.setattr(ws, "_search_with_searxng", fake_searxng)
    monkeypatch.setattr(ws, "_search_with_ddgs", fake_ddgs)

    results = ws.web_search("hello", max_results=5)
    assert len(results) == 1
    assert results[0]["title"] == "A"
    assert calls == ["searxng"]


def test_web_search_chain_falls_back_to_ddgs_on_error(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "chain")
    calls: list[str] = []

    def fake_searxng(query: str, max_results: int):
        calls.append("searxng")
        raise ConnectionError("searxng down")

    def fake_ddgs(query: str, max_results: int):
        calls.append("ddgs")
        return [{"title": "B", "url": "http://b.example", "snippet": "snippet b"}]

    monkeypatch.setattr(ws, "_search_with_searxng", fake_searxng)
    monkeypatch.setattr(ws, "_search_with_ddgs", fake_ddgs)

    results = ws.web_search("hello")
    assert results[0]["title"] == "B"
    assert calls == ["searxng", "ddgs"]


def test_web_search_chain_falls_back_when_searxng_empty(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "chain")
    calls: list[str] = []

    def fake_searxng(query: str, max_results: int):
        calls.append("searxng")
        return []

    def fake_ddgs(query: str, max_results: int):
        calls.append("ddgs")
        return [{"title": "B", "url": "http://b.example", "snippet": "snippet b"}]

    monkeypatch.setattr(ws, "_search_with_searxng", fake_searxng)
    monkeypatch.setattr(ws, "_search_with_ddgs", fake_ddgs)

    results = ws.web_search("hello")
    assert results[0]["title"] == "B"
    assert calls == ["searxng", "ddgs"]


def test_web_search_searxng_only(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "searxng")
    monkeypatch.setattr(
        ws,
        "_search_with_searxng",
        lambda q, n: [{"title": "S", "url": "http://s.example", "snippet": "s"}],
    )

    def fail_ddgs(query: str, max_results: int):
        raise AssertionError("ddgs should not be called")

    monkeypatch.setattr(ws, "_search_with_ddgs", fail_ddgs)

    results = ws.web_search("hello")
    assert results[0]["title"] == "S"


def test_normalize_item_maps_searxng_fields():
    item = ws._normalize_item(
        {"title": "T", "url": "http://x", "content": "body text"}
    )
    assert item == {"title": "T", "url": "http://x", "snippet": "body text"}
