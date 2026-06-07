"""
依環境資源自動選擇本地 BGE-M3 或雲端 Embedding。
模式：config [embedding] mode = auto | local | cloud
"""
from __future__ import annotations

import logging
import os
from typing import Any, Literal

from configs import config as app_config

EmbeddingMode = Literal["local", "cloud"]

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL = (
    "/app/models/hub/models--BAAI--bge-m3/snapshots/"
    "5617a9f61b028005a4858fdac845db406aefb181"
)


def _config_get(section: str, key: str, fallback: str = "") -> str:
    cfg = app_config.config
    if cfg.has_option(section, key):
        return cfg.get(section, key).strip()
    return fallback


def _config_get_int(section: str, key: str, fallback: int) -> int:
    raw = _config_get(section, key, str(fallback))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def local_model_path() -> str:
    return _config_get("embedding", "local_model_path", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL


def _local_model_usable(path: str) -> bool:
    return os.path.isdir(path) and any(
        os.path.isfile(os.path.join(path, name))
        for name in ("config.json", "pytorch_model.bin", "model.safetensors")
    )


def _gpu_free_bytes() -> int | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return int(free)
    except Exception:
        return None


def _ram_available_bytes() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:
        try:
            import shutil

            return int(shutil.disk_usage("/").free)
        except Exception:
            return 0


def resolve_embedding_mode() -> EmbeddingMode:
    """決定整個環境使用 local 或 cloud embedding。"""
    forced = (
        os.getenv("EMBEDDING_MODE", "").strip().lower()
        or _config_get("embedding", "mode", "auto").lower()
    )
    if forced in ("local", "cloud"):
        logger.info("Embedding mode forced by config/env: %s", forced)
        return forced  # type: ignore[return-value]

    path = local_model_path()
    if not _local_model_usable(path):
        logger.warning("Local embedding model not found at %s → using cloud", path)
        return "cloud"

    min_vram = _config_get_int("embedding", "min_vram_bytes", 4 * 1024**3)
    min_ram = _config_get_int("embedding", "min_ram_bytes", 6 * 1024**3)

    gpu_free = _gpu_free_bytes()
    if gpu_free is not None and gpu_free < min_vram:
        logger.warning(
            "GPU free memory %s < %s → using cloud embedding for all services",
            gpu_free,
            min_vram,
        )
        return "cloud"

    ram_free = _ram_available_bytes()
    if gpu_free is None and ram_free < min_ram:
        logger.warning(
            "RAM available %s < %s → using cloud embedding",
            ram_free,
            min_ram,
        )
        return "cloud"

    logger.info("Using local embedding model at %s", path)
    return "local"


def apply_embedding_mode_to_config() -> EmbeddingMode:
    """同步 rag_setting.cloud，供既有程式讀取。"""
    mode = resolve_embedding_mode()
    app_config.rag_model_cloud = mode == "cloud"
    return mode


def create_embedding_model() -> Any:
    """建立 LangChain Embeddings 實例（local HuggingFace 或 cloud OpenAI）。"""
    mode = apply_embedding_mode_to_config()
    if mode == "cloud":
        from langchain_openai import OpenAIEmbeddings

        cloud_model = _config_get(
            "embedding",
            "cloud_model",
            os.getenv("CLOUD_EMBEDDING_MODEL", "text-embedding-3-small"),
        )
        api_key = app_config.api_key or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "Cloud embedding selected but API_KEY / OPENAI_API_KEY is not set"
            )
        logger.info("Initializing cloud embeddings: %s", cloud_model)
        kwargs = dict(model=cloud_model, openai_api_key=api_key)
        if app_config.rag_model_cloud:
            kwargs["openai_api_base"] = "https://api.openai.com/v1"
        else:
            base = os.getenv("OPENAI_BASE_URL")
            if base:
                kwargs["openai_api_base"] = base
        return OpenAIEmbeddings(**kwargs)

    from langchain_huggingface import HuggingFaceEmbeddings

    path = local_model_path()
    device = "cuda" if _gpu_free_bytes() and _gpu_free_bytes() >= _config_get_int(
        "embedding", "min_vram_bytes", 4 * 1024**3
    ) else "cpu"
    logger.info("Initializing local HuggingFace embeddings on %s", device)
    return HuggingFaceEmbeddings(model_name=path, model_kwargs={"device": device})
