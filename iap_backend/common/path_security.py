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

    與 iap_ae / iap 的 get_image 共用，避免直接讀取系統任意路徑。
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


def iap_ae_image_roots() -> list[str]:
    return _parse_roots(
        os.getenv("iap_ae_IMAGE_ROOTS"),
        ("/app", "/tmp", "/mnt"),
    )


def pe_image_roots() -> list[str]:
    # 預設涵蓋圖片庫與常見暫存目錄；單元測試常用 /tmp
    return _parse_roots(
        os.getenv("PE_IMAGE_ROOTS") or os.getenv("iap_IMAGE_ROOTS"),
        ("/app/db/images", "/app", "/tmp", "/mnt"),
    )


iap_image_roots = pe_image_roots  # 向後相容
