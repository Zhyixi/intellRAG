"""Tests for services/job_service.py — Redis-backed Job state machine.

Uses an in-memory FakeRedis so no real Redis instance is needed.
"""
from __future__ import annotations

import datetime
import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.job_service import JobService

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal in-memory Redis fake
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Simulates the Redis commands used by JobService."""

    def __init__(self):
        self._store: dict[str, tuple[bytes, float | None]] = {}  # key -> (value, expire_at)

    def _expired(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return True
        _, exp = entry
        if exp is not None and time.monotonic() > exp:
            del self._store[key]
            return True
        return False

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = (value.encode() if isinstance(value, str) else value,
                            time.monotonic() + ttl)

    def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool:
        if nx and not self._expired(key) and key in self._store:
            return False
        exp = time.monotonic() + ex if ex else None
        self._store[key] = (str(value).encode(), exp)
        return True

    def get(self, key: str) -> bytes | None:
        if self._expired(key):
            return None
        entry = self._store.get(key)
        return entry[0] if entry else None

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def svc():
    return JobService(_FakeRedis())


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def test_create_returns_running_job(svc):
    job = svc.create("j1", user_id=1, doc_id="d1", filename="doc.pdf")
    assert job["job_id"] == "j1"
    assert job["status"] == "running"
    assert job["percent"] == 0
    assert job["stage"] == "uploaded"


def test_create_persists_to_redis(svc):
    svc.create("j2", user_id=1, doc_id="d2", filename="file.pdf")
    fetched = svc.get("j2")
    assert fetched is not None
    assert fetched["doc_id"] == "d2"


# ---------------------------------------------------------------------------
# _page_percent
# ---------------------------------------------------------------------------

def test_page_percent_zero_total():
    assert JobService._page_percent(0, 0) == 0


def test_page_percent_half():
    pct = JobService._page_percent(5, 10)
    assert 15 <= pct <= 95


def test_page_percent_full_caps_at_95():
    assert JobService._page_percent(10, 10) == 95


def test_page_percent_negative_total():
    assert JobService._page_percent(5, -1) == 0


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_update_changes_stage_and_percent(svc):
    svc.create("j3", user_id=1, doc_id="d3", filename="f.pdf")
    updated = svc.update("j3", stage="embedding", percent=50, message="half done")
    assert updated["stage"] == "embedding"
    assert updated["percent"] == 50
    assert svc.get("j3")["stage"] == "embedding"


def test_update_clamps_percent_to_100(svc):
    svc.create("j4", user_id=1, doc_id="d4", filename="f.pdf")
    updated = svc.update("j4", stage="done", percent=999, message="ok")
    assert updated["percent"] == 100


def test_update_clamps_percent_minimum_0(svc):
    svc.create("j5", user_id=1, doc_id="d5", filename="f.pdf")
    updated = svc.update("j5", stage="failed", percent=-50, message="fail")
    assert updated["percent"] == 0


def test_update_sets_optional_fields(svc):
    svc.create("j6", user_id=1, doc_id="d6", filename="f.pdf")
    svc.update("j6", stage="indexing", percent=60, message="ok",
               total_pages=20, processed_pages=12, indexed_chunks=100)
    job = svc.get("j6")
    assert job["total_pages"] == 20
    assert job["processed_pages"] == 12
    assert job["indexed_chunks"] == 100


# ---------------------------------------------------------------------------
# complete / fail / pause / mark_interrupted
# ---------------------------------------------------------------------------

def test_complete_sets_done(svc):
    svc.create("j7", user_id=1, doc_id="d7", filename="f.pdf")
    svc.complete("j7")
    job = svc.get("j7")
    assert job["status"] == "done"
    assert job["percent"] == 100


def test_fail_sets_failed(svc):
    svc.create("j8", user_id=1, doc_id="d8", filename="f.pdf")
    svc.fail("j8", "something broke")
    job = svc.get("j8")
    assert job["status"] == "failed"
    assert "something broke" in job["message"]


def test_pause_sets_paused(svc):
    svc.create("j9", user_id=1, doc_id="d9", filename="f.pdf")
    svc.pause("j9")
    job = svc.get("j9")
    assert job["status"] == "paused"
    assert job["stage"] == "paused"


def test_mark_interrupted_keeps_existing_percent(svc):
    svc.create("j10", user_id=1, doc_id="d10", filename="f.pdf")
    svc.update("j10", stage="embedding", percent=45, message="running")
    svc.mark_interrupted("j10")
    job = svc.get("j10")
    assert job["status"] == "interrupted"
    assert job["percent"] == 45


def test_mark_interrupted_missing_job_returns_empty(svc):
    result = svc.mark_interrupted("nonexistent")
    assert result == {}


# ---------------------------------------------------------------------------
# request_pause / request_cancel / clear_control_flags
# ---------------------------------------------------------------------------

def test_request_pause_sets_flag(svc):
    svc.create("j11", user_id=1, doc_id="d11", filename="f.pdf")
    svc.request_pause("j11")
    assert svc.is_pause_requested("j11") is True


def test_request_pause_does_not_set_flag_when_done(svc):
    svc.create("j12", user_id=1, doc_id="d12", filename="f.pdf")
    svc.complete("j12")
    result = svc.request_pause("j12")
    # done status → returned unchanged without setting flag
    assert result is not None
    assert svc.is_pause_requested("j12") is False


def test_request_cancel_sets_flag(svc):
    svc.create("j13", user_id=1, doc_id="d13", filename="f.pdf")
    svc.request_cancel("j13")
    assert svc.is_cancel_requested("j13") is True


def test_request_cancel_ignores_done_job(svc):
    svc.create("j14", user_id=1, doc_id="d14", filename="f.pdf")
    svc.complete("j14")
    svc.request_cancel("j14")
    assert svc.is_cancel_requested("j14") is False


def test_clear_control_flags(svc):
    svc.create("j15", user_id=1, doc_id="d15", filename="f.pdf")
    svc.request_pause("j15")
    svc.request_cancel("j15")
    svc.clear_control_flags("j15")
    assert svc.is_pause_requested("j15") is False
    assert svc.is_cancel_requested("j15") is False


def test_request_pause_missing_job_returns_none(svc):
    assert svc.request_pause("no-such-job") is None


def test_request_cancel_missing_job_returns_none(svc):
    assert svc.request_cancel("no-such-job") is None


# ---------------------------------------------------------------------------
# is_stale_running
# ---------------------------------------------------------------------------

def test_is_stale_running_fresh_job(svc):
    job = svc.create("j16", user_id=1, doc_id="d16", filename="f.pdf")
    assert svc.is_stale_running(job) is False


def test_is_stale_running_old_timestamp(svc):
    old_time = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(seconds=JobService.STALE_SECONDS + 10)).isoformat()
    job = {"status": "running", "updated_at": old_time}
    assert svc.is_stale_running(job) is True


def test_is_stale_running_not_running_status(svc):
    job = {"status": "paused", "updated_at": "2000-01-01T00:00:00+00:00"}
    assert svc.is_stale_running(job) is False


def test_is_stale_running_missing_updated_at(svc):
    job = {"status": "running"}
    assert svc.is_stale_running(job) is True


# ---------------------------------------------------------------------------
# get / missing key
# ---------------------------------------------------------------------------

def test_get_returns_none_for_missing_job(svc):
    assert svc.get("does-not-exist") is None


# ---------------------------------------------------------------------------
# lock helpers
# ---------------------------------------------------------------------------

def test_try_acquire_lock_succeeds_first_time(svc):
    assert svc.try_acquire_lock("doc-abc", "job-1") is True


def test_try_acquire_lock_fails_when_already_held(svc):
    svc.try_acquire_lock("doc-abc", "job-1")
    assert svc.try_acquire_lock("doc-abc", "job-2") is False


def test_release_lock_allows_reacquire(svc):
    svc.try_acquire_lock("doc-xyz", "job-1")
    svc.release_lock("doc-xyz", "job-1")
    assert svc.try_acquire_lock("doc-xyz", "job-2") is True


def test_release_lock_ignores_wrong_job_id(svc):
    svc.try_acquire_lock("doc-xyz2", "job-owner")
    svc.release_lock("doc-xyz2", "job-other")  # different job_id → should be no-op
    # Lock should still be held by "job-owner"
    assert svc.try_acquire_lock("doc-xyz2", "job-new") is False


# ---------------------------------------------------------------------------
# restore_from_doc
# ---------------------------------------------------------------------------

def test_restore_from_doc_with_checkpoint(svc):
    checkpoint = {"total_pages": 10, "next_page_index": 3, "indexed_chunks": 30}
    job = svc.restore_from_doc(
        "j17", user_id=1, doc_id="d17", filename="f.pdf",
        checkpoint=checkpoint, status="running"
    )
    assert job["total_pages"] == 10
    assert job["processed_pages"] == 3
    assert job["indexed_chunks"] == 30
    assert job["status"] == "running"


def test_restore_from_doc_no_checkpoint(svc):
    job = svc.restore_from_doc(
        "j18", user_id=1, doc_id="d18", filename="f.pdf",
        checkpoint=None
    )
    assert job["total_pages"] == 0
    assert job["percent"] == 0
