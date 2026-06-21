"""Unit tests untuk AI service schemas."""
import uuid

from app.services.ai.schemas import (
    AIAnalysisResult,
    AnalyzeRequest,
    EntityResult,
    KeywordAnalysisStats,
    SentimentResult,
)


def test_ai_analysis_result_to_dict():
    pid = uuid.uuid4()
    result = AIAnalysisResult(
        post_id=pid,
        sentiment=SentimentResult(label="positive", score=0.9, model_version="indobert"),
        entities=[EntityResult(text="Jakarta", entity_type="LOCATION", start_char=0, end_char=7, score=0.95)],
        embedding_updated=True,
        errors=[],
    )
    d = result.to_dict()
    assert d["post_id"] == str(pid)
    assert d["sentiment"]["label"] == "positive"
    assert d["entities_count"] == 1
    assert d["embedding_updated"] is True
    assert d["errors"] == []


def test_ai_analysis_result_no_sentiment():
    pid = uuid.uuid4()
    result = AIAnalysisResult(post_id=pid)
    d = result.to_dict()
    assert d["sentiment"] is None
    assert d["entities_count"] == 0
    assert d["embedding_updated"] is False


def test_keyword_analysis_stats_to_dict():
    kid = uuid.uuid4()
    stats = KeywordAnalysisStats(
        keyword_id=kid,
        total_posts=10,
        analyzed=9,
        sentiment_positive=5,
        sentiment_negative=3,
        sentiment_neutral=1,
        entities_extracted=25,
        embeddings_generated=9,
        errors=["post xyz: error"],
    )
    d = stats.to_dict()
    assert d["keyword_id"] == str(kid)
    assert d["total_posts"] == 10
    assert d["sentiment_distribution"]["positive"] == 5
    assert d["entities_extracted"] == 25
    assert len(d["errors"]) == 1


def test_analyze_request_defaults():
    req = AnalyzeRequest(keyword_id=uuid.uuid4())
    assert req.force_reanalyze is False
    assert req.run_sentiment is True
    assert req.run_ner is True
    assert req.run_embedding is True


def test_entity_result_fields():
    e = EntityResult(
        text="Joko Widodo",
        entity_type="PERSON",
        start_char=0,
        end_char=11,
        score=0.98,
    )
    assert e.text == "Joko Widodo"
    assert e.entity_type == "PERSON"
    assert e.score == 0.98
