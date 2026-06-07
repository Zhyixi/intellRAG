import os

import pytest

from common.cors_settings import build_cors_origins
from common.path_security import UnsafePathError, resolve_allowed_file_path
from fast_api_service.api_errors import public_error_detail, sanitize_http_exception_detail


pytestmark = pytest.mark.unit


def test_build_cors_origins_default_includes_local_frontend_ports(monkeypatch):
    # dev.env / docker 常会注入 CORS_ORIGINS；此用例只测「未设变量」时的代码默认值
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    origins = build_cors_origins()
    assert "http://localhost:4200" in origins
    assert "http://localhost:9504" in origins
    assert "*" not in origins


def test_build_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://10.0.0.1:9504,https://faca.example.com")
    assert build_cors_origins() == ["http://10.0.0.1:9504", "https://faca.example.com"]


def test_resolve_allowed_file_path_rejects_outside_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    safe_file = allowed / "ok.txt"
    safe_file.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("y", encoding="utf-8")

    resolved = resolve_allowed_file_path(str(safe_file), allowed_roots=[str(allowed)])
    assert resolved == str(safe_file.resolve())

    with pytest.raises(UnsafePathError):
        resolve_allowed_file_path(str(outside), allowed_roots=[str(allowed)])


def test_sanitize_http_exception_detail_strips_traceback():
    leaked = {
        "responseCode": 500,
        "responseMessage": "Internal Server Error - Traceback (most recent call last):\n  File \"x.py\"",
    }
    safe = sanitize_http_exception_detail(leaked)
    assert safe["responseCode"] == 500
    assert "Traceback" not in safe["responseMessage"]
    assert "errorId" in safe


def test_public_error_detail_shape():
    body = public_error_detail(error_id="abc123")
    assert body["responseCode"] == 500
    assert body["errorId"] == "abc123"
