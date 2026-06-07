"""Smoke：前端可访问、登录页渲染。"""
from __future__ import annotations

from playwright.sync_api import Page, expect

from conftest import BASE_URL, wait_for_streamlit


def test_login_page_loads(page: Page):
    page.goto(BASE_URL, wait_until="domcontentloaded")
    wait_for_streamlit(page)
    expect(page.get_by_text("IntelliAgnet")).to_be_visible()
    expect(page.get_by_role("button", name="登入 / Sign In")).to_be_visible()
    expect(page.get_by_role("tab", name="登入 / Sign In")).to_be_visible()
