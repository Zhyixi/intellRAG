#!/usr/bin/env python3
"""Purge Langfuse traces from the DEV project only.

Safety:
- Uses credentials from dev.env only (never prod.env).
- Verifies prod project trace count before/after; aborts reporting if prod changed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_ENV_FILE = REPO_ROOT / "dev.env"
PROD_ENV_FILE = REPO_ROOT / "prod.env"

# Known project keys (public prefix) — refuse to run if credentials mismatch.
DEV_PUBLIC_PREFIX = "pk-lf-86efdda3"
PROD_PUBLIC_PREFIX = "pk-lf-59e2533f"


def _load_langfuse_creds(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        raise FileNotFoundError(f"Missing env file: {env_file}")
    load_dotenv(env_file, override=True)
    public = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip().strip('"')
    secret = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip().strip('"')
    base = (os.getenv("LANGFUSE_BASE_URL") or "").strip().strip('"')
    if not public or not secret or not base:
        raise RuntimeError(f"Langfuse credentials incomplete in {env_file}")
    return {"public_key": public, "secret_key": secret, "host": base}


def _client(creds: dict[str, str]) -> Langfuse:
    return Langfuse(
        public_key=creds["public_key"],
        secret_key=creds["secret_key"],
        host=creds["host"],
    )


def _trace_total(client: Langfuse) -> int:
    response = client.api.trace.list(limit=1, page=1)
    meta = getattr(response, "meta", None)
    return int(getattr(meta, "total_items", 0) or 0)


def _assert_dev_credentials(creds: dict[str, str]) -> None:
    public = creds["public_key"]
    if not public.startswith(DEV_PUBLIC_PREFIX):
        raise RuntimeError(
            f"Refusing to purge: public key {public[:16]}... is not the dev project key."
        )
    if public.startswith(PROD_PUBLIC_PREFIX):
        raise RuntimeError("Refusing to purge: prod credentials detected.")


def _assert_prod_credentials(creds: dict[str, str]) -> None:
    public = creds["public_key"]
    if not public.startswith(PROD_PUBLIC_PREFIX):
        raise RuntimeError(
            f"Refusing to verify prod: public key {public[:16]}... is not the prod project key."
        )


def purge_dev_traces(
    *,
    batch_size: int = 100,
    max_batches: int | None = None,
    sleep_s: float = 0.2,
    dry_run: bool = False,
) -> dict[str, int]:
    dev_creds = _load_langfuse_creds(DEV_ENV_FILE)
    prod_creds = _load_langfuse_creds(PROD_ENV_FILE)
    _assert_dev_credentials(dev_creds)
    _assert_prod_credentials(prod_creds)

    dev_client = _client(dev_creds)
    prod_client = _client(prod_creds)

    prod_before = _trace_total(prod_client)
    dev_before = _trace_total(dev_client)
    print(f"[info] prod traces (baseline, read-only): {prod_before}", flush=True)
    print(f"[info] dev traces (target): {dev_before}", flush=True)

    if dry_run:
        print("[dry-run] No traces deleted.", flush=True)
        dev_client.shutdown()
        prod_client.shutdown()
        return {
            "prod_before": prod_before,
            "dev_before": dev_before,
            "deleted": 0,
            "batches": 0,
        }

    deleted = 0
    batches = 0
    while True:
        if max_batches is not None and batches >= max_batches:
            print(f"[info] reached max_batches={max_batches}, stopping.", flush=True)
            break

        response = dev_client.api.trace.list(limit=batch_size, page=1)
        traces = list(getattr(response, "data", None) or [])
        if not traces:
            break

        trace_ids = [trace.id for trace in traces if getattr(trace, "id", None)]
        if not trace_ids:
            break

        dev_client.api.trace.delete_multiple(trace_ids=trace_ids)
        deleted += len(trace_ids)
        batches += 1

        if sleep_s > 0:
            time.sleep(sleep_s)

        if batches % 50 == 0 or batches == 1:
            remaining = _trace_total(dev_client)
            print(
                f"[progress] batches={batches} deleted={deleted} dev_remaining={remaining}",
                flush=True,
            )
        elif batches % 10 == 0:
            print(f"[progress] batches={batches} deleted={deleted}", flush=True)

    dev_after = _trace_total(dev_client)
    prod_after = _trace_total(prod_client)
    print(f"[info] dev traces after purge: {dev_after}", flush=True)
    print(f"[info] prod traces after purge: {prod_after}", flush=True)

    if prod_after != prod_before:
        raise RuntimeError(
            f"Prod trace count changed ({prod_before} -> {prod_after}). "
            "Investigate immediately; dev purge should not touch prod project."
        )

    dev_client.shutdown()
    prod_client.shutdown()
    return {
        "prod_before": prod_before,
        "prod_after": prod_after,
        "dev_before": dev_before,
        "dev_after": dev_after,
        "deleted": deleted,
        "batches": batches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge Langfuse DEV project traces only.")
    parser.add_argument("--dry-run", action="store_true", help="Count only, do not delete.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to wait after each batch (Langfuse delete is async).",
    )
    args = parser.parse_args()

    try:
        result = purge_dev_traces(
            batch_size=max(1, min(args.batch_size, 100)),
            max_batches=args.max_batches,
            sleep_s=max(0.0, args.sleep),
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"[done] {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
