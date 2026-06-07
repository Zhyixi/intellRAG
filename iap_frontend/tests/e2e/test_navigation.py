"""E2E：登录后切换分页不阻塞。"""
from __future__ import annotations

from playwright.sync_api import Page, expect


def test_switch_notebook_documents_account(logged_in_page: Page):
    page = logged_in_page
    expect(page.get_by_role("button", name="新建對話 / New chat")).to_be_visible()
    expect(page.get_by_placeholder("輸入你的問題… / Ask a question…")).to_be_visible()

    page.get_by_role("link", name="文件 / Documents").click()
    expect(page.get_by_text("我的文件 / My Documents")).to_be_visible(timeout=30_000)

    page.get_by_role("link", name="個人管理 / Account").click()
    expect(page.get_by_text("個人管理 / Account").first).to_be_visible(timeout=30_000)

    page.get_by_role("link", name="我的筆記本 / My Notebook").click()
    expect(page.get_by_placeholder("輸入你的問題… / Ask a question…")).to_be_visible(timeout=30_000)
