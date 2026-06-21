"""Tests for common/utils.py — pure helper functions."""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# generate_chunk_id
# ---------------------------------------------------------------------------

def test_generate_chunk_id_is_deterministic():
    from common.utils import generate_chunk_id
    assert generate_chunk_id("hello") == generate_chunk_id("hello")


def test_generate_chunk_id_differs_for_different_content():
    from common.utils import generate_chunk_id
    assert generate_chunk_id("aaa") != generate_chunk_id("bbb")


def test_generate_chunk_id_is_hex_string():
    from common.utils import generate_chunk_id
    result = generate_chunk_id("test content")
    assert isinstance(result, str)
    int(result, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# get_url_path
# ---------------------------------------------------------------------------

def test_get_url_path_strips_api_v1_prefix():
    from common.utils import get_url_path
    result = get_url_path("http://localhost:44000/api/v1/common/check_backend")
    assert result == "/common/check_backend"


def test_get_url_path_returns_path_for_non_api_url():
    from common.utils import get_url_path
    result = get_url_path("http://localhost/health")
    assert result == "/health"


def test_get_url_path_handles_deep_api_path():
    from common.utils import get_url_path
    result = get_url_path("http://host/api/v1/notebook/chat/stream")
    assert result == "/notebook/chat/stream"


# ---------------------------------------------------------------------------
# contains_chinese
# ---------------------------------------------------------------------------

def test_contains_chinese_true_for_chinese():
    from common.utils import contains_chinese
    assert contains_chinese("你好世界") is True
    assert contains_chinese("Mixed text 中文 here") is True


def test_contains_chinese_false_for_ascii():
    from common.utils import contains_chinese
    assert contains_chinese("hello world") is False
    assert contains_chinese("123 ABC") is False


def test_contains_chinese_empty_string():
    from common.utils import contains_chinese
    assert contains_chinese("") is False


# ---------------------------------------------------------------------------
# custom_jsonable_encoder
# ---------------------------------------------------------------------------

def test_custom_jsonable_encoder_np_integer():
    from common.utils import custom_jsonable_encoder
    result = custom_jsonable_encoder(np.int64(42))
    assert result == 42
    assert isinstance(result, int)


def test_custom_jsonable_encoder_np_floating():
    from common.utils import custom_jsonable_encoder
    result = custom_jsonable_encoder(np.float64(3.14))
    assert abs(result - 3.14) < 1e-9
    assert isinstance(result, float)


def test_custom_jsonable_encoder_np_ndarray():
    from common.utils import custom_jsonable_encoder
    arr = np.array([1, 2, 3])
    result = custom_jsonable_encoder(arr)
    assert result == [1, 2, 3]


def test_custom_jsonable_encoder_plain_dict():
    from common.utils import custom_jsonable_encoder
    result = custom_jsonable_encoder({"key": "value"})
    assert result == {"key": "value"}
