"""Tests for fast_api_service/response.py — response builder helpers."""
import pytest

pytestmark = pytest.mark.unit


def test_init_response_default_values():
    from fast_api_service.response import initResponse
    resp = initResponse()
    assert resp["responseCode"] == 200
    assert resp["responseMessage"] == "OK"


def test_set_error_response_fields():
    from fast_api_service.response import setErrorResponse
    resp = setErrorResponse(404, "Not Found")
    assert resp["responseCode"] == 404
    assert resp["responseMessage"] == "Not Found"


def test_set_error_response_does_not_include_extra_keys():
    from fast_api_service.response import setErrorResponse
    resp = setErrorResponse(500, "Error")
    assert set(resp.keys()) == {"responseCode", "responseMessage"}


def test_parse_response_json_merges_extra_fields():
    from fast_api_service.response import parseResponseJson
    resp = parseResponseJson({"data": [1, 2, 3], "total": 3})
    assert resp["responseCode"] == 200
    assert resp["responseMessage"] == "OK"
    assert resp["data"] == [1, 2, 3]
    assert resp["total"] == 3


def test_parse_response_json_empty_dict():
    from fast_api_service.response import parseResponseJson
    resp = parseResponseJson({})
    assert resp["responseCode"] == 200
    assert resp["responseMessage"] == "OK"
