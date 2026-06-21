"""Playwright E2E fixtures（在 iap_frontend 容器内执行）。"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from playwright.sync_api import Page, expect

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8501").rstrip("/")
BACKEND_URL = os.getenv("E2E_BACKEND_URL", "http://iap_backend:44000").rstrip("/")
E2E_EMAIL = os.getenv("E2E_EMAIL", "")
E2E_PASSWORD = os.getenv("E2E_PASSWORD", "E2eTestPass123!!")
PROXIES = {"http": None, "https": None}


def _register_user(email: str, password: str) -> None:
    resp = requests.post(
        f"{BACKEND_URL}/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "E2E User"},
        timeout=30,
        proxies=PROXIES,
    )
    if resp.status_code in (200, 201, 409):
        return
    if resp.status_code == 422:
        login = requests.post(
            f"{BACKEND_URL}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=30,
            proxies=PROXIES,
        )
        if login.status_code == 200:
            return
    resp.raise_for_status()


def _login_api(email: str, password: str) -> str:
    resp = requests.post(
        f"{BACKEND_URL}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=30,
        proxies=PROXIES,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def test_credentials() -> tuple[str, str]:
    email = E2E_EMAIL or f"e2e_{uuid.uuid4().hex[:10]}@example.com"
    _register_user(email, E2E_PASSWORD)
    return email, E2E_PASSWORD


@pytest.fixture(scope="session")
def browser_context_args():
    return {"viewport": {"width": 1400, "height": 900}}


def wait_for_streamlit(page: Page, timeout_ms: int = 60_000) -> None:
    page.wait_for_selector("[data-testid='iap-react-app']", timeout=timeout_ms)


def login_via_ui(page: Page, email: str, password: str) -> None:
    """登录页 smoke 测试（真实 UI 流程）。"""
    wait_for_streamlit(page)
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.get_by_role("tab", name="登入 / Sign In").click()
    login_panel = page.get_by_role("tabpanel", name="登入 / Sign In")
    login_panel.get_by_label("電子郵件 / Email").fill(email)
    login_panel.locator('input[type="password"]').fill(password)
    login_panel.get_by_role("button", name="登入 / Sign In").click()
    page.wait_for_timeout(2000)


def login_via_token(page: Page, email: str, password: str) -> None:
    """API 登录 + dev e2e_token 注入（可靠、快速）。"""
    token = _login_api(email, password)
    page.goto(f"{BASE_URL}/?e2e_token={token}", wait_until="domcontentloaded", timeout=60_000)
    wait_for_streamlit(page)
    expect(page.get_by_role("link", name="我的筆記本 / My Notebook")).to_be_visible(timeout=60_000)


@pytest.fixture
def logged_in_page(page: Page, test_credentials: tuple[str, str]) -> Page:
    email, password = test_credentials
    login_via_token(page, email, password)
    return page
