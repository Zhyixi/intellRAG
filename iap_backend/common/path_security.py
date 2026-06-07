"""路徑安全：限制檔案讀取只能在白名單目錄內（防止路徑穿越 / 任意檔讀取）。"""
from __future__ import annotations

import os
from typing import Iterable


class UnsafePathError(ValueError):
    """請求的路徑不在允許讀取的目錄內。"""


def _parse_roots(env_value: str | None, defaults: tuple[str, ...]) -> list[str]:
    if env_value and str(env_value).strip():
        parts = [p.strip() for p in str(env_value).split(",") if p.strip()]
    else:
        parts = list(defaults)
    return [os.path.realpath(p) for p in parts]


def resolve_allowed_file_path(
    file_info: str,
    *,
    allowed_roots: Iterable[str],
) -> str:
    """
    將使用者傳入的路徑正規化後，確認落在 allowed_roots 底下且為一般檔案。

    與靜態檔案 / 圖片讀取 API 共用，避免直接讀取系統任意路徑。
    """
    if not file_info or not str(file_info).strip():
        raise UnsafePathError("empty path")

    file_path = os.path.realpath(str(file_info).strip())
    roots = [os.path.realpath(r) for r in allowed_roots]

    if not any(
        file_path == root or file_path.startswith(root + os.sep) for root in roots
    ):
        raise UnsafePathError("path outside allowed roots")

    if not os.path.isfile(file_path):
        raise UnsafePathError("not a file")

    return file_path


def static_file_roots() -> list[str]:
    return _parse_roots(
        os.getenv("STATIC_FILE_ROOTS") or os.getenv("PE_IMAGE_ROOTS"),
        ("/app", "/tmp", "/mnt"),
    )
