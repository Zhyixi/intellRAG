#!/usr/bin/env python3
"""Upload IAP LLM prompts to Langfuse (label: production)."""

from __future__ import annotations

import os
import sys

sys.path.extend([".", ".."])

from dotenv import load_dotenv
from langfuse import Langfuse

from scripts.langfuse_prompts import PROMPT_DEFINITIONS


def _strip(value: str | None) -> str:
    return (value or "").strip().strip('"')


def main() -> int:
    load_dotenv("/app/dev.env", override=False)
    load_dotenv("/app/configs/.env.dev", override=False)

    public_key = _strip(os.getenv("LANGFUSE_PUBLIC_KEY"))
    secret_key = _strip(os.getenv("LANGFUSE_SECRET_KEY"))
    base_url = _strip(os.getenv("LANGFUSE_BASE_URL")) or _strip(os.getenv("LANGFUSE_HOST"))

    if not public_key or not secret_key:
        print("ERROR: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
        return 1
    if not base_url:
        print("ERROR: LANGFUSE_BASE_URL not set")
        return 1

    # Inside Docker, localhost may not reach Langfuse; prefer service name when localhost.
    if "localhost" in base_url or "127.0.0.1" in base_url:
        base_url = "http://iap_langfuse_web:3000"

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
        environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", os.getenv("ENV", "dev")),
    )

    created = 0
    for name, spec in PROMPT_DEFINITIONS.items():
        client.create_prompt(
            name=name,
            prompt=spec["prompt"],
            labels=["production"],
            config=spec.get("config"),
            commit_message="Seed initial IAP LLM prompts",
        )
        created += 1
        print(f"✓ uploaded: {name}")

    client.flush()
    print(f"\nDone. {created} prompts uploaded to {base_url} (label=production)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
