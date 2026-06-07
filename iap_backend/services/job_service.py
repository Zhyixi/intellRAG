"""Redis-backed job progress for document embedding (pause / resume / checkpoint)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis


class JobService:
    PREFIX = "nb_job:"
    LOCK_PREFIX = "nb_job_lock:"
    TTL_SECONDS = 86400 * 7
    STALE_SECONDS = 120

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    def new_job_id(self) -> str:
        return str(uuid.uuid4())

    def _key(self, job_id: str) -> str:
        return f"{self.PREFIX}{job_id}"

    def _lock_key(self, doc_id: str) -> str:
        return f"{self.LOCK_PREFIX}{doc_id}"

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, job_id: str, *, user_id: int, doc_id: str, filename: str) -> dict:
        data = {
            "job_id": job_id,
            "user_id": user_id,
            "doc_id": doc_id,
            "filename": filename,
            "stage": "uploaded",
            "percent": 0,
            "total_pages": 0,
            "processed_pages": 0,
            "indexed_chunks": 0,
            "message": "已上傳，等待處理 / Uploaded, waiting to process",
            "status": "running",
            "pause_requested": False,
            "cancel_requested": False,
            "updated_at": self._now_iso(),
        }
        self._redis.setex(self._key(job_id), self.TTL_SECONDS, json.dumps(data, ensure_ascii=False))
        return data

    def restore_from_doc(
        self,
        job_id: str,
        *,
        user_id: int,
        doc_id: str,
        filename: str,
        checkpoint: dict | None,
        status: str = "running",
    ) -> dict:
        cp = checkpoint or {}
        total = int(cp.get("total_pages") or 0)
        next_idx = int(cp.get("next_page_index") or 0)
        indexed = int(cp.get("indexed_chunks") or 0)
        processed = next_idx
        percent = self._page_percent(processed, total) if total else 0
        data = {
            "job_id": job_id,
            "user_id": user_id,
            "doc_id": doc_id,
            "filename": filename,
            "stage": "resuming",
            "percent": percent,
            "total_pages": total,
            "processed_pages": processed,
            "indexed_chunks": indexed,
            "message": f"從第 {next_idx + 1} 頁繼續… / Resuming from page {next_idx + 1}…",
            "status": status,
            "pause_requested": False,
            "cancel_requested": False,
            "updated_at": self._now_iso(),
        }
        self._redis.setex(self._key(job_id), self.TTL_SECONDS, json.dumps(data, ensure_ascii=False))
        return data

    @staticmethod
    def _page_percent(processed: int, total: int) -> int:
        if total <= 0:
            return 0
        return min(95, 15 + int(processed / total * 80))

    def update(
        self,
        job_id: str,
        *,
        stage: str,
        percent: int,
        message: str,
        status: str = "running",
        total_pages: int | None = None,
        processed_pages: int | None = None,
        indexed_chunks: int | None = None,
    ) -> dict:
        raw = self._redis.get(self._key(job_id))
        data: dict[str, Any] = json.loads(raw) if raw else {"job_id": job_id}
        data.update(
            {
                "stage": stage,
                "percent": min(100, max(0, percent)),
                "message": message,
                "status": status,
                "updated_at": self._now_iso(),
            }
        )
        if total_pages is not None:
            data["total_pages"] = total_pages
        if processed_pages is not None:
            data["processed_pages"] = processed_pages
        if indexed_chunks is not None:
            data["indexed_chunks"] = indexed_chunks
        self._redis.setex(self._key(job_id), self.TTL_SECONDS, json.dumps(data, ensure_ascii=False))
        return data

    def complete(self, job_id: str, message: str = "處理完成 / Processing complete") -> dict:
        return self.update(
            job_id,
            stage="done",
            percent=100,
            message=message,
            status="done",
        )

    def fail(self, job_id: str, message: str) -> dict:
        return self.update(job_id, stage="failed", percent=0, message=message, status="failed")

    def pause(self, job_id: str, message: str = "已暫停 / Paused") -> dict:
        return self.update(job_id, stage="paused", message=message, status="paused")

    def mark_interrupted(self, job_id: str, message: str = "工作中斷，可繼續 / Interrupted, resumable") -> dict:
        raw = self._redis.get(self._key(job_id))
        if not raw:
            return {}
        data = json.loads(raw)
        pct = int(data.get("percent") or 0)
        return self.update(job_id, stage="interrupted", percent=pct, message=message, status="interrupted")

    def request_pause(self, job_id: str) -> dict | None:
        raw = self._redis.get(self._key(job_id))
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("status") not in ("running", "interrupted"):
            return data
        data["pause_requested"] = True
        data["message"] = "正在暫停… / Pausing…"
        data["updated_at"] = self._now_iso()
        self._redis.setex(self._key(job_id), self.TTL_SECONDS, json.dumps(data, ensure_ascii=False))
        return data

    def request_cancel(self, job_id: str) -> dict | None:
        raw = self._redis.get(self._key(job_id))
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("status") in ("done", "failed"):
            return data
        data["cancel_requested"] = True
        data["message"] = "正在停止… / Stopping…"
        data["updated_at"] = self._now_iso()
        self._redis.setex(self._key(job_id), self.TTL_SECONDS, json.dumps(data, ensure_ascii=False))
        return data

    def clear_control_flags(self, job_id: str) -> None:
        raw = self._redis.get(self._key(job_id))
        if not raw:
            return
        data = json.loads(raw)
        data["pause_requested"] = False
        data["cancel_requested"] = False
        self._redis.setex(self._key(job_id), self.TTL_SECONDS, json.dumps(data, ensure_ascii=False))

    def is_pause_requested(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and job.get("pause_requested"))

    def is_cancel_requested(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and job.get("cancel_requested"))

    def is_stale_running(self, job: dict) -> bool:
        if job.get("status") != "running":
            return False
        updated = job.get("updated_at")
        if not updated:
            return True
        try:
            ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age > self.STALE_SECONDS
        except ValueError:
            return True

    def get(self, job_id: str) -> dict | None:
        raw = self._redis.get(self._key(job_id))
        if not raw:
            return None
        return json.loads(raw)

    def try_acquire_lock(self, doc_id: str, job_id: str, ttl: int = 7200) -> bool:
        return bool(self._redis.set(self._lock_key(doc_id), job_id, nx=True, ex=ttl))

    def release_lock(self, doc_id: str, job_id: str | None = None) -> None:
        if job_id:
            current = self._redis.get(self._lock_key(doc_id))
            if current is not None:
                cur = current.decode() if isinstance(current, bytes) else str(current)
                if cur != job_id:
                    return
        self._redis.delete(self._lock_key(doc_id))
