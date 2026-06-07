"""Fernet encryption for user API keys."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from configs.config import ENCRYPTION_KEY


def _fernet() -> Fernet:
    raw = (ENCRYPTION_KEY or "").strip()
    if not raw:
        raw = "iap-dev-encryption-key-change-in-production!!"
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()
