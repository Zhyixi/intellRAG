"""Tests for services/memory_service.py — pure logic (no real Mongo needed)."""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from services.memory_service import MAX_MEMORIES, MemoryService

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(rows: list[dict] | None = None) -> MemoryService:
    """Return a MemoryService whose Mongo returns the given rows."""
    mock_mongo = MagicMock()
    df = pd.DataFrame(rows or [])
    mock_mongo.find_df.return_value = df
    return MemoryService(mock_mongo)


# ---------------------------------------------------------------------------
# list_memories
# ---------------------------------------------------------------------------

def test_list_memories_empty_returns_empty_list():
    svc = _make_service([])
    assert svc.list_memories(user_id=1) == []


def test_list_memories_returns_list_of_dicts():
    rows = [{"user_id": 1, "content": "User prefers dark mode", "timestamp": datetime.datetime.utcnow()}]
    svc = _make_service(rows)
    result = svc.list_memories(user_id=1)
    assert len(result) == 1
    assert result[0]["content"] == "User prefers dark mode"


# ---------------------------------------------------------------------------
# format_for_prompt
# ---------------------------------------------------------------------------

def test_format_for_prompt_empty_memories_returns_empty_string():
    svc = _make_service([])
    assert svc.format_for_prompt(user_id=1) == ""


def test_format_for_prompt_returns_formatted_string():
    rows = [
        {"content": "用户喜欢简洁的回答", "timestamp": datetime.datetime.utcnow()},
        {"content": "项目使用 Python 3.11", "timestamp": datetime.datetime.utcnow()},
    ]
    svc = _make_service(rows)
    result = svc.format_for_prompt(user_id=1)
    assert "用户长期记忆" in result
    assert "用户喜欢简洁的回答" in result
    assert "项目使用 Python 3.11" in result


def test_format_for_prompt_skips_rows_without_content():
    rows = [
        {"content": "有效记忆", "timestamp": datetime.datetime.utcnow()},
        {"content": "", "timestamp": datetime.datetime.utcnow()},
        {"timestamp": datetime.datetime.utcnow()},  # no content key
    ]
    svc = _make_service(rows)
    result = svc.format_for_prompt(user_id=1)
    assert "有效记忆" in result
    # Only one bullet should appear
    assert result.count("- ") == 1


# ---------------------------------------------------------------------------
# maybe_extract_memory — turn_count filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maybe_extract_memory_skips_non_multiple_of_5():
    mock_mongo = MagicMock()
    svc = MemoryService(mock_mongo)
    mock_llm = AsyncMock()

    for turn in [1, 2, 3, 4, 6, 7, 8, 9]:
        await svc.maybe_extract_memory(
            user_id=1, session_id="s1",
            user_input="test", assistant_reply="ok",
            llm=mock_llm, turn_count=turn,
        )

    mock_llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_extract_memory_calls_llm_on_turn_5():
    mock_mongo = MagicMock()
    svc = MemoryService(mock_mongo)

    mock_response = MagicMock()
    mock_response.content = "NONE"
    mock_llm = AsyncMock(return_value=mock_response)

    await svc.maybe_extract_memory(
        user_id=1, session_id="s1",
        user_input="test", assistant_reply="reply",
        llm=mock_llm, turn_count=5,
    )

    mock_llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_extract_memory_none_response_does_not_insert():
    mock_mongo = MagicMock()
    svc = MemoryService(mock_mongo)

    mock_response = MagicMock()
    mock_response.content = "NONE"
    mock_llm = AsyncMock(return_value=mock_response)

    await svc.maybe_extract_memory(
        user_id=1, session_id="s1",
        user_input="hi", assistant_reply="hello",
        llm=mock_llm, turn_count=5,
    )

    mock_mongo.insert_many.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_extract_memory_inserts_extracted_lines():
    mock_mongo = MagicMock()
    svc = MemoryService(mock_mongo)

    mock_response = MagicMock()
    mock_response.content = "用户项目使用 FastAPI\n用户偏好中文回复"
    mock_llm = AsyncMock(return_value=mock_response)

    # _trim_old calls find_df; return empty so no trimming
    mock_mongo.find_df.return_value = pd.DataFrame()

    await svc.maybe_extract_memory(
        user_id=1, session_id="s1",
        user_input="question", assistant_reply="answer",
        llm=mock_llm, turn_count=10,
    )

    assert mock_mongo.insert_many.call_count == 2
    calls_args = [c.kwargs["insert_data"]["content"] for c in mock_mongo.insert_many.call_args_list]
    assert "用户项目使用 FastAPI" in calls_args
    assert "用户偏好中文回复" in calls_args


@pytest.mark.asyncio
async def test_maybe_extract_memory_ignores_llm_errors():
    mock_mongo = MagicMock()
    svc = MemoryService(mock_mongo)
    mock_llm = AsyncMock(side_effect=RuntimeError("connection refused"))

    # Should not raise
    await svc.maybe_extract_memory(
        user_id=1, session_id="s1",
        user_input="hi", assistant_reply="ok",
        llm=mock_llm, turn_count=5,
    )
    mock_mongo.insert_many.assert_not_called()


# ---------------------------------------------------------------------------
# _trim_old
# ---------------------------------------------------------------------------

def test_trim_old_does_nothing_under_limit():
    mock_mongo = MagicMock()
    rows = [{"user_id": 1, "content": f"mem{i}", "timestamp": datetime.datetime.utcnow()}
            for i in range(MAX_MEMORIES - 1)]
    mock_mongo.find_df.return_value = pd.DataFrame(rows)
    svc = MemoryService(mock_mongo)

    svc._trim_old(user_id=1)
    mock_mongo.delete_many.assert_not_called()


def test_trim_old_deletes_when_over_limit():
    mock_mongo = MagicMock()
    base = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    rows = [{"user_id": 1, "content": f"mem{i}",
             "timestamp": base + datetime.timedelta(hours=i)}
            for i in range(MAX_MEMORIES + 5)]
    mock_mongo.find_df.return_value = pd.DataFrame(rows)
    svc = MemoryService(mock_mongo)

    svc._trim_old(user_id=1)
    mock_mongo.delete_many.assert_called_once()
