"""Tests for common/embedding_resolver.py — mode resolution logic."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _local_model_usable
# ---------------------------------------------------------------------------

def test_local_model_usable_returns_true_with_config_json(tmp_path):
    from common.embedding_resolver import _local_model_usable
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert _local_model_usable(str(tmp_path)) is True


def test_local_model_usable_returns_true_with_safetensors(tmp_path):
    from common.embedding_resolver import _local_model_usable
    (tmp_path / "model.safetensors").write_bytes(b"")
    assert _local_model_usable(str(tmp_path)) is True


def test_local_model_usable_returns_false_when_dir_empty(tmp_path):
    from common.embedding_resolver import _local_model_usable
    assert _local_model_usable(str(tmp_path)) is False


def test_local_model_usable_returns_false_when_dir_missing():
    from common.embedding_resolver import _local_model_usable
    assert _local_model_usable("/nonexistent/path/to/model") is False


# ---------------------------------------------------------------------------
# resolve_embedding_mode — forced by env var
# ---------------------------------------------------------------------------

def test_resolve_embedding_mode_forced_local(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODE", "local")
    from importlib import reload
    import common.embedding_resolver as er
    reload(er)
    assert er.resolve_embedding_mode() == "local"


def test_resolve_embedding_mode_forced_cloud(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODE", "cloud")
    from importlib import reload
    import common.embedding_resolver as er
    reload(er)
    assert er.resolve_embedding_mode() == "cloud"


def test_resolve_embedding_mode_auto_falls_back_to_cloud_when_no_model(monkeypatch, tmp_path):
    monkeypatch.delenv("EMBEDDING_MODE", raising=False)
    # Point local_model_path to an empty directory → not usable
    monkeypatch.setenv("EMBEDDING_MODE", "")
    from importlib import reload
    import common.embedding_resolver as er
    reload(er)

    monkeypatch.setattr(er, "_local_model_usable", lambda path: False)
    result = er.resolve_embedding_mode()
    assert result == "cloud"


def test_resolve_embedding_mode_auto_uses_local_when_model_exists_and_resources_ok(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODE", raising=False)
    from importlib import reload
    import common.embedding_resolver as er
    reload(er)

    monkeypatch.setattr(er, "_local_model_usable", lambda path: True)
    monkeypatch.setattr(er, "_gpu_free_bytes", lambda: None)       # no GPU
    monkeypatch.setattr(er, "_ram_available_bytes", lambda: 8 * 1024 ** 3)  # 8 GB RAM
    result = er.resolve_embedding_mode()
    assert result == "local"


def test_resolve_embedding_mode_auto_falls_back_when_gpu_too_low(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODE", raising=False)
    from importlib import reload
    import common.embedding_resolver as er
    reload(er)

    monkeypatch.setattr(er, "_local_model_usable", lambda path: True)
    monkeypatch.setattr(er, "_gpu_free_bytes", lambda: 512 * 1024 ** 2)  # only 512 MB
    result = er.resolve_embedding_mode()
    assert result == "cloud"


def test_resolve_embedding_mode_auto_falls_back_when_ram_too_low(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODE", raising=False)
    from importlib import reload
    import common.embedding_resolver as er
    reload(er)

    monkeypatch.setattr(er, "_local_model_usable", lambda path: True)
    monkeypatch.setattr(er, "_gpu_free_bytes", lambda: None)
    monkeypatch.setattr(er, "_ram_available_bytes", lambda: 1 * 1024 ** 2)  # only 1 MB
    result = er.resolve_embedding_mode()
    assert result == "cloud"
