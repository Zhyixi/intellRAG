"""
IAP Platform — 統一 ETL：定期掃描 inbox 雙目錄。

- with_embedding/    建立向量索引（多語言）
- without_embedding/ 僅轉 PDF、擷取文字與截圖，不寫入 ES embedding
"""
from __future__ import annotations

import inspect
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from common.platform import PLATFORM_INDEX_PREFIX, PLATFORM_NAME
from configs import config as app_config
from etl.pdf_pipeline import (
    is_supported_file,
    process_file_to_pages,
    should_skip_page_content,
)
from repositories.repositories import TableManagerRepository
from services.rag_service import RAGService
from services.services import TranslatorService

logger = logging.getLogger(__name__)

TARGET_LANGUAGES = {
    "zh": "繁體中文",
    "en": "英文",
    "vi": "越南文",
    "pt": "葡萄牙文",
    "es": "西班牙文",
}


def _inbox_root() -> Path:
    root = app_config.config.get(
        "etl_inbox", "root", fallback=app_config.rag_input_dir + "/inbox"
    )
    return Path(root)


def inbox_with_embedding() -> Path:
    sub = app_config.config.get("etl_inbox", "with_embedding", fallback="with_embedding")
    return _inbox_root() / sub


def inbox_without_embedding() -> Path:
    sub = app_config.config.get(
        "etl_inbox", "without_embedding", fallback="without_embedding"
    )
    return _inbox_root() / sub


def inbox_processed() -> Path:
    return _inbox_root() / "processed"


def _list_inbox_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        return []
    files: list[Path] = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and is_supported_file(p):
            files.append(p)
    return files


def _move_to_processed(source: Path, bucket: str) -> Path:
    dest_dir = inbox_processed() / bucket / datetime.now().strftime("%Y%m%d")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if dest.exists():
        dest = dest_dir / f"{source.stem}_{datetime.now().strftime('%H%M%S')}{source.suffix}"
    shutil.move(str(source), str(dest))
    return dest


def _cleanup_temp_pdf(temp_pdf: str | None) -> None:
    if not temp_pdf:
        return
    try:
        p = Path(temp_pdf)
        if p.is_file():
            p.unlink()
    except OSError as exc:
        logger.warning("Failed to remove temp pdf %s: %s", temp_pdf, exc)


async def _index_page(
    *,
    rag_service: RAGService,
    translator: TranslatorService,
    lang_code: str,
    text: str,
    metadata: dict[str, Any],
    index_name: str,
) -> None:
    embedding_content = text
    if lang_code != "zh":
        translated = await translator.translate(
            texts=[text],
            source_lang="auto",
            target_lang=lang_code,
        )
        if translated[0].get("success") == "Y":
            embedding_content = translated[0]["translated"]
            metadata = {**metadata, "translated_record": translated[0]}
        else:
            logger.info("Translation failed for %s, using source text", lang_code)

    await rag_service.build_nodes(
        index_name=index_name,
        text_content=embedding_content,
        metadata=metadata,
        node_type="file",
    )


async def _drop_embedding_indices_if_refresh(rag_service: RAGService, refresh: bool) -> None:
    if not refresh:
        return
    for lang_code in TARGET_LANGUAGES:
        await rag_service.drop_index(index_name=f"{PLATFORM_INDEX_PREFIX}_file_{lang_code}")


async def _process_with_embedding(
    file_path: Path,
    *,
    rag_service: RAGService,
    translator: TranslatorService,
    refresh: bool,
) -> dict[str, int]:
    stats = {"pages": 0, "indexed": 0, "skipped": 0}
    try:
        bundle = process_file_to_pages(file_path)
    except Exception as exc:
        logger.exception("Failed to process %s: %s", file_path, exc)
        return stats

    temp_pdf = bundle.get("temp_pdf")
    filename = Path(bundle["file_name"]).name
    snapshot_paths = bundle.get("snapshot_paths") or []

    for pdf_page in bundle["pages"]:
        stats["pages"] += 1
        content = pdf_page["content"]
        if should_skip_page_content(content):
            stats["skipped"] += 1
            continue

        target = filename.replace(".pdf", "") + f"/{pdf_page['page']}.jpg"
        snapshot_path = next((k for k in snapshot_paths if target in k), "")

        base_metadata = {
            "file_path": bundle["pdf_path"],
            "file_name": filename,
            "page_label": pdf_page["page"],
            "page_snapshot": snapshot_path,
            "platform": PLATFORM_NAME,
            "content_type": "inbox_document",
        }

        for lang_code in TARGET_LANGUAGES:
            index_name = f"{PLATFORM_INDEX_PREFIX}_file_{lang_code}"
            await _index_page(
                rag_service=rag_service,
                translator=translator,
                lang_code=lang_code,
                text=content,
                metadata=dict(base_metadata),
                index_name=index_name,
            )
            stats["indexed"] += 1

    _cleanup_temp_pdf(temp_pdf)
    _move_to_processed(file_path, "with_embedding")
    return stats


async def _process_without_embedding(file_path: Path) -> dict[str, int]:
    """僅轉 PDF、擷取文字與截圖，輸出到 processed 旁的文字摘要，不建索引。"""
    stats = {"pages": 0, "exported": 0}
    try:
        bundle = process_file_to_pages(file_path)
    except Exception as exc:
        logger.exception("Failed to process (no embedding) %s: %s", file_path, exc)
        return stats

    out_dir = inbox_processed() / "extracted_text" / file_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    for pdf_page in bundle["pages"]:
        stats["pages"] += 1
        if should_skip_page_content(pdf_page["content"]):
            continue
        out_file = out_dir / f"page_{pdf_page['page']:04d}.txt"
        out_file.write_text(pdf_page["content"], encoding="utf-8")
        stats["exported"] += 1

    _cleanup_temp_pdf(bundle.get("temp_pdf"))
    _move_to_processed(file_path, "without_embedding")
    return stats


async def scan_inbox_task(
    *,
    refresh: bool = False,
    table_manager: TableManagerRepository | None = None,
    rag_service: RAGService | None = None,
    translator: TranslatorService | None = None,
) -> dict[str, Any]:
    """掃描雙 inbox 目錄並處理所有支援檔案。"""
    _ = table_manager  # 保留 DI 簽名，供未來記錄表使用
    if rag_service is None:
        raise ValueError("rag_service is required for with_embedding processing")

    translator = translator or rag_service.translator
    fn = inspect.currentframe().f_code.co_name
    logger.info("[%s] %s ETL scan started (embedding_cloud=%s)", fn, PLATFORM_NAME, app_config.rag_model_cloud)

    summary: dict[str, Any] = {
        "with_embedding": {"files": 0, "pages": 0, "indexed": 0},
        "without_embedding": {"files": 0, "pages": 0, "exported": 0},
    }

    await _drop_embedding_indices_if_refresh(rag_service, refresh)

    for fp in _list_inbox_files(inbox_with_embedding()):
        summary["with_embedding"]["files"] += 1
        st = await _process_with_embedding(
            fp, rag_service=rag_service, translator=translator, refresh=refresh
        )
        summary["with_embedding"]["pages"] += st.get("pages", 0)
        summary["with_embedding"]["indexed"] += st.get("indexed", 0)

    for fp in _list_inbox_files(inbox_without_embedding()):
        summary["without_embedding"]["files"] += 1
        st = await _process_without_embedding(fp)
        summary["without_embedding"]["pages"] += st.get("pages", 0)
        summary["without_embedding"]["exported"] += st.get("exported", 0)

    logger.info("%s ETL scan finished: %s", PLATFORM_NAME, summary)
    return summary
