"""Per-user OpenAI LLM / Embedding (BYOK with platform fallback)."""
from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from sqlalchemy.orm import Session

from configs import config as app_config
from models.user_models import UserApiKey

logger = logging.getLogger(__name__)


def _clean_api_key(value: str | None) -> str:
    return (value or "").strip().strip('"')


def resolve_openai_api_key(session: Session, user_id: int) -> str:
    row = (
        session.query(UserApiKey)
        .filter(
            UserApiKey.user_id == user_id,
            UserApiKey.provider == "openai",
            UserApiKey.is_active.is_(True),
        )
        .first()
    )
    if row:
        from common.crypto import decrypt_secret

        try:
            return decrypt_secret(row.encrypted_key)
        except Exception as exc:
            logger.warning("Failed to decrypt API key for user %s: %s", user_id, exc)
    return _clean_api_key(app_config.api_key) or _clean_api_key(os.getenv("OPENAI_API_KEY"))


def create_chat_llm(api_key: str, *, streaming: bool = False) -> ChatOpenAI:
    kwargs = dict(
        model=app_config.chat_model_name,
        openai_api_key=api_key,
        temperature=0,
        max_tokens=8192,
        timeout=300,
        streaming=streaming,
        top_p=1.0,
    )
    if app_config.chat_model_cloud:
        kwargs["openai_api_base"] = "https://api.openai.com/v1"
        return ChatOpenAI(**kwargs)
    base = os.getenv("OPENAI_BASE_URL")
    if base:
        kwargs["openai_api_base"] = base
    return ChatOpenAI(**kwargs)


def _openai_embedding_kwargs(api_key: str, *, cloud_model: str) -> dict:
    kwargs: dict = {"model": cloud_model, "openai_api_key": api_key}
    # Compose 常注入內網 OPENAI_BASE_URL；cloud 模式應直連 api.openai.com
    if app_config.rag_model_cloud:
        kwargs["openai_api_base"] = "https://api.openai.com/v1"
    else:
        base = os.getenv("OPENAI_BASE_URL")
        if base:
            kwargs["openai_api_base"] = base
    return kwargs


def create_embedding_model(api_key: str):
    from common.embedding_resolver import _config_get, resolve_embedding_mode

    if resolve_embedding_mode() == "local":
        from common.embedding_resolver import create_embedding_model as create_platform_embedding

        return create_platform_embedding()

    cloud_model = _config_get(
        "embedding",
        "cloud_model",
        os.getenv("CLOUD_EMBEDDING_MODEL", "text-embedding-3-small"),
    )
    return OpenAIEmbeddings(**_openai_embedding_kwargs(api_key, cloud_model=cloud_model))


def user_index_name(user_id: int) -> str:
    return f"iap_nb_{user_id}_file"
