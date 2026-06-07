"""Document upload, list, delete, job status (pause / resume)."""
from __future__ import annotations

import datetime
import logging
import uuid
from pathlib import Path

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from langfuse import observe

from common.auth import get_current_user
from containers import Container, get_container
from database.database import Database
from etl.pdf_pipeline import is_supported_file
from models.user_models import User
from services.document_worker import (
    NB_DOCUMENTS,
    RESUMABLE_DOC_STATUSES,
    UPLOAD_ROOT,
    delete_document_from_es,
    delete_upload_files,
    mark_stale_processing_docs,
    process_document_job,
)
from services.job_service import JobService
from services.llm_factory import resolve_openai_api_key
from services.rag_service import RAGService
from services.services import MongoService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notebook", tags=["iap-notebook"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _get_db_session():
    db: Database = get_container().sql_db()
    with db.session() as session:
        yield session


def _doc_file_path(user_id: int, doc_id: str, filename: str) -> Path:
    return UPLOAD_ROOT / str(user_id) / doc_id / filename


async def _run_job(
    job_id: str,
    user_id: int,
    doc_id: str,
    file_path: Path,
    api_key: str,
    *,
    resume: bool = False,
):
    container = get_container()
    rag_service = container.rag_service()
    mongo_service = container.mongo_service()
    redis_client = container.redis_client()
    job_service = JobService(redis_client)
    await process_document_job(
        job_id=job_id,
        user_id=user_id,
        doc_id=doc_id,
        file_path=file_path,
        rag_service=rag_service,
        mongo_service=mongo_service,
        job_service=job_service,
        api_key=api_key,
        resume=resume,
    )


def _get_owned_doc(mongo_service: MongoService, user_id: int, doc_id: str) -> dict:
    doc = mongo_service.find_one(
        query={"doc_id": doc_id, "user_id": user_id},
        collection_name=NB_DOCUMENTS,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


def _get_owned_job(job_service: JobService, job_id: str, user_id: int) -> dict:
    job = job_service.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if int(job.get("user_id", -1)) != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return job


@router.post("/documents/upload")
@observe(name="notebook_embedding")
@inject
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    redis_client=Depends(Provide[Container.redis_client]),
    session=Depends(_get_db_session),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    if not is_supported_file(file.filename):
        raise HTTPException(status_code=400, detail="unsupported file type")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 50MB)")

    doc_id = str(uuid.uuid4())
    job_service = JobService(redis_client)
    job_id = job_service.new_job_id()

    dest_dir = UPLOAD_ROOT / str(current_user.id) / doc_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / Path(file.filename).name
    dest_path.write_bytes(content)

    mongo_service.insert_many(
        insert_data={
            "user_id": current_user.id,
            "doc_id": doc_id,
            "filename": file.filename,
            "status": "processing",
            "page_count": 0,
            "indexed_chunks": 0,
            "es_index": "",
            "job_id": job_id,
            "created_at": datetime.datetime.utcnow(),
        },
        collection_name=NB_DOCUMENTS,
    )
    job_service.create(job_id, user_id=current_user.id, doc_id=doc_id, filename=file.filename)

    api_key = resolve_openai_api_key(session, current_user.id)
    background_tasks.add_task(
        _run_job,
        job_id,
        current_user.id,
        doc_id,
        dest_path,
        api_key,
    )
    return {"job_id": job_id, "doc_id": doc_id, "filename": file.filename}


@router.get("/documents")
@inject
async def list_documents(
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
):
    df = mongo_service.find_df(
        query={"user_id": current_user.id},
        collection_name=NB_DOCUMENTS,
        sort=[("created_at", -1)],
    )
    if df.empty:
        return {"documents": []}
    if "_id" in df.columns:
        df.drop(columns=["_id"], inplace=True)
    return {"documents": df.to_dict(orient="records")}


@router.delete("/documents/{doc_id}")
@inject
async def delete_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    rag_service: RAGService = Depends(Provide[Container.rag_service]),
    redis_client=Depends(Provide[Container.redis_client]),
):
    doc = _get_owned_doc(mongo_service, current_user.id, doc_id)
    job_id = doc.get("job_id")
    if job_id:
        JobService(redis_client).request_cancel(str(job_id))
        JobService(redis_client).release_lock(doc_id, str(job_id))
    await delete_document_from_es(rag_service, current_user.id, doc_id)
    delete_upload_files(current_user.id, doc_id)
    mongo_service.delete_many(query={"doc_id": doc_id, "user_id": current_user.id}, collection_name=NB_DOCUMENTS)
    return {"message": "deleted", "doc_id": doc_id}


@router.get("/jobs/{job_id}")
@inject
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    redis_client=Depends(Provide[Container.redis_client]),
):
    job_service = JobService(redis_client)
    job = job_service.get(job_id)
    if not job:
        doc = mongo_service.find_one(
            query={"job_id": job_id, "user_id": current_user.id},
            collection_name=NB_DOCUMENTS,
        )
        if doc and doc.get("status") in RESUMABLE_DOC_STATUSES:
            cp = doc.get("embedding_checkpoint") or {}
            return job_service.restore_from_doc(
                job_id,
                user_id=current_user.id,
                doc_id=doc["doc_id"],
                filename=doc.get("filename", ""),
                checkpoint=cp,
                status=str(doc.get("status", "interrupted")),
            )
        raise HTTPException(status_code=404, detail="job not found")
    if int(job.get("user_id", -1)) != current_user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    if job_service.is_stale_running(job):
        job_service.mark_interrupted(job_id)
        job["status"] = "interrupted"
        mongo_service.update_one(
            query={"job_id": job_id, "user_id": current_user.id},
            update={"$set": {"status": "interrupted"}},
            collection_name=NB_DOCUMENTS,
        )
    return job


@router.post("/jobs/{job_id}/pause")
@inject
async def pause_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    redis_client=Depends(Provide[Container.redis_client]),
):
    job_service = JobService(redis_client)
    job = _get_owned_job(job_service, job_id, current_user.id)
    if job.get("status") not in ("running",):
        return {"job_id": job_id, "status": job.get("status"), "message": "not running"}
    job_service.request_pause(job_id)
    return {"job_id": job_id, "status": "pausing", "message": "pause requested"}


@router.post("/jobs/{job_id}/cancel")
@inject
async def cancel_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    redis_client=Depends(Provide[Container.redis_client]),
):
    job_service = JobService(redis_client)
    job = _get_owned_job(job_service, job_id, current_user.id)
    if job.get("status") in ("done", "failed"):
        return {"job_id": job_id, "status": job.get("status"), "message": "already finished"}
    job_service.request_cancel(job_id)
    return {"job_id": job_id, "status": "stopping", "message": "stop requested"}


@router.post("/jobs/{job_id}/resume")
@inject
async def resume_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    redis_client=Depends(Provide[Container.redis_client]),
    session=Depends(_get_db_session),
):
    job_service = JobService(redis_client)
    doc = mongo_service.find_one(
        query={"job_id": job_id, "user_id": current_user.id},
        collection_name=NB_DOCUMENTS,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    doc_status = doc.get("status", "")
    if doc_status == "ready":
        raise HTTPException(status_code=400, detail="document already indexed")
    if doc_status == "failed" and not doc.get("embedding_checkpoint"):
        raise HTTPException(status_code=400, detail="cannot resume failed job without checkpoint")

    job = job_service.get(job_id)
    if job and job.get("status") == "running" and not job_service.is_stale_running(job):
        raise HTTPException(status_code=409, detail="job already running")

    file_path = _doc_file_path(current_user.id, doc["doc_id"], doc["filename"])
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="uploaded file missing on disk")

    checkpoint = doc.get("embedding_checkpoint") or {}
    job_service.restore_from_doc(
        job_id,
        user_id=current_user.id,
        doc_id=doc["doc_id"],
        filename=doc.get("filename", ""),
        checkpoint=checkpoint,
        status="running",
    )
    mongo_service.update_one(
        query={"doc_id": doc["doc_id"], "user_id": current_user.id},
        update={"$set": {"status": "processing"}},
        collection_name=NB_DOCUMENTS,
    )

    api_key = resolve_openai_api_key(session, current_user.id)
    background_tasks.add_task(
        _run_job,
        job_id,
        current_user.id,
        doc["doc_id"],
        file_path,
        api_key,
        resume=True,
    )
    return {
        "job_id": job_id,
        "status": "running",
        "message": "resume started",
        "checkpoint": checkpoint,
    }
