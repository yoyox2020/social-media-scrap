"""Unit tests untuk Agent schemas."""
import uuid

from app.services.agents.schemas import (
    AgentContext,
    AgentResponse,
    AgentResult,
    AskRequest,
)


def test_ask_request_defaults():
    req = AskRequest(
        question="Apa sentimen terkait produk ini?",
        keyword_id=uuid.uuid4(),
    )
    assert req.platform is None
    assert req.date_from is None
    assert req.date_to is None
    assert req.use_llm_planner is False


def test_agent_result_ok():
    result = AgentResult(
        agent_name="sentiment",
        data={"distribution": {"positive": 10}},
        summary="Sentimen positif mendominasi.",
        sources=[{"post_id": "abc123"}],
    )
    d = result.to_dict()
    assert d["agent"] == "sentiment"
    assert d["summary"] == "Sentimen positif mendominasi."
    assert d["data"]["distribution"]["positive"] == 10
    assert d["error"] is None


def test_agent_result_error():
    result = AgentResult(agent_name="search", error="koneksi gagal")
    d = result.to_dict()
    assert d["error"] == "koneksi gagal"
    assert d["data"] == {}


def test_agent_response_to_dict():
    kid = uuid.uuid4()
    r1 = AgentResult(agent_name="sentiment", summary="positif dominan", data={})
    r2 = AgentResult(agent_name="summary", summary="Jawaban akhir.", data={})
    response = AgentResponse(
        question="Apa sentimen?",
        keyword_id=kid,
        answer="Jawaban akhir.",
        agent_plan=["sentiment", "summary"],
        details={"sentiment": r1, "summary": r2},
        processing_time_ms=123,
    )
    d = response.to_dict()
    assert d["question"] == "Apa sentimen?"
    assert d["keyword_id"] == str(kid)
    assert d["answer"] == "Jawaban akhir."
    assert d["agent_plan"] == ["sentiment", "summary"]
    assert "sentiment" in d["details"]
    assert d["processing_time_ms"] == 123


def test_agent_context_fields():
    kid = uuid.uuid4()
    ctx = AgentContext(question="test", keyword_id=kid, platform="tiktok")
    assert ctx.platform == "tiktok"
    assert ctx.keyword_text == ""
    assert ctx.date_from is None


def test_agent_result_sources_default():
    result = AgentResult(agent_name="entity")
    assert result.sources == []
    assert result.errors if hasattr(result, "errors") else True  # no error attr check
