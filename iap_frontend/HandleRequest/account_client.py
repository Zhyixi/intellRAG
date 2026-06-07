"""Account API client."""
from __future__ import annotations

import logging
import os
import sys

import requests

sys.path.extend([".", ".."])

DEFAULT_TIMEOUT = int(os.getenv("iap_API_TIMEOUT", "60"))
_BACKEND_PROXIES = {"http": None, "https": None}


def _api_v1_base() -> str:
    host = os.getenv("BACKEND_IP", "iap_backend")
    port = os.getenv("BACKEND_PORT", "44000")
    return f"http://{host}:{port}/api/v1"


class _AccountApiUrl:
    @property
    def profile(self) -> str:
        return f"{_api_v1_base()}/account/profile"

    @property
    def api_key(self) -> str:
        return f"{_api_v1_base()}/account/api-key"

    @property
    def usage_summary(self) -> str:
        return f"{_api_v1_base()}/account/usage/summary"

    @property
    def usage_daily(self) -> str:
        return f"{_api_v1_base()}/account/usage/daily"

    @property
    def usage_by_model(self) -> str:
        return f"{_api_v1_base()}/account/usage/by-model"


account_api_url = _AccountApiUrl()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _request(method: str, url: str, token: str, **kwargs) -> tuple[dict | list | None, int]:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    kwargs.setdefault("proxies", _BACKEND_PROXIES)
    kwargs["headers"] = {**_headers(token), **kwargs.get("headers", {})}
    try:
        resp = requests.request(method, url, **kwargs)
        if resp.status_code in (200, 201, 204):
            if resp.status_code == 204 or not resp.content:
                return {}, resp.status_code
            return resp.json(), resp.status_code
        logging.warning("Account API %s %s -> %s", method, url, resp.status_code)
        try:
            return resp.json(), resp.status_code
        except ValueError:
            return {"detail": resp.text}, resp.status_code
    except requests.RequestException as exc:
        return {"detail": str(exc)}, 0


def get_profile(token: str) -> dict:
    from HandleRequest.auth_client import get_me

    return get_me(token)


def update_profile(token: str, *, display_name: str | None = None, password: str | None = None, current_password: str | None = None) -> dict:
    payload = {}
    if display_name is not None:
        payload["display_name"] = display_name
    if password:
        payload["password"] = password
        payload["current_password"] = current_password
    data, status = _request("PATCH", account_api_url.profile, token, json=payload)
    return {"data": data, "status_code": status}


def get_api_key_status(token: str) -> dict:
    data, status = _request("GET", account_api_url.api_key, token)
    return {"data": data, "status_code": status}


def set_api_key(token: str, api_key: str) -> dict:
    data, status = _request("PUT", account_api_url.api_key, token, json={"api_key": api_key})
    return {"data": data, "status_code": status}


def delete_api_key(token: str) -> dict:
    data, status = _request("DELETE", account_api_url.api_key, token)
    return {"data": data, "status_code": status}


def get_usage_summary(token: str) -> dict:
    data, status = _request("GET", account_api_url.usage_summary, token)
    return {"data": data, "status_code": status}


def get_usage_daily(token: str, days: int = 30) -> dict:
    url = f"{account_api_url.usage_daily}?days={days}"
    data, status = _request("GET", url, token)
    return {"data": data, "status_code": status}


def get_usage_by_model(token: str, days: int = 30) -> dict:
    url = f"{account_api_url.usage_by_model}?days={days}"
    data, status = _request("GET", url, token)
    return {"data": data, "status_code": status}
