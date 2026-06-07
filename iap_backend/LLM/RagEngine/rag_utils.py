import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# LibreOffice 可轉為 PDF 的副檔名
_CONVERTIBLE_EXT = {
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
}


def file2pdf(source_path: str) -> str:
    """將 Office 等格式轉為 PDF（與來源同目錄、同名 .pdf）。"""
    source = Path(source_path)
    pdf_path = source.with_suffix(".pdf")
    if source.suffix.lower() == ".pdf":
        return str(source.resolve())

    outdir = str(source.parent)
    try:
        subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "pdf",
                str(source),
                "--outdir",
                outdir,
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )
        logger.info("Converted to PDF: %s -> %s", source, pdf_path)
    except subprocess.CalledProcessError as e:
        logger.error("LibreOffice conversion failed for %s: %s", source, e)
    except FileNotFoundError:
        logger.error("libreoffice not installed; cannot convert %s", source)
    return str(pdf_path)


def ensure_pdf_path(source_path: str) -> str:
    """非 PDF 先轉檔；.txt/.md 包成簡易 PDF 或仍走 libreoffice。"""
    source = Path(source_path)
    ext = source.suffix.lower()
    if ext == ".pdf":
        return str(source.resolve())
    if ext in _CONVERTIBLE_EXT:
        return file2pdf(str(source))
    if ext in (".txt", ".md"):
        return _text_to_pdf(source)
    raise ValueError(f"Unsupported file type for PDF pipeline: {ext}")


def _text_to_pdf(text_path: Path) -> str:
    """純文字以 PyMuPDF 產生簡易 PDF。"""
    import fitz

    pdf_path = text_path.with_suffix(".pdf")
    text = text_path.read_text(encoding="utf-8", errors="ignore")
    doc = fitz.open()
    page = doc.new_page()
    rect = page.rect
    margin = 36
    page.insert_textbox(
        fitz.Rect(margin, margin, rect.width - margin, rect.height - margin),
        text[:50000],
        fontsize=10,
    )
    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path.resolve())
