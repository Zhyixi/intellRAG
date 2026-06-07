"""Account: profile, API key, usage (Langfuse proxy)."""
from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from common.auth import get_current_user, hash_password, verify_password
from common.crypto import encrypt_secret
from containers import get_container
from database.database import Database
from models.user_models import User, UserApiKey
from services.langfuse_usage_service import LangfuseUsageService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/account", tags=["iap-account"])

_usage = LangfuseUsageService()


def _get_db_session():
    db: Database = get_container().sql_db()
    with db.session() as session:
        yield session


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    current_password: str | None = None


class ApiKeyUpdate(BaseModel):
    api_key: str = Field(..., min_length=10)


class ApiKeyStatus(BaseModel):
    configured: bool
    updated_at: str | None = None


@router.patch("/profile")
def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(_get_db_session),
):
    user = session.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.password:
        if not body.current_password or not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="current password incorrect")
        user.password_hash = hash_password(body.password)
    user.updated_at = datetime.datetime.utcnow()
    session.commit()
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@router.put("/api-key", response_model=ApiKeyStatus)
def set_api_key(
    body: ApiKeyUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(_get_db_session),
):
    row = (
        session.query(UserApiKey)
        .filter(UserApiKey.user_id == current_user.id, UserApiKey.provider == "openai")
        .first()
    )
    now = datetime.datetime.utcnow()
    enc = encrypt_secret(body.api_key.strip())
    if row:
        row.encrypted_key = enc
        row.is_active = True
        row.updated_at = now
    else:
        session.add(
            UserApiKey(
                user_id=current_user.id,
                provider="openai",
                encrypted_key=enc,
                is_active=True,
            )
        )
    session.commit()
    return ApiKeyStatus(configured=True, updated_at=now.isoformat())


@router.delete("/api-key")
def delete_api_key(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(_get_db_session),
):
    session.query(UserApiKey).filter(
        UserApiKey.user_id == current_user.id,
        UserApiKey.provider == "openai",
    ).delete()
    session.commit()
    return {"configured": False}


@router.get("/api-key", response_model=ApiKeyStatus)
def api_key_status(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(_get_db_session),
):
    row = (
        session.query(UserApiKey)
        .filter(
            UserApiKey.user_id == current_user.id,
            UserApiKey.provider == "openai",
            UserApiKey.is_active.is_(True),
        )
        .first()
    )
    if not row:
        return ApiKeyStatus(configured=False)
    return ApiKeyStatus(configured=True, updated_at=row.updated_at.isoformat() if row.updated_at else None)


@router.get("/usage/summary")
async def usage_summary(current_user: User = Depends(get_current_user)):
    return await _usage.summary(str(current_user.id))


@router.get("/usage/daily")
async def usage_daily(current_user: User = Depends(get_current_user), days: int = 30):
    return {"series": await _usage.daily_series(str(current_user.id), days=days)}


@router.get("/usage/by-model")
async def usage_by_model(current_user: User = Depends(get_current_user), days: int = 30):
    return {"models": await _usage.by_model(str(current_user.id), days=days)}
