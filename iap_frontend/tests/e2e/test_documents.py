"""E2E：文件页上传控件与进度区。"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, expect

FIXTURES = Path(__file__).parent / "fixtures"


def test_documents_page_has_uploader(logged_in_page: Page):
    page = logged_in_page
    page.get_by_role("link", name="文件 / Documents").click()
    expect(page.get_by_text("我的文件 / My Documents")).to_be_visible(timeout=30_000)
    expect(page.get_by_text("選擇文件 / Choose file")).to_be_visible()
    expect(page.get_by_role("button", name="Choose File")).to_be_visible()


def test_upload_small_txt_shows_progress_or_success(logged_in_page: Page):
    page = logged_in_page
    page.get_by_role("link", name="文件 / Documents").click()
    expect(page.get_by_text("我的文件 / My Documents")).to_be_visible(timeout=30_000)

    sample = FIXTURES / "sample.txt"
    sample.write_text("E2E sample document for indexing.\nLine 2.\n", encoding="utf-8")
    try:
        page.locator('input[type="file"]').set_input_files(str(sample))
        page.get_by_role("button", name="開始上傳並索引 / Upload & index").click()

        # 上传完成或进度区出现（embedding 可能较慢，允许两种结果）
        progress = page.get_by_text("索引進度 / Indexing progress")
        uploaded = page.get_by_text("已上傳", exact=False)
        expect(progress.or_(uploaded).first).to_be_visible(timeout=120_000)
    finally:
        if sample.exists():
            sample.unlink()
