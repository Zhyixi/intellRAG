"""Proxy Langfuse Metrics API for per-user token/cost (userId filter)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from configs.config import LANGFUSE_BASE_URL, LANGFUSE_CONFIGURED, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

logger = logging.getLogger(__name__)

# USD per 1M tokens (estimate when Langfuse has no model price)
MODEL_RATES = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "default": {"input": 0.50, "output": 1.50},
}


class LangfuseUsageService:
    def __init__(self):
        self._base = (LANGFUSE_BASE_URL or "http://iap_langfuse_web:3000").rstrip("/")
        self._enabled = LANGFUSE_CONFIGURED

    def _auth(self) -> tuple[str, str]:
        return LANGFUSE_PUBLIC_KEY or "", LANGFUSE_SECRET_KEY or ""

    async def _get_daily(self, user_id: str, days: int = 30) -> list[dict]:
        if not self._enabled:
            return []
        url = f"{self._base}/api/public/metrics/daily"
        params = {"userId": user_id, "limit": days}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params, auth=self._auth())
                if resp.status_code != 200:
                    logger.warning("Langfuse daily metrics %s: %s", resp.status_code, resp.text[:200])
                    return []
                return resp.json().get("data") or []
        except Exception as exc:
            logger.warning("Langfuse daily metrics failed: %s", exc)
            return []

    async def summary(self, user_id: str) -> dict[str, Any]:
        daily = await self._get_daily(user_id, days=60)
        today = datetime.utcnow().date()
        month_start = today.replace(day=1)

        def _sum_rows(rows: list[dict]) -> dict:
            tokens = 0
            cost = 0.0
            for row in rows:
                cost += float(row.get("totalCost") or 0)
                for u in row.get("usage") or []:
                    tokens += int(u.get("totalUsage") or 0)
            return {"tokens": tokens, "cost_usd": round(cost, 4)}

        today_rows = [r for r in daily if r.get("date") == str(today)]
        month_rows = [
            r for r in daily if r.get("date") and datetime.fromisoformat(r["date"]).date() >= month_start
        ]
        all_time = _sum_rows(daily)
        return {
            "langfuse_enabled": self._enabled,
            "today": _sum_rows(today_rows),
            "month": _sum_rows(month_rows),
            "all_time": all_time,
            "note": "费用为 Langfuse 估算值（基于 model 单价表）" if self._enabled else "Langfuse 未配置",
        }

    async def daily_series(self, user_id: str, days: int = 30) -> list[dict]:
        daily = await self._get_daily(user_id, days=days)
        out = []
        for row in daily:
            tokens = sum(int(u.get("totalUsage") or 0) for u in (row.get("usage") or []))
            out.append(
                {
                    "date": row.get("date"),
                    "tokens": tokens,
                    "cost_usd": float(row.get("totalCost") or 0),
                    "trace_count": int(row.get("countTraces") or 0),
                }
            )
        return sorted(out, key=lambda x: x["date"] or "")

    async def by_model(self, user_id: str, days: int = 30) -> list[dict]:
        daily = await self._get_daily(user_id, days=days)
        agg: dict[str, dict] = {}
        for row in daily:
            for u in row.get("usage") or []:
                model = u.get("model") or "unknown"
                bucket = agg.setdefault(model, {"model": model, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                bucket["input_tokens"] += int(u.get("inputUsage") or 0)
                bucket["output_tokens"] += int(u.get("outputUsage") or 0)
                bucket["total_tokens"] += int(u.get("totalUsage") or 0)
        return list(agg.values())
