import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def fastapi_app():
    from fast_api_service.main import app

    return app


@pytest.fixture
def test_client(fastapi_app):
    return TestClient(fastapi_app)


@pytest.fixture
async def async_client(fastapi_app):
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def reset_llm_singletons():
    from containers import Container

    Container.ollama_general_llm.reset()
    Container.vllm_general_llm.reset()
    yield
    Container.ollama_general_llm.reset()
    Container.vllm_general_llm.reset()
