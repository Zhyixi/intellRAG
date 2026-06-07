import pytest

from services.rag_service import RetriveService


pytestmark = pytest.mark.unit


def test_heuristic_query_extraction_detects_error_code_and_entities():
    service = RetriveService.__new__(RetriveService)
    result = service._heuristic_query_extraction(
        "汇川 SV630A E150.3 马达刹车异常",
        jieba_tokens=["汇川", "SV630A", "E150.3", "马达", "刹车", "异常"],
    )

    assert "E150.3" in result.error_codes
    assert result.errorcode == "E150.3"
    assert "异常" in result.symptoms
    assert "SV630A" in result.entities


@pytest.mark.asyncio
async def test_llm_extract_query_falls_back_to_heuristic():
    service = RetriveService.__new__(RetriveService)

    class FakeLLM:
        def bind(self, **kwargs):
            return self

        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("connection failed")

    service.vllm_general_llm = FakeLLM()
    service.ollama_general_llm = None

    result = await service._llm_extract_query(
        "汇川 SV630A E150.3 马达刹车异常",
        jieba_tokens=["汇川", "SV630A", "E150.3", "马达", "刹车", "异常"],
    )

    assert result.errorcode == "E150.3"
    assert "SV630A" in result.entities
