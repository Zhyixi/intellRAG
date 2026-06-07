"""API 錯誤回應：對外不洩漏 stack trace，完整錯誤僅寫 server log / Slack。"""
from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any, Callable

from fastapi import HTTPException

from fast_api_service.response import setErrorResponse

logger = logging.getLogger(__name__)

# 對外固定訊息，避免 traceback、路徑、SQL 等資訊外洩
PUBLIC_ERROR_MESSAGE = "Internal Server Error"


def new_request_error_id() -> str:
    return uuid.uuid4().hex[:12]


def public_error_detail(
    *,
    error_code: int = 500,
    error_id: str | None = None,
    message: str = PUBLIC_ERROR_MESSAGE,
) -> dict[str, Any]:
    """組裝可安全回傳給前端的 error body（與既有 responseCode 格式相容）。"""
    body = setErrorResponse(error_code, message)
    if error_id:
        body["errorId"] = error_id
    return body


def log_and_build_public_http_exception(
    *,
    endpoint: str,
    exc: Exception,
    input_para: Any = None,
    status_code: int = 500,
    slack_notify: Callable[..., Any] | None = None,
) -> HTTPException:
    """
    記錄完整堆疊到 log（可選 Slack），僅將通用錯誤回傳給前端。
    """
    error_id = new_request_error_id()
    traceback_msg = traceback.format_exc()
    logger.error(
        "[%s] error_id=%s endpoint=%s input=%r\n%s",
        endpoint,
        error_id,
        endpoint,
        input_para,
        traceback_msg,
    )
    if slack_notify is not None:
        try:
            slack_notify(
                py_name=endpoint,
                error_msg=f"error_id={error_id}\n{traceback_msg}\nInput_para={input_para}",
            )
        except Exception as slack_exc:
            logger.warning("Slack notify failed: %s", slack_exc)

    return HTTPException(
        status_code=status_code,
        detail=public_error_detail(error_code=status_code, error_id=error_id),
    )


def sanitize_http_exception_detail(detail: Any) -> Any:
    """
    攔截既有程式仍帶 traceback 的 HTTPException.detail，避免直接回傳給瀏覽器。
    保留 4xx 的原始訊息（多為業務驗證），僅對疑似內部錯誤內容做脫敏。
    """
    if detail is None:
        return detail

    text = detail if isinstance(detail, str) else str(detail)
    leak_markers = ("Traceback (most recent call last)", "File \"", "line ", "Internal Server Error -")
    if not any(marker in text for marker in leak_markers):
        return detail

    if isinstance(detail, dict) and "responseMessage" in detail:
        error_id = new_request_error_id()
        logger.error("Sanitized leaked error detail error_id=%s: %s", error_id, detail)
        return public_error_detail(error_id=error_id)

    error_id = new_request_error_id()
    logger.error("Sanitized leaked error detail error_id=%s", error_id)
    return public_error_detail(error_id=error_id)
