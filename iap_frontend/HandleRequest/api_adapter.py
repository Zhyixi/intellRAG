"""將後端 retrieve API 回應轉成 Streamlit UI 預期的表格結構。"""
from __future__ import annotations

from typing import Any


def _flatten_related_file(related: Any, default_desc: str = "") -> list[dict]:
    """把 nested relatedFile (rootCause/actionCause/...) 展平成列。"""
    rows: list[dict] = []

    if isinstance(related, list):
        return [r for r in related if isinstance(r, dict)]

    if not isinstance(related, dict):
        if default_desc:
            return [{"file_path": "", "PageContent": default_desc, "Page": "", "ImagePath": ""}]
        return []

    for _cause_name, cause in related.items():
        if not isinstance(cause, dict):
            continue
        desc = (cause.get("DESCRIPTION") or "").strip()

        for img_key in ("ImagePath_0", "ImagePath_1", "ImagePath_2"):
            img_path = (cause.get(img_key) or "").strip()
            if img_path:
                rows.append(
                    {
                        "file_path": img_path,
                        "PageContent": desc,
                        "Page": "",
                        "ImagePath": img_path,
                    }
                )

        for file_key in ("file_0", "file_1", "file_2"):
            file_data = cause.get(file_key) or {}
            if not isinstance(file_data, dict):
                continue
            path = (file_data.get("path") or "").strip()
            hits = file_data.get("hits") or []
            if not hits and path:
                rows.append(
                    {
                        "file_path": path,
                        "PageContent": desc,
                        "Page": "",
                        "ImagePath": path,
                    }
                )
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                snapshot = (hit.get("snapshot") or path or "").strip()
                page = hit.get("page", "")
                content = (hit.get("content") or desc or "").strip()
                rows.append(
                    {
                        "file_path": path or snapshot,
                        "PageContent": content,
                        "Page": str(page) if page is not None else "",
                        "ImagePath": snapshot,
                    }
                )

        if desc and not any(r.get("PageContent") == desc for r in rows):
            rows.append(
                {"file_path": "", "PageContent": desc, "Page": "", "ImagePath": ""}
            )

    if not rows and default_desc:
        rows.append(
            {"file_path": "", "PageContent": default_desc, "Page": "", "ImagePath": ""}
        )
    return rows


def normalize_retrieve_response(api_response: dict | None) -> dict:
    """
    後端 ResponseModel -> UI 使用的 results 結構。
    保留 role / chat_response / source / suggested_questions 等欄位不變。
    """
    if not api_response:
        return {"results": [], "role": "assistant", "chat_response": "", "ret_type": "pe_faca"}

    out = dict(api_response)
    raw_results = api_response.get("results") or []
    normalized: list[dict] = []

    for chunk in raw_results:
        if not isinstance(chunk, dict):
            continue
        texts = chunk.get("Text") or []
        if isinstance(texts, str):
            texts = [texts]
        issue_desc = " ".join(str(t) for t in texts if t).strip()
        issue_type = str(chunk.get("ISSUE_TYPE") or chunk.get("ISSUE_ID") or "").strip()
        flat_related = _flatten_related_file(chunk.get("relatedFile"), default_desc=issue_desc)

        normalized.append(
            {
                "ISSUE_ID": chunk.get("ISSUE_ID", ""),
                "ISSUE_DESC": issue_desc,
                "ISSUE_TYPE": issue_type,
                "Text": texts,
                "Score": chunk.get("Score"),
                "relatedFile": flat_related,
            }
        )

    out["results"] = normalized
    return out
