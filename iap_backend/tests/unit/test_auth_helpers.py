"""Tests for common/auth.py — password hashing and JWT helpers."""
from __future__ import annotations

import datetime

import jwt
import pytest
from fastapi import HTTPException

from common.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from configs.config import JWT_ALGORITHM, JWT_SECRET

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def test_hash_password_is_not_plain():
    hashed = hash_password("mysecret")
    assert hashed != "mysecret"
    assert len(hashed) > 20


def test_verify_password_correct():
    hashed = hash_password("correct-password")
    assert verify_password("correct-password", hashed) is True


def test_verify_password_wrong():
    hashed = hash_password("correct-password")
    assert verify_password("wrong-password", hashed) is False


def test_hash_same_plaintext_produces_different_hash():
    h1 = hash_password("same")
    h2 = hash_password("same")
    # bcrypt embeds a random salt, so two hashes must differ
    assert h1 != h2


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def test_create_and_decode_access_token():
    token = create_access_token(user_id=42, email="user@example.com")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["email"] == "user@example.com"


def test_decode_access_token_rejects_expired():
    expired_payload = {
        "sub": "1",
        "email": "x@example.com",
        "exp": datetime.datetime.utcnow() - datetime.timedelta(seconds=1),
        "iat": datetime.datetime.utcnow() - datetime.timedelta(minutes=5),
    }
    token = jwt.encode(expired_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_decode_access_token_rejects_wrong_secret():
    payload = {
        "sub": "1",
        "email": "x@example.com",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        "iat": datetime.datetime.utcnow(),
    }
    token = jwt.encode(payload, "wrong-secret", algorithm=JWT_ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_decode_access_token_rejects_garbage():
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not.a.jwt")
    assert exc_info.value.status_code == 401


def test_token_contains_sub_and_email():
    token = create_access_token(user_id=99, email="admin@example.com")
    raw = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert raw["sub"] == "99"
    assert raw["email"] == "admin@example.com"
    assert "exp" in raw
    assert "iat" in raw
