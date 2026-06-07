import pytest


pytestmark = [pytest.mark.api, pytest.mark.integration, pytest.mark.slow, pytest.mark.manual]


def test_common_check_backend_smoke(test_client):
    response = test_client.get("/api/v1/common/check_backend")

    assert response.status_code == 200
    assert response.json()["code"] == 200


@pytest.mark.asyncio
async def test_iap_ae_v3_retrieve_smoke(async_client):
    response = await async_client.post(
        "/api/v1/ae/retrieve",
        json={
            "role": "10038437",
            "content": "解除条件 030.1",
            "session_id": "integration-smoke-iap_ae",
            "syslang": "ZH",
        },
        timeout=300,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "assistant"
    assert data["ret_type"] in {"ae_faca", "ae_sop"}
    assert "results" in data
    assert "chat_response" in data


@pytest.mark.asyncio
async def test_iap_v3_retrieve_smoke(async_client):
    response = await async_client.post(
        "/api/v1/pe/retrieve",
        json={
            "role": "10038437",
            "content": "Warlock AMD",
            "session_id": "integration-smoke-iap",
            "syslang": "ZH",
        },
        timeout=300,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "assistant"
    assert data["ret_type"] == "pe_faca"
    assert "results" in data
    assert "chat_response" in data
