#!/usr/bin/env python3
"""Upload IAP LLM prompts to Langfuse."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.extend([".", ".."])

from dotenv import load_dotenv
from langfuse import Langfuse

from scripts.langfuse_prompts import PROMPT_DEFINITIONS

NOTEBOOK_PROMPT_PREFIX = "notebook_"


def _strip(value: str | None) -> str:
    return (value or "").strip().strip('"')


def _resolve_base_url(base_url: str) -> str:
    if "localhost" in base_url or "127.0.0.1" in base_url:
        return "http://iap_langfuse_web:3000"
    return base_url


def _select_prompts(only: list[str] | None) -> dict[str, dict]:
    if not only:
        return PROMPT_DEFINITIONS
    selected: dict[str, dict] = {}
    for name in only:
        if name in PROMPT_DEFINITIONS:
            selected[name] = PROMPT_DEFINITIONS[name]
        else:
            print(f"WARN: unknown prompt name skipped: {name}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Langfuse managed prompts")
    parser.add_argument(
        "--label",
        default="production",
        help="Langfuse prompt label (e.g. production, test)",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        help="Upload only these prompt names",
    )
    parser.add_argument(
        "--notebook-only",
        action="store_true",
        help="Upload only notebook_* prompts",
    )
    args = parser.parse_args()

    load_dotenv("/app/dev.env", override=False)
    load_dotenv("/app/configs/.env.dev", override=False)
    load_dotenv("dev.env", override=False)

    public_key = _strip(os.getenv("LANGFUSE_PUBLIC_KEY"))
    secret_key = _strip(os.getenv("LANGFUSE_SECRET_KEY"))
    base_url = _strip(os.getenv("LANGFUSE_BASE_URL")) or _strip(os.getenv("LANGFUSE_HOST"))

    if not public_key or not secret_key:
        print("ERROR: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
        return 1
    if not base_url:
        print("ERROR: LANGFUSE_BASE_URL not set")
        return 1

    base_url = _resolve_base_url(base_url)

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
        environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", os.getenv("ENV", "dev")),
    )

    if args.notebook_only:
        prompts = {
            k: v for k, v in PROMPT_DEFINITIONS.items() if k.startswith(NOTEBOOK_PROMPT_PREFIX)
        }
    else:
        prompts = _select_prompts(args.only)

    if not prompts:
        print("ERROR: no prompts selected")
        return 1

    created = 0
    for name, spec in prompts.items():
        client.create_prompt(
            name=name,
            prompt=spec["prompt"],
            labels=[args.label],
            config=spec.get("config"),
            commit_message=f"Seed IAP prompts (label={args.label})",
        )
        created += 1
        print(f"✓ uploaded: {name} (label={args.label})")

    client.flush()
    print(f"\nDone. {created} prompts uploaded to {base_url} (label={args.label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
