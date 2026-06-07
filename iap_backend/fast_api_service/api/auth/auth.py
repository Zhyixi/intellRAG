"""Email/password registration and JWT login."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from common.auth import create_access_token, get_current_user, hash_password, verify_password
from fast_api_service.api.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from models.user_models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["iap-auth"])


def _get_db_session():
    from containers import get_container

    db = get_container().sql_db()
    with db.session() as session:
        yield session


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, session: Session = Depends(_get_db_session)):
    existing = session.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    display_name = body.display_name.strip() or body.email.split("@")[0]
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=display_name,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user_id=user.id, email=user.email)
    logger.info("Registered user id=%s email=%s", user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(_get_db_session)):
    user = session.query(User).filter(User.email == body.email.lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    token = create_access_token(user_id=user.id, email=user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=current_user.id, email=current_user.email, display_name=current_user.display_name)
