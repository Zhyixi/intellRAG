import pytest
from dependency_injector import providers

from containers import Container
from fast_api_service import main as fastapi_main
from services.services import TranslatorService

pytestmark = pytest.mark.api

app = fastapi_main.app


class FakeTranslatorService:
    def translate_zh2vi(self, texts):
        return [f"vi:{text}" for text in texts]

    async def translate(self, texts, source_lang, target_lang):
        return [
            {
                "original": text,
                "translated": f"{target_lang}:{text}",
                "src": source_lang,
                "tgt": target_lang,
            }
            for text in texts
        ]


@pytest.fixture(autouse=True)
def mocked_api_dependencies():
    fake_translator = FakeTranslatorService()
    provider_overrides = []

    for container in (Container, fastapi_main.container):
        provider_overrides.extend(
            [
                (container.translate_service, providers.Object(fake_translator)),
                (container.translator, providers.Object(fake_translator)),
            ]
        )

    for provider, override in provider_overrides:
        provider.override(override)

    yield

    for provider, _ in reversed(provider_overrides):
        provider.reset_override()


def test_common_check_backend(test_client):
    response = test_client.get("/api/v1/common/check_backend")

    assert response.status_code == 200
    assert response.json() == {"status": "Backend is ready to accept requests", "code": 200}


def test_common_translate_zh2vi(test_client):
    response = test_client.post("/api/v1/common/translate_zh2vi", json=["測試"])

    assert response.status_code == 200
    assert response.json() == ["vi:測試"]


def test_common_translate(test_client):
    response = test_client.post(
        "/api/v1/common/translate",
        json={"texts": ["測試"], "source_lang": "zh", "target_lang": "en"},
    )

    assert response.status_code == 200
    assert response.json()["results"][0] == {
        "original": "測試",
        "translated": "en:測試",
        "src": "zh",
        "tgt": "en",
    }
