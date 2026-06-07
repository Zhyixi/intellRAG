"""Notebook API client."""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Iterator

import requests

sys.path.extend([".", ".."])
from configs.config import notebook_api_url

DEFAULT_TIMEOUT = int(os.getenv("iap_API_TIMEOUT", "600"))
POLL_TIMEOUT = int(os.getenv("iap_POLL_TIMEOUT", "5"))
LIST_TIMEOUT = int(os.getenv("iap_LIST_TIMEOUT", "15"))
UPLOAD_TIMEOUT = int(os.getenv("iap_UPLOAD_TIMEOUT", "120"))
_BACKEND_PROXIES = {"http": None, "https": None}


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _request_json(method: str, url: str, token: str, **kwargs) -> tuple[dict | list | None, int]:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    kwargs.setdefault("proxies", _BACKEND_PROXIES)
    headers = kwargs.pop("headers", {})
    headers.update(_auth_headers(token))
    kwargs["headers"] = headers
    try:
        response = requests.request(method, url, **kwargs)
        status = response.status_code
        if status == 200:
            try:
                return response.json(), status
            except ValueError:
                return None, status
        logging.warning("Notebook API %s %s -> %s", method, url, status)
        return None, status
    except requests.RequestException as exc:
        logging.error("Notebook API %s %s failed: %s", method, url, exc)
        return None, 0


def chat(
    token: str,
    *,
    session_id: str,
    content: str,
    syslang: str = "zh",
    confirm_web_search: bool = False,
    web_search_query: str | None = None,
) -> dict:
    payload = {
        "session_id": session_id,
        "content": content,
        "syslang": syslang,
        "confirm_web_search": confirm_web_search,
        "web_search_query": web_search_query,
    }
    data, status = _request_json("POST", notebook_api_url.chat, token, json=payload)
    if data is None:
        return {"status_code": status, "chat_response": ""}
    data["status_code"] = status
    return data


def chat_stream(
    token: str,
    *,
    session_id: str,
    content: str,
    syslang: str = "zh",
    confirm_web_search: bool = False,
    web_search_query: str | None = None,
) -> Iterator[dict]:
    payload = {
        "session_id": session_id,
        "content": content,
        "syslang": syslang,
        "confirm_web_search": confirm_web_search,
        "web_search_query": web_search_query,
    }
    try:
        response = requests.post(
            notebook_api_url.chat_stream,
            json=payload,
            headers=_auth_headers(token),
            stream=True,
            timeout=DEFAULT_TIMEOUT,
            proxies=_BACKEND_PROXIES,
        )
        if response.status_code != 200:
            yield {"type": "error", "message": f"HTTP {response.status_code}"}
            return
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            try:
                yield json.loads(raw_line[6:])
            except json.JSONDecodeError:
                continue
    except requests.RequestException as exc:
        logging.error("Notebook stream failed: %s", exc)
        yield {"type": "error", "message": str(exc)}


def get_history_session_ids(token: str) -> dict:
    data, status = _request_json("GET", notebook_api_url.history_session_ids, token)
    return {"res": data or [], "status_code": status}


def get_history_session(token: str, session_id: str) -> dict:
    url = f"{notebook_api_url.history_session}?session_id={requests.utils.quote(session_id)}"
    data, status = _request_json("GET", url, token)
    return {"res": data or {"result": []}, "status_code": status}


def delete_history_session(token: str, session_id: str) -> dict:
    url = f"{notebook_api_url.delete_session}?session_id={requests.utils.quote(session_id)}"
    data, status = _request_json("DELETE", url, token)
    return {"res": data, "status_code": status}


def upload_document(token: str, file_bytes: bytes, filename: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    files = {"file": (filename, file_bytes)}
    kwargs = {
        "timeout": DEFAULT_TIMEOUT,
        "proxies": _BACKEND_PROXIES,
        "headers": headers,
        "files": files,
    }
    try:
        resp = requests.post(
            notebook_api_url.documents_upload,
            **kwargs,
            timeout=UPLOAD_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return {"data": resp.json(), "status_code": resp.status_code}
        return {"data": None, "status_code": resp.status_code}
    except requests.RequestException as exc:
        logging.error("upload failed: %s", exc)
        return {"data": None, "status_code": 0}


def list_documents(token: str) -> dict:
    data, status = _request_json(
        "GET", notebook_api_url.documents_list, token, timeout=LIST_TIMEOUT
    )
    return {"data": data or {"documents": []}, "status_code": status}


def delete_document(token: str, doc_id: str) -> dict:
    url = f"{notebook_api_url.documents_delete}/{doc_id}"
    data, status = _request_json("DELETE", url, token, timeout=LIST_TIMEOUT)
    return {"data": data, "status_code": status}


def get_job(token: str, job_id: str) -> dict:
    url = f"{notebook_api_url.job_status}/{job_id}"
    data, status = _request_json("GET", url, token, timeout=POLL_TIMEOUT)
    return {"data": data, "status_code": status}


def pause_job(token: str, job_id: str) -> dict:
    url = notebook_api_url.job_pause.format(job_id=job_id)
    data, status = _request_json("POST", url, token, timeout=POLL_TIMEOUT)
    return {"data": data, "status_code": status}


def cancel_job(token: str, job_id: str) -> dict:
    url = notebook_api_url.job_cancel.format(job_id=job_id)
    data, status = _request_json("POST", url, token, timeout=POLL_TIMEOUT)
    return {"data": data, "status_code": status}


def resume_job(token: str, job_id: str) -> dict:
    url = notebook_api_url.job_resume.format(job_id=job_id)
    data, status = _request_json("POST", url, token, timeout=LIST_TIMEOUT)
    return {"data": data, "status_code": status}
