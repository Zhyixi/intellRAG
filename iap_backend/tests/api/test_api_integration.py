import pytest


pytestmark = [pytest.mark.api, pytest.mark.integration, pytest.mark.slow, pytest.mark.manual]


def test_common_check_backend_smoke(test_client):
    response = test_client.get("/api/v1/common/check_backend")

    assert response.status_code == 200
    assert response.json()["code"] == 200
