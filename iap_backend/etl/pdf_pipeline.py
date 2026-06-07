"""PDF 讀取、非 PDF 轉檔、品質檢查 — 統一 ETL 共用。"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from LLM.RagEngine.build_image import build_pdf_image
from LLM.RagEngine.rag_utils import ensure_pdf_path

logger = logging.getLogger(__name__)

SUPPORTED_SOURCE_EXT = {
    ".pdf",
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
    ".txt",
    ".md",
}


def is_supported_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SOURCE_EXT


def read_pdf_pages(path: str | Path) -> list[dict[str, Any]]:
    doc = fitz.open(str(path))
    texts: list[dict[str, Any]] = []
    try:
        for i, page in enumerate(doc):
            blocks = page.get_text("blocks")
            blocks = sorted(blocks, key=lambda b: (round(b[1]), round(b[0])))
            page_text = "\n".join([b[4].strip() for b in blocks if b[4].strip()])
            if page_text:
                texts.append({"page": i + 1, "content": page_text})
    finally:
        doc.close()
    return texts


def check_garbage_text(text: str, threshold: float = 0.1) -> tuple[bool, float]:
    if not text:
        return True, 1.0
    garbage_pattern = re.compile(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\u07b0-\u0fff\u1700-\u18ff]"
    )
    garbage_chars = garbage_pattern.findall(text)
    garbage_ratio = len(garbage_chars) / len(text)
    return garbage_ratio > threshold, garbage_ratio


def should_skip_page_content(content: str) -> bool:
    garbage, _ = check_garbage_text(content, threshold=0.1)
    if garbage:
        return True
    skip_kw = ("目录", "目錄", "一览表", "一覽表", "table of contents")
    return any(k in content for k in skip_kw)


def prepare_pdf(source_path: str | Path) -> tuple[Path, Path | None]:
    """
    確保得到 PDF 路徑。若為臨時轉檔則回傳 (pdf_path, temp_pdf_to_delete)。
    """
    source = Path(source_path)
    if source.suffix.lower() == ".pdf":
        return source.resolve(), None
    pdf_path = ensure_pdf_path(str(source))
    return Path(pdf_path).resolve(), Path(pdf_path).resolve()


def process_file_to_pages(source_path: str | Path) -> dict[str, Any]:
    """非 PDF 先轉 PDF，再逐頁擷取文字與截圖路徑。"""
    pdf_path, temp_pdf = prepare_pdf(source_path)
    pages = read_pdf_pages(pdf_path)
    snapshot_paths: list[str] = []
    try:
        snapshot_paths = build_pdf_image(str(pdf_path)) or []
    except Exception as exc:
        logger.warning("build_pdf_image failed for %s: %s", pdf_path, exc)

    return {
        "source_path": str(source_path),
        "pdf_path": str(pdf_path),
        "temp_pdf": str(temp_pdf) if temp_pdf else None,
        "pages": pages,
        "snapshot_paths": snapshot_paths,
        "file_name": Path(source_path).name,
    }
