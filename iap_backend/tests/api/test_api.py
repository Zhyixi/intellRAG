from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from dependency_injector import providers
from PIL import Image

from containers import Container
from fast_api_service import main as fastapi_main
from fast_api_service.api.ae import ae as iap_ae
from fast_api_service.api.common_api import common_api
from fast_api_service.api.pe import pe as iap_pe
from fast_api_service.api.pe import pe_simpson as iap


pytestmark = pytest.mark.api

app = fastapi_main.app
SESSION_ID = "unit-test-session"
ROLE = "10038437"


class FakeMongoService:
    def __init__(self):
        self._repository = SimpleNamespace(client=object())
        self.inserted = []
        self.deleted_queries = []

    def insert_many(self, insert_data, collection_name):
        self.inserted.append((collection_name, insert_data))

    def aggregate_to_df(self, pipeline, collection_name):
        return pd.DataFrame(
            [{"_id": SESSION_ID, "Question": "測試問題", "timestamp": datetime(2026, 1, 1)}]
        )

    def find_list(self, session_id, collection_name):
        return [
            {
                "_id": "mongo-id",
                "session_id": session_id,
                "Question": "測試問題",
                "Response": "測試回答",
                "timestamp": datetime(2026, 1, 1),
            }
        ]

    def find_df(self, query, collection_name, sort=None, limit=None):
        return pd.DataFrame(
            [
                {"Question": "如何處理馬達異常？", "timestamp": datetime(2026, 1, 2)},
                {"Question": "螺絲浮鎖原因？", "timestamp": datetime(2026, 1, 1)},
            ]
        )

    def delete_many(self, query, collection_name):
        self.deleted_queries.append((collection_name, query))
        return SimpleNamespace(deleted_count=2)


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


class FakeDb:
    engine = object()


def fake_agent_state(intent: str) -> dict:
    return {
        "final_response": f"{intent} mocked response",
        "results": [
            {
                "ISSUE_ID": "MOCK-ISSUE",
                "Text": ["mock text"],
                "Score": 0.99,
                "relatedFile": {},
            }
        ],
        "intent": intent,
        "source": ["mock-source"],
        "suggested_questions": ["mock question?"],
    }


async def fake_ae_process_agent_retrieve(user_input, run_config, checkpointer):
    configured_intent = run_config["configurable"].get("intent")
    return fake_agent_state(configured_intent if configured_intent else "ae_faca")


async def fake_pe_process_agent_retrieve(user_input, run_config, checkpointer):
    configured_intent = run_config["configurable"].get("intent")
    intent = "pe_faca_invoke" if configured_intent == "direct_analysis" else "pe_faca"
    return fake_agent_state(intent)


@pytest.fixture
def fake_mongo():
    return FakeMongoService()


@pytest.fixture(autouse=True)
def mocked_api_dependencies(monkeypatch, fake_mongo):
    fake_translator = FakeTranslatorService()
    provider_overrides = []

    for container in (Container, fastapi_main.container):
        provider_overrides.extend(
            [
                (container.mongo_service, providers.Object(fake_mongo)),
                (container.retrive_service, providers.Object(object())),
                (container.table_manager_repository, providers.Object(object())),
                (container.vllm_general_llm, providers.Object(object())),
                (container.langfuse_handler, providers.Object(object())),
                (container.translate_service, providers.Object(fake_translator)),
                (container.translator, providers.Object(fake_translator)),
            ]
        )

    for provider, override in provider_overrides:
        provider.override(override)

    monkeypatch.setattr(iap_ae, "MongoDBSaver", lambda client: object())
    monkeypatch.setattr(iap_pe, "MongoDBSaver", lambda client: object())
    monkeypatch.setattr(iap_ae, "process_agent_retrieve", fake_ae_process_agent_retrieve)
    monkeypatch.setattr(iap_pe, "process_agent_retrieve", fake_pe_process_agent_retrieve)
    app.dependency_overrides[iap_ae.get_db_from_container] = lambda: FakeDb()
    app.dependency_overrides[iap_pe.get_db_from_container] = lambda: FakeDb()

    yield

    app.dependency_overrides.clear()
    for provider, _ in reversed(provider_overrides):
        provider.reset_override()


def assert_agent_response(data: dict, expected_ret_type: str):
    assert data["role"] == "assistant"
    assert data["ret_type"] == expected_ret_type
    assert data["chat_response"]
    assert data["results"]
    assert data["source"] == ["mock-source"]
    assert data["suggested_questions"] == ["mock question?"]


def test_common_check_backend(test_client):
    response = test_client.get("/api/v1/common/check_backend")

    assert response.status_code == 200
    assert response.json() == {"status": "Backend is ready to accept requests", "code": 200}


def test_common_download_pdf_uses_matched_file(test_client, monkeypatch, tmp_path):
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(common_api, "search_files_by_keyword", lambda *args, **kwargs: [str(pdf_path)])

    response = test_client.get("/api/v1/common/download_pdf", params={"file_info": "manual.pdf"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "content-disposition" in response.headers


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


@pytest.mark.parametrize(
    ("url", "payload", "expected_ret_type"),
    [
        ("/api/v1/ae/retrieve", {"role": ROLE, "content": "馬達刹車異常？", "session_id": SESSION_ID}, "ae_faca"),
        (
            "/api/v1/ae/retrieve",
            {"role": ROLE, "content": "馬達刹車異常？", "session_id": SESSION_ID, "syslang": "ZH"},
            "ae_faca",
        ),
        (
            "/api/v1/ae/retrieve",
            {"role": ROLE, "content": "馬達刹車異常？ 1103017020", "session_id": SESSION_ID, "syslang": "ZH"},
            "ae_faca",
        ),
    ],
)
def test_iap_ae_retrieve_versions(test_client, url, payload, expected_ret_type, fake_mongo):
    response = test_client.post(url, json=payload)

    assert response.status_code == 200
    data = response.json()
    assert_agent_response(data, expected_ret_type)
    if url.endswith("/v3/iap_ae/retrieve"):
        assert data["device_ids"] == ["1103017020"]
    assert fake_mongo.inserted


@pytest.mark.parametrize(
    ("url", "payload"),
    [
        ("/api/v1/ae/invoke", {"role": ROLE, "text_input": "直接分析", "session_id": SESSION_ID}),
        (
            "/api/v1/ae/invoke",
            {"role": ROLE, "content": "直接分析", "session_id": SESSION_ID, "syslang": "ZH"},
        ),
    ],
)
def test_iap_ae_invoke_versions(test_client, url, payload):
    response = test_client.post(url, json=payload)

    assert response.status_code == 200
    assert_agent_response(response.json(), "ae_faca_invoke")


def test_iap_ae_history_session_id(test_client):
    response = test_client.get("/api/v1/ae/history_session_id", params={"usr": ROLE})

    assert response.status_code == 200
    assert response.json()[0][0] == SESSION_ID


def test_iap_ae_history_session(test_client):
    response = test_client.get("/api/v1/ae/history_session", params={"session_id": SESSION_ID})

    assert response.status_code == 200
    assert response.json()["result"][0]["session_id"] == SESSION_ID


def test_iap_ae_delete_history_session(test_client, fake_mongo):
    response = test_client.delete("/api/v1/ae/history_session", params={"session_id": SESSION_ID})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "session_id": SESSION_ID, "deleted_count": 2}
    assert fake_mongo.deleted_queries[-1][1] == {"session_id": SESSION_ID}


def test_iap_ae_get_image(test_client, tmp_path):
    image_path = tmp_path / "iap_ae.jpg"
    Image.new("RGB", (2, 2), color="white").save(image_path)

    response = test_client.get("/api/v1/ae/get_image", params={"file_info": str(image_path)})

    assert response.status_code == 200
    assert response.json()["image"]


def test_iap_ae_faqs(test_client):
    response = test_client.get("/api/v1/ae/faqs")

    assert response.status_code == 200
    assert set(response.json()) == {"如何處理馬達異常？", "螺絲浮鎖原因？"}


def test_iap_ae_analyst(test_client, monkeypatch):
    monkeypatch.setattr(
        iap_ae,
        "generate_report_from_db",
        lambda device_ids, db_engine: {"equipment_statistics": [{"DeviceID": device_ids[0]}]},
    )

    response = test_client.post("/api/v1/ae/analyst", json={"DeviceID": ["1103017020"]})

    assert response.status_code == 200
    assert response.json()["equipment_statistics"][0]["DeviceID"] == "1103017020"


def test_iap_ae_analyst_detail(test_client, monkeypatch):
    monkeypatch.setattr(
        iap_ae,
        "build_equipment_issue_list_from_db",
        lambda device_ids, db_engine: {"equipment_issue_list": [{"DeviceID": device_ids[0]}]},
    )

    response = test_client.post("/api/v1/ae/analyst-detail", json={"DeviceID": ["1103017020"]})

    assert response.status_code == 200
    assert response.json()["equipment_issue_list"][0]["DeviceID"] == "1103017020"


@pytest.mark.parametrize(
    ("url", "payload"),
    [
        ("/api/v1/pe/retrieve", {"role": ROLE, "content": "Warlock AMD", "session_id": SESSION_ID}),
        (
            "/api/v1/pe/retrieve",
            {"role": ROLE, "content": "Warlock AMD", "session_id": SESSION_ID, "syslang": "ZH"},
        ),
        (
            "/api/v1/pe/retrieve",
            {"role": ROLE, "content": "Warlock AMD", "session_id": SESSION_ID, "syslang": "ZH"},
        ),
    ],
)
def test_iap_retrieve_versions(test_client, url, payload, fake_mongo):
    response = test_client.post(url, json=payload)

    assert response.status_code == 200
    assert_agent_response(response.json(), "pe_faca")
    assert fake_mongo.inserted


def test_iap_invoke_v1(test_client):
    response = test_client.post(
        "/api/v1/pe/invoke",
        json={"role": ROLE, "text_input": "直接分析", "session_id": SESSION_ID},
    )

    assert response.status_code == 200
    assert_agent_response(response.json(), "pe_faca_invoke")


def test_iap_invoke_v2(test_client):
    response = test_client.post(
        "/api/v1/pe/invoke",
        json={"role": ROLE, "content": "直接分析", "session_id": SESSION_ID, "syslang": "ZH"},
    )

    assert response.status_code == 200
    assert_agent_response(response.json(), "pe_faca_invoke")


def test_iap_history_session_id(test_client):
    response = test_client.get("/api/v1/pe/history_session_id", params={"usr": ROLE})

    assert response.status_code == 200
    assert response.json()[0][0] == SESSION_ID


def test_iap_history_session(test_client):
    response = test_client.get("/api/v1/pe/history_session", params={"session_id": SESSION_ID})

    assert response.status_code == 200
    assert response.json()["result"][0]["session_id"] == SESSION_ID


def test_iap_delete_history_session(test_client, fake_mongo):
    response = test_client.delete("/api/v1/pe/history_session", params={"session_id": SESSION_ID})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "session_id": SESSION_ID, "deleted_count": 2}
    assert fake_mongo.deleted_queries[-1][1] == {"session_id": SESSION_ID}


def test_iap_get_image(test_client, tmp_path):
    image_path = tmp_path / "iap.jpg"
    Image.new("RGB", (2, 2), color="white").save(image_path)

    response = test_client.get("/api/v1/pe/get_image", params={"file_info": str(image_path)})

    assert response.status_code == 200
    assert response.json()["image"]


def test_iap_faqs(test_client):
    response = test_client.get("/api/v1/pe/faqs")

    assert response.status_code == 200
    assert set(response.json()) == {"如何處理馬達異常？", "螺絲浮鎖原因？"}


def test_iap_analyst(test_client, monkeypatch):
    async def fake_fetch_error_code(client, issue_id):
        return "6A07"

    monkeypatch.setattr(iap, "fetch_error_code", fake_fetch_error_code)
    monkeypatch.setattr(iap,
        "get_latest_summary_df",
        lambda engine: pd.DataFrame(
            [
                {
                    "ERROR_CODE": "6A07",
                    "ISSUE_TYPE": "組裝問題",
                    "COUNT": 3,
                    "TOTAL": 5,
                    "PERCENTAGE": 60.0,
                    "ISSUE_ID_LIST": "SFCW1;SFCW2",
                }
            ]
        ),
    )

    response = test_client.post("/api/v1/pe/analyst", json={"IssueID": ["SFCW1"]})

    assert response.status_code == 200
    assert response.json()["errorcode_statistics"][0]["ErrorCode"] == "6A07"


def test_iap_analyst_detail(test_client, monkeypatch):
    monkeypatch.setattr(iap,
        "get_latest_summary_df",
        lambda engine: pd.DataFrame(
            [
                {
                    "ERROR_CODE": "6A07",
                    "ISSUE_TYPE": "組裝問題",
                    "COUNT": 3,
                    "TOTAL": 5,
                    "PERCENTAGE": 60.0,
                    "ISSUE_ID_LIST": "['SFCW1', 'SFCW2']",
                }
            ]
        ),
    )

    response = test_client.post("/api/v1/pe/analyst-detail", json={"ErrorCode": ["6A07"]})

    assert response.status_code == 200
    assert response.json()["equipment_issue_list"][0]["ErrorCode"] == "6A07"
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from dependency_injector import providers
from PIL import Image

from containers import Container
from fast_api_service.api.ae import iap_ae
