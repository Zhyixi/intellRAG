"""Tests for etl/pdf_pipeline.py — pure helper functions (no file I/O required)."""
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# is_supported_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", [
    "report.pdf", "slides.pptx", "document.docx", "data.xlsx",
    "notes.txt", "readme.md", "archive.odt", "form.rtf",
    "sheet.ods", "present.odp", "legacy.doc", "old.ppt", "table.xls",
])
def test_is_supported_file_accepts_valid_extensions(filename):
    from etl.pdf_pipeline import is_supported_file
    assert is_supported_file(filename) is True


@pytest.mark.parametrize("filename", [
    "image.png", "photo.jpg", "archive.zip", "script.py",
    "binary.exe", "video.mp4", "data.csv",
])
def test_is_supported_file_rejects_invalid_extensions(filename):
    from etl.pdf_pipeline import is_supported_file
    assert is_supported_file(filename) is False


def test_is_supported_file_case_insensitive():
    from etl.pdf_pipeline import is_supported_file
    assert is_supported_file("REPORT.PDF") is True
    assert is_supported_file("Slides.PPTX") is True


# ---------------------------------------------------------------------------
# check_garbage_text
# ---------------------------------------------------------------------------

def test_check_garbage_text_clean_text_is_not_garbage():
    from etl.pdf_pipeline import check_garbage_text
    is_garbage, ratio = check_garbage_text("This is a normal sentence with no garbage.")
    assert is_garbage is False
    assert ratio < 0.1


def test_check_garbage_text_empty_is_garbage():
    from etl.pdf_pipeline import check_garbage_text
    is_garbage, ratio = check_garbage_text("")
    assert is_garbage is True
    assert ratio == 1.0


def test_check_garbage_text_high_ratio_is_garbage():
    from etl.pdf_pipeline import check_garbage_text
    # Control characters (0x01-0x08) count as garbage
    garbage = "\x01\x02\x03\x04\x05" * 20 + "ok"
    is_garbage, ratio = check_garbage_text(garbage)
    assert is_garbage is True
    assert ratio > 0.1


def test_check_garbage_text_custom_threshold():
    from etl.pdf_pipeline import check_garbage_text
    text = "normal" + "\x01"  # 1 garbage in 7 chars ≈ 14%
    is_garbage_default, _ = check_garbage_text(text, threshold=0.1)
    is_garbage_high, _ = check_garbage_text(text, threshold=0.5)
    assert is_garbage_default is True   # 14% > 10%
    assert is_garbage_high is False     # 14% < 50%


# ---------------------------------------------------------------------------
# should_skip_page_content
# ---------------------------------------------------------------------------

def test_should_skip_page_content_toc_simplified():
    from etl.pdf_pipeline import should_skip_page_content
    assert should_skip_page_content("目录\n1. 引言 ........ 1") is True


def test_should_skip_page_content_toc_traditional():
    from etl.pdf_pipeline import should_skip_page_content
    assert should_skip_page_content("目錄\n第一章 ........ 1") is True


def test_should_skip_page_content_toc_english():
    from etl.pdf_pipeline import should_skip_page_content
    assert should_skip_page_content("Table of Contents\n1. Introduction") is True


def test_should_skip_page_content_normal_text():
    from etl.pdf_pipeline import should_skip_page_content
    text = "本章討論嵌入式系統的設計原則，包含效能、功耗與可靠性三大面向。"
    assert should_skip_page_content(text) is False


def test_should_skip_page_content_garbage():
    from etl.pdf_pipeline import should_skip_page_content
    # Pure garbage characters → check_garbage_text triggers skip
    assert should_skip_page_content("\x01\x02\x03" * 50) is True
