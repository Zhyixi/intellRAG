"""Auth API client for Streamlit frontend."""
from __future__ import annotations

import logging
import os
import sys

import requests

sys.path.extend([".", ".."])
from configs.config import auth_api_url

DEFAULT_TIMEOUT = int(os.getenv("iap_API_TIMEOUT", "60"))
_BACKEND_PROXIES = {"http": None, "https": None}


def _request_json(method: str, url: str, **kwargs) -> tuple[dict | None, int]:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    kwargs.setdefault("proxies", _BACKEND_PROXIES)
    try:
        response = requests.request(method, url, **kwargs)
        status = response.status_code
        if status in (200, 201):
            try:
                return response.json(), status
            except ValueError:
                return None, status
        detail = None
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        logging.warning("Auth API %s %s -> %s %s", method, url, status, detail)
        return detail if isinstance(detail, dict) else {"detail": str(detail)}, status
    except requests.RequestException as exc:
        logging.error("Auth API %s %s failed: %s", method, url, exc)
        return {"detail": str(exc)}, 0


def register(email: str, password: str, display_name: str = "") -> dict:
    payload = {"email": email, "password": password, "display_name": display_name}
    data, status = _request_json("POST", auth_api_url.register, json=payload)
    return {"data": data, "status_code": status}


def login(email: str, password: str) -> dict:
    payload = {"email": email, "password": password}
    data, status = _request_json("POST", auth_api_url.login, json=payload)
    return {"data": data, "status_code": status}


def get_me(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    data, status = _request_json("GET", auth_api_url.me, headers=headers)
    return {"data": data, "status_code": status}
