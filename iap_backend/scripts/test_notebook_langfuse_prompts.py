#!/usr/bin/env python3
"""Validate notebook Langfuse system prompts (compile + optional LLM smoke test)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.extend([".", ".."])

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langfuse import Langfuse

from common.langfuse_tracing import compile_langfuse_prompt_or_fallback, langfuse_configured
from scripts.langfuse_prompts import PROMPT_DEFINITIONS

NOTEBOOK_PROMPTS = sorted(k for k in PROMPT_DEFINITIONS if k.startswith("notebook_"))

SAMPLE_USER = "你好，今天過得怎麼樣？"
SAMPLE_DOC = "文件片段：螺絲浮鎖因底孔尺寸偏差造成鎖附不良。"
SAMPLE_WEB = "搜尋結果：[1] 台北天氣 晴 25°C\n\n用户问题：台北今天天氣如何？"


def _strip(value: str | None) -> str:
    return (value or "").strip().strip('"')


def _build_client() -> Langfuse:
    public_key = _strip(os.getenv("LANGFUSE_PUBLIC_KEY"))
    secret_key = _strip(os.getenv("LANGFUSE_SECRET_KEY"))
    base_url = _strip(os.getenv("LANGFUSE_BASE_URL")) or _strip(os.getenv("LANGFUSE_HOST"))
    if "localhost" in base_url or "127.0.0.1" in base_url:
        base_url = "http://iap_langfuse_web:3000"
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
        environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", os.getenv("ENV", "dev")),
    )


def _sample_human(name: str) -> str:
    if name == "notebook_classify_intent":
        return f"用户输入：{SAMPLE_USER}"
    if name in ("notebook_generate_from_docs", "notebook_evaluate_coverage"):
        return f"{SAMPLE_DOC}\n\n用户问题：螺絲浮鎖怎麼處理？"
    if name == "notebook_generate_from_web":
        return SAMPLE_WEB
    if name == "notebook_suggest_followups":
        return f"用户问题：螺絲浮鎖\n\n助手回答：檢查底孔尺寸並重新攻牙。"
    return SAMPLE_USER


async def _smoke_invoke(llm, name: str, system: str) -> str:
    human = _sample_human(name)
    response = await llm.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=human)]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    return content[:120]


def main() -> int:
    parser = argparse.ArgumentParser(description="Test notebook Langfuse prompts")
    parser.add_argument("--label", default="test", help="Langfuse prompt label to fetch")
    parser.add_argument(
        "--invoke",
        action="store_true",
        help="Run one LLM call per prompt (requires OPENAI_API_KEY)",
    )
    parser.add_argument("--only", nargs="*", help="Test specific prompt names")
    args = parser.parse_args()

    load_dotenv("/app/dev.env", override=False)
    load_dotenv("/app/configs/.env.dev", override=False)
    load_dotenv("dev.env", override=False)

    if not langfuse_configured():
        print("ERROR: Langfuse credentials not configured")
        return 1

    client = _build_client()
    names = args.only or NOTEBOOK_PROMPTS
    failed = 0

    for name in names:
        if name not in PROMPT_DEFINITIONS:
            print(f"✗ unknown prompt: {name}")
            failed += 1
            continue
        fallback = PROMPT_DEFINITIONS[name]["prompt"]
        try:
            compiled = compile_langfuse_prompt_or_fallback(
                client, name, fallback=fallback, label=args.label
            )
            using_fallback = compiled.strip() == fallback.strip()
            source = "fallback" if using_fallback else f"langfuse:{args.label}"
            print(f"✓ compile {name} ({source}, {len(compiled)} chars)")
        except Exception as exc:
            print(f"✗ compile {name}: {exc}")
            failed += 1
            continue

        if args.invoke:
            try:
                from containers import Container

                container = Container()
                llm = container.vllm_general_llm()
                preview = asyncio.run(_smoke_invoke(llm, name, compiled))
                print(f"  → LLM preview: {preview!r}")
            except Exception as exc:
                print(f"  ✗ invoke failed: {exc}")
                failed += 1

    client.flush()
    if failed:
        print(f"\n{failed} prompt(s) failed")
        return 1
    print(f"\nAll {len(names)} notebook prompt(s) OK (label={args.label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
