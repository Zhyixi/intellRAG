"""Background document processing: PDF extract → embed → ES index (resumable)."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from etl.pdf_pipeline import is_supported_file, prepare_pdf, read_pdf_pages, should_skip_page_content
from LLM.RagEngine.build_image import build_pdf_image
from langfuse import observe

from common.langfuse_tracing import (
    embedding_page_observation,
    flush_langfuse,
    wrap_embedding_model_for_tracing,
)
from services.job_service import JobService
from services.llm_factory import create_embedding_model, user_index_name
from services.services import MongoService

logger = logging.getLogger(__name__)

NB_DOCUMENTS = "nb_documents"
UPLOAD_ROOT = Path(os.getenv("USER_UPLOAD_ROOT", "/app/user_uploads"))

RESUMABLE_DOC_STATUSES = frozenset({"processing", "paused", "interrupted", "cancelled"})


def _save_checkpoint(
    mongo_service: MongoService,
    *,
    user_id: int,
    doc_id: str,
    checkpoint: dict[str, Any],
    status: str = "processing",
    indexed_chunks: int | None = None,
) -> None:
    update: dict[str, Any] = {
        "embedding_checkpoint": checkpoint,
        "status": status,
    }
    if indexed_chunks is not None:
        update["indexed_chunks"] = indexed_chunks
    mongo_service.update_one(
        query={"doc_id": doc_id, "user_id": user_id},
        update={"$set": update},
        collection_name=NB_DOCUMENTS,
    )


async def _check_job_control(
    job_service: JobService,
    mongo_service: MongoService,
    *,
    job_id: str,
    user_id: int,
    doc_id: str,
    checkpoint: dict[str, Any],
    indexed: int,
) -> str | None:
    """Return 'pause', 'cancel', or None to continue."""
    if job_service.is_cancel_requested(job_id):
        job_service.update(
            job_id,
            stage="cancelled",
            percent=JobService._page_percent(
                int(checkpoint.get("next_page_index") or 0),
                int(checkpoint.get("total_pages") or 0),
            ),
            message="已停止，可從斷點繼續 / Stopped, resumable",
            status="cancelled",
            indexed_chunks=indexed,
        )
        _save_checkpoint(
            mongo_service,
            user_id=user_id,
            doc_id=doc_id,
            checkpoint=checkpoint,
            status="cancelled",
            indexed_chunks=indexed,
        )
        return "cancel"
    if job_service.is_pause_requested(job_id):
        job_service.clear_control_flags(job_id)
        job_service.pause(job_id, message="已暫停，可繼續 / Paused, click resume")
        _save_checkpoint(
            mongo_service,
            user_id=user_id,
            doc_id=doc_id,
            checkpoint=checkpoint,
            status="paused",
            indexed_chunks=indexed,
        )
        return "pause"
    return None


@observe(name="notebook_embedding_job")
async def process_document_job(
    *,
    job_id: str,
    user_id: int,
    doc_id: str,
    file_path: Path,
    rag_service,
    mongo_service: MongoService,
    job_service: JobService,
    api_key: str,
    resume: bool = False,
) -> None:
    index_name = user_index_name(user_id)
    filename = file_path.name
    temp_pdf: Path | None = None
    lock_acquired = False

    try:
        from langfuse import get_client

        lf = get_client()
        lf.update_current_trace(
            user_id=str(user_id),
            metadata={"job_id": job_id, "doc_id": doc_id, "filename": filename, "resume": resume},
        )
    except Exception:
        pass

    if not job_service.try_acquire_lock(doc_id, job_id):
        logger.warning("Job %s skipped: doc %s already locked", job_id, doc_id)
        return

    lock_acquired = True
    job_service.clear_control_flags(job_id)

    doc = mongo_service.find_one(
        query={"doc_id": doc_id, "user_id": user_id},
        collection_name=NB_DOCUMENTS,
    ) or {}
    checkpoint: dict[str, Any] = dict(doc.get("embedding_checkpoint") or {})

    try:
        start_page_index = 0
        snapshot_paths: list[str] = list(checkpoint.get("snapshot_paths") or [])
        pages: list[dict] = []
        total = int(checkpoint.get("total_pages") or 0)

        if resume and checkpoint.get("next_page_index") is not None:
            start_page_index = int(checkpoint["next_page_index"])
            job_service.update(
                job_id,
                stage="resuming",
                percent=JobService._page_percent(start_page_index, total) if total else 5,
                message=f"從第 {start_page_index + 1}/{total or '?'} 頁繼續… / Resuming…",
                status="running",
                total_pages=total,
                processed_pages=start_page_index,
                indexed_chunks=int(checkpoint.get("indexed_chunks") or 0),
            )
            if not is_supported_file(file_path):
                raise ValueError(f"不支援的檔案類型 / Unsupported file type: {file_path.suffix}")
            pdf_path, temp_pdf = await asyncio.to_thread(prepare_pdf, file_path)
            if not total:
                pages = await asyncio.to_thread(read_pdf_pages, pdf_path)
                total = max(len(pages), 1)
            else:
                pages = await asyncio.to_thread(read_pdf_pages, pdf_path)
        else:
            job_service.update(
                job_id,
                stage="converting_pdf",
                percent=3,
                message="正在準備文件… / Preparing file…",
            )
            if not is_supported_file(file_path):
                raise ValueError(f"不支援的檔案類型 / Unsupported file type: {file_path.suffix}")

            pdf_path, temp_pdf = await asyncio.to_thread(prepare_pdf, file_path)
            job_service.update(
                job_id,
                stage="extracting_text",
                percent=6,
                message="正在擷取文字… / Extracting text…",
            )
            pages = await asyncio.to_thread(read_pdf_pages, pdf_path)
            total = max(len(pages), 1)
            start_page_index = 0
            snapshot_paths = []

            job_service.update(
                job_id,
                stage="rendering_pages",
                percent=10,
                message=f"共 {len(pages)} 頁，產生預覽… / {len(pages)} pages, rendering…",
                total_pages=total,
                processed_pages=0,
            )
            try:
                snapshot_paths = await asyncio.to_thread(build_pdf_image, str(pdf_path)) or []
            except Exception as exc:
                logger.warning("build_pdf_image failed for %s: %s", pdf_path, exc)

            checkpoint = {
                "next_page_index": 0,
                "total_pages": total,
                "indexed_chunks": 0,
                "snapshot_paths": snapshot_paths,
            }
            _save_checkpoint(
                mongo_service,
                user_id=user_id,
                doc_id=doc_id,
                checkpoint=checkpoint,
                status="processing",
                indexed_chunks=0,
            )

            job_service.update(
                job_id,
                stage="extracting_pages",
                percent=12,
                message=f"共 {len(pages)} 頁，開始索引… / {len(pages)} pages, indexing…",
                total_pages=total,
            )

        indexed = int(checkpoint.get("indexed_chunks") or doc.get("indexed_chunks") or 0)
        embedding = wrap_embedding_model_for_tracing(create_embedding_model(api_key))
        old_embedding = rag_service.embedding_model
        rag_service.embedding_model = embedding
        rag_service._vector_stores.pop(index_name, None)

        try:
            for idx, pdf_page in enumerate(pages):
                if idx < start_page_index:
                    continue

                control = await _check_job_control(
                    job_service,
                    mongo_service,
                    job_id=job_id,
                    user_id=user_id,
                    doc_id=doc_id,
                    checkpoint={
                        "next_page_index": idx,
                        "total_pages": total,
                        "indexed_chunks": indexed,
                        "snapshot_paths": snapshot_paths,
                    },
                    indexed=indexed,
                )
                if control:
                    return

                content = pdf_page.get("content", "")
                page_num = pdf_page.get("page", idx + 1)
                processed = idx + 1

                if should_skip_page_content(content):
                    checkpoint = {
                        "next_page_index": idx + 1,
                        "total_pages": total,
                        "indexed_chunks": indexed,
                        "snapshot_paths": snapshot_paths,
                    }
                    pct = JobService._page_percent(processed, total)
                    job_service.update(
                        job_id,
                        stage="embedding",
                        percent=pct,
                        message=f"跳過空白頁 {processed}/{total} / Skipping blank page {processed}/{total}",
                        total_pages=total,
                        processed_pages=processed,
                        indexed_chunks=indexed,
                    )
                    _save_checkpoint(
                        mongo_service,
                        user_id=user_id,
                        doc_id=doc_id,
                        checkpoint=checkpoint,
                        indexed_chunks=indexed,
                    )
                    continue

                snapshot = snapshot_paths[idx] if idx < len(snapshot_paths) else ""
                metadata = {
                    "user_id": str(user_id),
                    "doc_id": doc_id,
                    "file_name": filename,
                    "file_path": str(file_path),
                    "page_label": str(page_num),
                    "page_snapshot": snapshot,
                    "content_type": "user_document",
                    "node_type": "file",
                }
                pct = JobService._page_percent(processed, total)
                job_service.update(
                    job_id,
                    stage="embedding",
                    percent=pct,
                    message=(
                        f"Embedding 第 {processed}/{total} 頁 ({pct}%)… / "
                        f"Page {processed}/{total} ({pct}%)…"
                    ),
                    total_pages=total,
                    processed_pages=processed,
                    indexed_chunks=indexed,
                )

                with embedding_page_observation(
                    page_num=int(page_num),
                    page_index=idx,
                    total_pages=total,
                    doc_id=doc_id,
                    filename=filename,
                    char_count=len(content),
                ):
                    await rag_service.build_nodes(
                        index_name=index_name,
                        text_content=content,
                        metadata=metadata,
                        node_type="file",
                    )
                indexed += 1
                checkpoint = {
                    "next_page_index": idx + 1,
                    "total_pages": total,
                    "indexed_chunks": indexed,
                    "snapshot_paths": snapshot_paths,
                }
                _save_checkpoint(
                    mongo_service,
                    user_id=user_id,
                    doc_id=doc_id,
                    checkpoint=checkpoint,
                    indexed_chunks=indexed,
                )
        finally:
            rag_service.embedding_model = old_embedding

        if temp_pdf and os.path.isfile(str(temp_pdf)):
            try:
                os.remove(str(temp_pdf))
            except OSError:
                pass

        mongo_service.update_one(
            query={"doc_id": doc_id, "user_id": user_id},
            update={
                "$set": {
                    "status": "ready",
                    "page_count": total,
                    "indexed_chunks": indexed,
                    "es_index": index_name,
                    "job_id": job_id,
                },
                "$unset": {"embedding_checkpoint": "", "error": ""},
            },
            collection_name=NB_DOCUMENTS,
        )
        job_service.complete(
            job_id,
            message=f"完成，已索引 {indexed} 個片段 / Done, {indexed} chunks indexed",
        )
        logger.info("Document job %s done: user=%s doc=%s chunks=%s", job_id, user_id, doc_id, indexed)
    except Exception as exc:
        logger.exception("Document job %s failed: %s", job_id, exc)
        cp = checkpoint if checkpoint else {}
        if cp:
            _save_checkpoint(
                mongo_service,
                user_id=user_id,
                doc_id=doc_id,
                checkpoint=cp,
                status="interrupted",
                indexed_chunks=int(cp.get("indexed_chunks") or 0),
            )
            job_service.mark_interrupted(
                job_id,
                message=f"工作中斷：{exc} / Interrupted: {exc}",
            )
        else:
            mongo_service.update_one(
                query={"doc_id": doc_id, "user_id": user_id},
                update={"$set": {"status": "failed", "error": str(exc)}},
                collection_name=NB_DOCUMENTS,
            )
            job_service.fail(job_id, str(exc))
    finally:
        flush_langfuse()
        if lock_acquired:
            job_service.release_lock(doc_id, job_id)


async def delete_document_from_es(rag_service, user_id: int, doc_id: str) -> None:
    index_name = user_index_name(user_id)
    es = rag_service._es_repo.es
    if not es.indices.exists(index=index_name):
        return
    try:
        es.delete_by_query(
            index=index_name,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"metadata.user_id.keyword": str(user_id)}},
                            {"term": {"metadata.doc_id.keyword": doc_id}},
                        ]
                    }
                }
            },
            conflicts="proceed",
        )
    except Exception as exc:
        logger.warning("delete_by_query failed for doc %s: %s", doc_id, exc)


def delete_upload_files(user_id: int, doc_id: str) -> None:
    folder = UPLOAD_ROOT / str(user_id) / doc_id
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def mark_stale_processing_docs(mongo_service: MongoService, job_service: JobService) -> int:
    """Mark documents whose Redis job is missing or stale as interrupted."""
    df = mongo_service.find_df(
        query={"status": {"$in": ["processing", "paused"]}},
        collection_name=NB_DOCUMENTS,
    )
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        job_id = row.get("job_id")
        if not job_id:
            continue
        job = job_service.get(str(job_id))
        if job is None or job_service.is_stale_running(job):
            mongo_service.update_one(
                query={"doc_id": row["doc_id"], "user_id": row["user_id"]},
                update={"$set": {"status": "interrupted"}},
                collection_name=NB_DOCUMENTS,
            )
            if job:
                job_service.mark_interrupted(str(job_id))
            count += 1
    return count
