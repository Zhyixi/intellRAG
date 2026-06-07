#!/usr/bin/env python3
"""Simulate API usage volume spread from 2026-01-01 through today.

Writes backdated records to Mongo (safe for usage / history analytics).
Optional --live mode hits a running backend (creates real traces at *now* only).

All seeded documents include ``_simulated: True`` for easy cleanup::

    python tests/scripts/simulate_api_usage.py --purge
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from configs.config import ENV, MONGO_URI  # noqa: E402

SIMULATION_TAG = "_simulated"
MONGO_DATABASE = "LLM"
USAGE_COLLECTION = "Simulated_API_Usage"
iap_ae_HISTORY = "iap_ae_Chat_History"
iap_HISTORY = "iap_Chat_History"

DEFAULT_START = date(2026, 1, 1)
DEFAULT_END = date.today()

AE_QUESTIONS = [
    "馬達刹車異常如何處理？",
    "螺絲機浮鎖原因分析",
    "AOI 誤判率偏高",
    "回流焊溫度曲線異常",
    "PCB 板彎如何改善？",
    "設備報警 E-Stop 復歸流程",
    "真空吸嘴吸力不足",
    "錫膏印刷偏移",
]
PE_QUESTIONS = [
    "Warlock AMD 異常排查",
    "PE 線體停機原因",
    "測試站良率下降",
    "治具定位偏差",
    "壓合力度異常",
]
ROLES = [
    "10038437",
    "10037016",
    "20675832",
    "10028005",
    "20880663",
    "10051234",
    "10059876",
    "20680123",
    "10044110",
    "10055220",
]
DEVICE_IDS = ["1103017020", "1103017021", "2204018033"]


@dataclass(frozen=True)
class EndpointSpec:
    domain: str
    version: str
    operation: str
    method: str
    path: str
    weight: float
    writes_chat_history: bool = False
    chat_collection: str | None = None
    default_ret_type: str | None = None


ENDPOINTS: list[EndpointSpec] = [
    EndpointSpec("iap_ae", "v3", "retrieve", "POST", "/api/v1/ae/retrieve", 28.0, True, iap_ae_HISTORY, "ae_faca"),
    EndpointSpec("iap_ae", "v2", "retrieve", "POST", "/api/v1/ae/retrieve", 12.0, True, iap_ae_HISTORY, "ae_faca"),
    EndpointSpec("iap_ae", "v1", "retrieve", "POST", "/api/v1/ae/retrieve", 6.0, True, iap_ae_HISTORY, "ae_faca"),
    EndpointSpec("iap_ae", "v2", "invoke", "POST", "/api/v1/ae/invoke", 5.0, True, iap_ae_HISTORY, "ae_faca_invoke"),
    EndpointSpec("iap_ae", "v1", "invoke", "POST", "/api/v1/ae/invoke", 3.0, True, iap_ae_HISTORY, "ae_faca_invoke"),
    EndpointSpec("iap_ae", "v1", "history_session_id", "GET", "/api/v1/ae/history_session_id", 8.0),
    EndpointSpec("iap_ae", "v1", "history_session", "GET", "/api/v1/ae/history_session", 6.0),
    EndpointSpec("iap_ae", "v1", "faqs", "GET", "/api/v1/ae/faqs", 4.0),
    EndpointSpec("iap_ae", "v1", "get_image", "GET", "/api/v1/ae/get_image", 2.0),
    EndpointSpec("iap_ae", "v1", "analyst", "POST", "/api/v1/ae/analyst", 3.0),
    EndpointSpec("iap_ae", "v1", "analyst-detail", "POST", "/api/v1/ae/analyst-detail", 2.0),
    EndpointSpec("iap", "v3", "retrieve", "POST", "/api/v1/pe/retrieve", 14.0, True, iap_HISTORY, "pe_faca"),
    EndpointSpec("iap", "v2", "retrieve", "POST", "/api/v1/pe/retrieve", 6.0, True, iap_HISTORY, "pe_faca"),
    EndpointSpec("iap", "v1", "retrieve", "POST", "/api/v1/pe/retrieve", 3.0, True, iap_HISTORY, "pe_faca"),
    EndpointSpec("iap", "v1", "invoke", "POST", "/api/v1/pe/invoke", 2.0, True, iap_HISTORY, "pe_faca_invoke"),
    EndpointSpec("iap", "v2", "invoke", "POST", "/api/v1/pe/invoke", 2.0, True, iap_HISTORY, "pe_faca_invoke"),
    EndpointSpec("iap", "v1", "history_session_id", "GET", "/api/v1/pe/history_session_id", 4.0),
    EndpointSpec("iap", "v1", "history_session", "GET", "/api/v1/pe/history_session", 3.0),
    EndpointSpec("iap", "v1", "faqs", "GET", "/api/v1/pe/faqs", 2.0),
    EndpointSpec("iap", "v1", "analyst", "POST", "/api/v1/pe/analyst", 1.5),
    EndpointSpec("iap", "v1", "analyst-detail", "POST", "/api/v1/pe/analyst-detail", 1.0),
    EndpointSpec("common", "v1", "translate", "POST", "/api/v1/common/translate", 2.5),
    EndpointSpec("common", "v1", "check_backend", "GET", "/api/v1/common/check_backend", 1.0),
]

MONTHLY_SHARE = {
    "2026-01": 0.14,
    "2026-02": 0.16,
    "2026-03": 0.18,
    "2026-04": 0.20,
    "2026-05": 0.22,
}
HOUR_WEIGHTS = np.array(
    [0.2, 0.3, 0.5, 0.8, 1.2, 1.5, 2.0, 2.5, 3.0, 3.2, 3.0, 2.8, 2.5, 2.2, 2.0, 1.8, 1.2, 0.8, 0.4, 0.2, 0.1, 0.1, 0.1, 0.1],
    dtype=float,
)
HOUR_WEIGHTS /= HOUR_WEIGHTS.sum()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _iter_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _build_month_weights(start: date, end: date) -> dict[str, float]:
    months = {_month_key(d) for d in _iter_days(start, end)}
    weights = {m: MONTHLY_SHARE.get(m, 0.10) for m in months}
    total = sum(weights.values()) or 1.0
    return {m: w / total for m, w in weights.items()}


def sample_timestamps(
    count: int,
    *,
    start: date,
    end: date,
    seed: int,
    weekday_factor: float = 1.0,
    weekend_factor: float = 0.35,
) -> list[datetime]:
    rng = np.random.default_rng(seed)
    days = _iter_days(start, end)
    month_weights = _build_month_weights(start, end)
    day_pool: list[date] = []
    day_probs: list[float] = []
    for d in days:
        base = month_weights.get(_month_key(d), 0.1)
        if d.weekday() >= 5:
            base *= weekend_factor
        else:
            base *= weekday_factor
        day_pool.append(d)
        day_probs.append(base)
    day_probs_arr = np.array(day_probs, dtype=float)
    day_probs_arr /= day_probs_arr.sum()

    timestamps: list[datetime] = []
    for _ in range(count):
        picked_day = day_pool[int(rng.choice(len(day_pool), p=day_probs_arr))]
        hour = int(rng.choice(24, p=HOUR_WEIGHTS))
        minute = int(rng.integers(0, 60))
        second = int(rng.integers(0, 60))
        micro = int(rng.integers(0, 1_000_000))
        timestamps.append(
            datetime.combine(picked_day, time(hour, minute, second)).replace(microsecond=micro)
        )
    timestamps.sort()
    return timestamps


def choose_endpoints(count: int, *, seed: int) -> list[EndpointSpec]:
    rng = np.random.default_rng(seed + 1)
    weights = np.array([e.weight for e in ENDPOINTS], dtype=float)
    weights /= weights.sum()
    indices = rng.choice(len(ENDPOINTS), size=count, p=weights)
    return [ENDPOINTS[i] for i in indices]


def _mock_results(issue_id: str = "SIM-ISSUE-001") -> list[dict[str, Any]]:
    return [
        {
            "ISSUE_ID": issue_id,
            "Text": ["模擬檢索內容 passage: simulated chunk"],
            "Score": round(float(np.random.default_rng().random()), 3),
            "relatedFile": {},
        }
    ]


def _build_chat_history_doc(
    *,
    endpoint: EndpointSpec,
    ts: datetime,
    role: str,
    session_id: str,
    question: str,
    ret_type: str,
    duration_s: float,
) -> dict[str, Any]:
    return {
        SIMULATION_TAG: True,
        "session_id": session_id,
        "timestamp": ts,
        "Question": question,
        "Role": role,
        "Response": f"[模擬] {endpoint.domain} {endpoint.operation} 回覆內容",
        "Results": _mock_results(),
        "ret_type": ret_type,
        "Time-Consuming": str(round(duration_s, 2)),
        "sim_endpoint": endpoint.path,
        "sim_method": endpoint.method,
    }


def _build_usage_event(
    *,
    endpoint: EndpointSpec,
    ts: datetime,
    role: str,
    session_id: str | None,
    duration_ms: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = {
        SIMULATION_TAG: True,
        "timestamp": ts,
        "domain": endpoint.domain,
        "version": endpoint.version,
        "operation": endpoint.operation,
        "method": endpoint.method,
        "path": endpoint.path,
        "role": role,
        "session_id": session_id,
        "duration_ms": duration_ms,
        "environment": ENV,
    }
    if extra:
        doc.update(extra)
    return doc


def _pick_question(endpoint: EndpointSpec, rng: np.random.Generator) -> str:
    pool = AE_QUESTIONS if endpoint.domain == "iap_ae" else PE_QUESTIONS
    q = str(rng.choice(pool))
    if endpoint.version == "v3" and endpoint.operation == "retrieve":
        device = str(rng.choice(DEVICE_IDS))
        q = f"{q} {device}"
    return q


def generate_events(
    total: int,
    *,
    start: date,
    end: date,
    seed: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    timestamps = sample_timestamps(total, start=start, end=end, seed=seed)
    endpoints = choose_endpoints(total, seed=seed)
    rng = np.random.default_rng(seed + 2)

    usage_docs: list[dict[str, Any]] = []
    history_batches: list[tuple[str, dict[str, Any]]] = []
    session_by_user_day: dict[tuple[str, str], str] = {}

    for ts, endpoint in zip(timestamps, endpoints):
        role = str(rng.choice(ROLES))
        day_key = ts.date().isoformat()
        session_key = (role, day_key)
        if endpoint.writes_chat_history:
            session_id = session_by_user_day.setdefault(session_key, str(uuid.uuid4()))
        else:
            session_id = session_by_user_day.get(session_key) or str(uuid.uuid4())

        duration_s = float(rng.uniform(2.0, 45.0))
        duration_ms = int(duration_s * 1000)
        extra: dict[str, Any] = {}

        if endpoint.writes_chat_history:
            ret_type = endpoint.default_ret_type or "unknown"
            question = _pick_question(endpoint, rng)
            history_batches.append(
                (
                    endpoint.chat_collection or iap_ae_HISTORY,
                    _build_chat_history_doc(
                        endpoint=endpoint,
                        ts=ts,
                        role=role,
                        session_id=session_id,
                        question=question,
                        ret_type=ret_type,
                        duration_s=duration_s,
                    ),
                )
            )
            extra["question"] = question
            extra["ret_type"] = ret_type

        if endpoint.operation in {"history_session", "history_session_id"}:
            extra["query_session_id"] = session_id
        if endpoint.operation in {"analyst", "analyst-detail"}:
            extra["device_ids"] = [str(rng.choice(DEVICE_IDS))]

        usage_docs.append(
            _build_usage_event(
                endpoint=endpoint,
                ts=ts,
                role=role,
                session_id=session_id,
                duration_ms=duration_ms,
                extra=extra,
            )
        )

    return usage_docs, history_batches


def _chunked(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def get_database(client: MongoClient):
    return client[MONGO_DATABASE]


def insert_events(
    client: MongoClient,
    usage_docs: list[dict[str, Any]],
    history_batches: list[tuple[str, dict[str, Any]]],
    *,
    batch_size: int,
    dry_run: bool,
) -> dict[str, int]:
    db = get_database(client)
    stats = {"usage": 0, "iap_ae_history": 0, "iap_history": 0}

    if dry_run:
        stats["usage"] = len(usage_docs)
        for coll, _ in history_batches:
            if coll == iap_ae_HISTORY:
                stats["iap_ae_history"] += 1
            elif coll == iap_HISTORY:
                stats["iap_history"] += 1
        return stats

    for chunk in _chunked(usage_docs, batch_size):
        db[USAGE_COLLECTION].insert_many(chunk, ordered=False)
        stats["usage"] += len(chunk)

    by_collection: dict[str, list[dict[str, Any]]] = {}
    for coll, doc in history_batches:
        by_collection.setdefault(coll, []).append(doc)

    for coll, docs in by_collection.items():
        for chunk in _chunked(docs, batch_size):
            db[coll].insert_many(chunk, ordered=False)
            if coll == iap_ae_HISTORY:
                stats["iap_ae_history"] += len(chunk)
            elif coll == iap_HISTORY:
                stats["iap_history"] += len(chunk)
    return stats


def purge_simulated(client: MongoClient) -> dict[str, int]:
    db = get_database(client)
    deleted: dict[str, int] = {}
    for name in (USAGE_COLLECTION, iap_ae_HISTORY, iap_HISTORY):
        result = db[name].delete_many({SIMULATION_TAG: True})
        deleted[name] = int(result.deleted_count)
    return deleted


def summarize_by_day(usage_docs: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for doc in usage_docs:
        day = doc["timestamp"].strftime("%Y-%m-%d")
        key = f"{doc['domain']}:{doc['operation']}"
        summary.setdefault(day, {})
        summary[day][key] = summary[day].get(key, 0) + 1
    return summary


def export_summary(path: Path, usage_docs: list[dict[str, Any]]) -> None:
    by_day = summarize_by_day(usage_docs)
    by_endpoint: dict[str, int] = {}
    by_month: dict[str, int] = {}
    for doc in usage_docs:
        ep = doc["path"]
        by_endpoint[ep] = by_endpoint.get(ep, 0) + 1
        month = doc["timestamp"].strftime("%Y-%m")
        by_month[month] = by_month.get(month, 0) + 1

    payload = {
        "total_events": len(usage_docs),
        "by_month": dict(sorted(by_month.items())),
        "by_endpoint": dict(sorted(by_endpoint.items(), key=lambda x: -x[1])),
        "by_day_sample": dict(list(sorted(by_day.items()))[:7]),
        "last_day_sample": dict(list(sorted(by_day.items()))[-7:]),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed backdated API usage simulation into Mongo (2026 -> today)."
    )
    parser.add_argument("--total", type=int, default=3000, help="Number of simulated API events.")
    parser.add_argument("--start", type=str, default=DEFAULT_START.isoformat())
    parser.add_argument("--end", type=str, default=DEFAULT_END.isoformat())
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--purge", action="store_true", help="Delete prior simulated records only.")
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="Allow running when ENV=prod (default: refuse).",
    )
    parser.add_argument(
        "--summary-out",
        type=str,
        default="",
        help="Optional JSON summary output path.",
    )
    args = parser.parse_args()

    if ENV == "prod" and not args.allow_prod:
        print("[error] ENV=prod：請在 dev 執行，或明確加上 --allow-prod。", file=sys.stderr)
        return 1

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if end < start:
        print("[error] --end 必須 >= --start", file=sys.stderr)
        return 1

    client = MongoClient(MONGO_URI)

    try:
        if args.purge:
            deleted = purge_simulated(client)
            print(f"[purge] removed simulated docs: {deleted}")
            if args.total <= 0:
                return 0

        print(f"[info] ENV={ENV} range={start}..{end} total={args.total} seed={args.seed}")
        usage_docs, history_batches = generate_events(
            args.total,
            start=start,
            end=end,
            seed=args.seed,
        )
        stats = insert_events(
            client,
            usage_docs,
            history_batches,
            batch_size=max(1, args.batch_size),
            dry_run=args.dry_run,
        )
        print(f"[info] inserted (or dry-run counted): {stats}")

        if args.summary_out:
            out = Path(args.summary_out)
            export_summary(out, usage_docs)
            print(f"[info] summary written to {out}")

        if args.dry_run:
            print("[dry-run] No documents written.")
        else:
            print(
                "[hint] 分析用量: db.Simulated_API_Usage.aggregate(["
                "{$match: {_simulated: true}}, "
                "{$group: {_id: {d: {$dateToString: {format: '%Y-%m-%d', date: '$timestamp'}}}, n: {$sum: 1}}}, "
                "{$sort: {_id: 1}}])"
            )
            print("[hint] 清除模擬資料: python tests/scripts/simulate_api_usage.py --purge --total 0")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
