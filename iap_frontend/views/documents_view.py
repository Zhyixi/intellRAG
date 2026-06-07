"""文件上傳、列表與 Embedding 進度（非阻塞輪詢 + 列表快取）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from HandleRequest.notebook import (
    cancel_job,
    delete_document,
    get_job,
    list_documents,
    pause_job,
    resume_job,
    upload_document,
)
from views.strings import (
    BTN_DELETE_DOC,
    BTN_PAUSE_INDEX,
    BTN_RESUME_INDEX,
    BTN_STOP_INDEX,
    BTN_UPLOAD_INDEX,
    DOCS_CAPTION,
    DOCS_COL_LABELS,
    DOCS_EMPTY,
    DOCS_INDEX_DONE,
    DOCS_INDEX_FAILED,
    DOCS_INDEX_INTERRUPTED,
    DOCS_INDEX_PAUSED,
    DOCS_INDEX_STOPPED,
    DOCS_LIST_TITLE,
    DOCS_PROGRESS_PAGES,
    DOCS_PROGRESS_PERCENT,
    DOCS_PROGRESS_TITLE,
    DOCS_TITLE,
    DOCS_UPLOAD_FAIL,
    DOCS_UPLOAD_OK,
    DOCS_UPLOADER,
    PLEASE_LOGIN,
)

_RESUMABLE = frozenset({"processing", "paused", "interrupted", "cancelled"})
_ACTIVE_JOB = frozenset({"running", "pausing", "stopping"})


def _sync_active_job_from_docs(docs: list) -> None:
    for doc in docs:
        if doc.get("status") in _RESUMABLE and doc.get("job_id"):
            st.session_state["active_job_id"] = doc["job_id"]
            return


def _load_docs(token: str, *, force: bool = False) -> list:
    if force or "docs_cache" not in st.session_state:
        resp = list_documents(token)
        docs = (resp.get("data") or {}).get("documents") or []
        st.session_state["docs_cache"] = docs
        st.session_state["docs_cache_error"] = resp.get("status_code") not in (200, None)
        _sync_active_job_from_docs(docs)
    return st.session_state.get("docs_cache") or []


@st.fragment(run_every=2)
def _job_progress_panel(token: str):
    job_id = st.session_state.get("active_job_id")
    if not job_id:
        return

    job_resp = get_job(token, job_id)
    job = job_resp.get("data") or {}
    if not job:
        if job_resp.get("status_code") == 0:
            st.caption("後端忙碌，稍後自動重試… / Backend busy, retrying…")
        elif job_resp.get("status_code") not in (200, None):
            st.warning(
                f"無法取得任務狀態 / Could not fetch job status (HTTP {job_resp.get('status_code')})"
            )
        return

    st.subheader(DOCS_PROGRESS_TITLE)
    pct = int(job.get("percent", 0))
    total_pages = int(job.get("total_pages") or 0)
    processed = int(job.get("processed_pages") or 0)
    indexed = int(job.get("indexed_chunks") or 0)
    status = job.get("status", "")
    stage = job.get("stage", "")

    col_pct, col_pages = st.columns([1, 2])
    with col_pct:
        st.metric("進度 / Progress", DOCS_PROGRESS_PERCENT.format(pct=pct))
    with col_pages:
        if total_pages > 0:
            st.caption(
                DOCS_PROGRESS_PAGES.format(done=processed, total=total_pages)
                + f" · {indexed} chunks"
            )

    st.progress(pct / 100.0)
    st.caption(f"{stage} — {job.get('message', '')}")

    btn_cols = st.columns(3)
    with btn_cols[0]:
        if status in _ACTIVE_JOB and st.button(BTN_PAUSE_INDEX, key=f"pause_{job_id}"):
            pause_job(token, job_id)
    with btn_cols[1]:
        if status in ("paused", "interrupted", "cancelled", "failed") and st.button(
            BTN_RESUME_INDEX, key=f"resume_{job_id}"
        ):
            resume_job(token, job_id)
            st.session_state["active_job_id"] = job_id
    with btn_cols[2]:
        if status in _ACTIVE_JOB | {"paused", "interrupted"} and st.button(
            BTN_STOP_INDEX, key=f"stop_{job_id}"
        ):
            cancel_job(token, job_id)

    if status == "done":
        st.success(DOCS_INDEX_DONE)
        st.session_state.pop("active_job_id", None)
        _load_docs(token, force=True)
    elif status == "failed":
        st.error(job.get("message", DOCS_INDEX_FAILED))
        if st.button(BTN_RESUME_INDEX, key=f"resume_failed_{job_id}"):
            resume_job(token, job_id)
    elif status == "paused":
        st.info(DOCS_INDEX_PAUSED)
    elif status == "interrupted":
        st.warning(DOCS_INDEX_INTERRUPTED)
    elif status == "cancelled":
        st.info(DOCS_INDEX_STOPPED)


@st.fragment
def _docs_list_panel(token: str):
    docs = _load_docs(token)
    if st.session_state.get("docs_cache_error"):
        st.caption("文件列表載入逾時，可稍後重新整理頁面 / Document list timed out")

    resumable = [d for d in docs if d.get("status") in _RESUMABLE and d.get("job_id")]
    for doc in resumable:
        if doc.get("status") in ("interrupted", "paused", "cancelled"):
            st.warning(
                f"「{doc.get('filename', '')}」"
                f"{DOCS_INDEX_INTERRUPTED if doc.get('status') == 'interrupted' else DOCS_INDEX_PAUSED if doc.get('status') == 'paused' else DOCS_INDEX_STOPPED}"
            )
            if st.button(
                BTN_RESUME_INDEX + f" — {doc.get('filename', '')[:24]}",
                key=f"banner_resume_{doc.get('doc_id')}",
            ):
                resume_job(token, doc["job_id"])
                st.session_state["active_job_id"] = doc["job_id"]
                st.rerun(scope="app")

    st.divider()
    st.subheader(DOCS_LIST_TITLE)
    if not docs:
        st.info(DOCS_EMPTY)
        return

    df = pd.DataFrame(docs)
    show_cols = [c for c in DOCS_COL_LABELS if c in df.columns]
    display_df = df[show_cols].rename(columns=DOCS_COL_LABELS)
    st.dataframe(display_df, width="stretch")

    for doc in docs:
        doc_id = doc.get("doc_id")
        fname = doc.get("filename", doc_id)
        if st.button(BTN_DELETE_DOC.format(name=fname), key=f"del_{doc_id}"):
            delete_document(token, doc_id)
            if st.session_state.get("active_job_id") == doc.get("job_id"):
                st.session_state.pop("active_job_id", None)
            _load_docs(token, force=True)
            st.rerun(scope="app")


def render_documents_page(cookies):
    token = st.session_state.get("auth_token") or cookies.get("auth_token")
    if not token:
        st.warning(PLEASE_LOGIN)
        return

    st.title(DOCS_TITLE)
    st.caption(DOCS_CAPTION)

    uploaded = st.file_uploader(
        DOCS_UPLOADER,
        type=["pdf", "doc", "docx", "ppt", "pptx", "txt", "md", "odt", "rtf"],
        accept_multiple_files=False,
        key="docs_file_uploader",
    )
    if uploaded and st.button(BTN_UPLOAD_INDEX):
        data = uploaded.getvalue()
        with st.status("上傳中… / Uploading…", expanded=True) as upload_status:
            resp = upload_document(token, data, uploaded.name)
            if resp["status_code"] in (200, 201) and resp.get("data"):
                job_id = resp["data"]["job_id"]
                st.session_state["active_job_id"] = job_id
                st.session_state.pop("docs_cache", None)
                upload_status.update(label="上傳完成 / Upload complete", state="complete")
                st.success(DOCS_UPLOAD_OK.format(job_id=job_id[:8]))
            else:
                upload_status.update(label="上傳失敗 / Upload failed", state="error")
                st.error(DOCS_UPLOAD_FAIL.format(code=resp.get("status_code")))

    _job_progress_panel(token)
    _docs_list_panel(token)
