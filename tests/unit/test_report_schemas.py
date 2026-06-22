"""Unit tests untuk report schemas Phase 6."""

import uuid
from datetime import datetime

from app.services.reports.schemas import (
    EntityData,
    GenerateReportRequest,
    ReportData,
    ReportJobResponse,
    SentimentData,
    TrendData,
)


def test_generate_report_request_defaults():
    req = GenerateReportRequest(
        keyword_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert req.format == "json"
    assert req.period == "day"
    assert req.include_posts_sample is True
    assert req.posts_sample_size == 5


def test_generate_report_request_custom():
    req = GenerateReportRequest(
        keyword_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="Laporan TikTok",
        format="pdf",
        period="week",
    )
    assert req.format == "pdf"
    assert req.period == "week"
    assert req.title == "Laporan TikTok"


def test_report_job_response_fields():
    rid = uuid.uuid4()
    resp = ReportJobResponse(
        report_id=rid,
        job_id="celery-abc-123",
        status="pending",
        format="docx",
    )
    assert resp.report_id == rid
    assert resp.job_id == "celery-abc-123"
    assert resp.format == "docx"


def test_sentiment_data_defaults():
    s = SentimentData()
    assert s.total_analyzed == 0
    assert s.dominant == "neutral"
    assert s.distribution == {}
    assert s.percentages == {}
    assert s.examples == []


def test_entity_data_defaults():
    e = EntityData()
    assert e.total_unique == 0
    assert e.by_type == {}


def test_trend_data_defaults():
    t = TrendData()
    assert t.direction == "stabil"
    assert t.total_posts == 0
    assert t.volume == []
    assert t.sentiment == []
    assert t.platform_breakdown == {}


def test_report_data_to_dict_structure():
    rid = uuid.uuid4()
    kid = uuid.uuid4()
    data = ReportData(
        report_id=rid,
        keyword_id=kid,
        keyword_text="jokowi",
        title="Test Report",
        total_posts=100,
        processed_posts=90,
        near_duplicates=5,
        language_breakdown={"id": 80, "en": 10},
        sentiment=SentimentData(
            distribution={"positive": 60, "negative": 20, "neutral": 10},
            percentages={"positive": 66.7, "negative": 22.2, "neutral": 11.1},
            dominant="positive",
            total_analyzed=90,
        ),
        entities=EntityData(
            by_type={"PERSON": [{"text": "Jokowi", "count": 45}]},
            total_unique=20,
        ),
        trend=TrendData(
            direction="naik",
            total_posts=100,
            platform_breakdown={"tiktok": 60, "youtube": 40},
        ),
    )

    d = data.to_dict()

    assert d["meta"]["keyword"] == "jokowi"
    assert d["meta"]["report_id"] == str(rid)
    assert d["posts"]["total"] == 100
    assert d["posts"]["near_duplicates"] == 5
    assert d["sentiment"]["dominant"] == "positive"
    assert d["sentiment"]["total_analyzed"] == 90
    assert d["entities"]["total_unique"] == 20
    assert "PERSON" in d["entities"]["by_type"]
    assert d["trend"]["direction"] == "naik"
    assert d["trend"]["platform_breakdown"]["tiktok"] == 60


def test_report_data_to_dict_empty():
    data = ReportData()
    d = data.to_dict()
    assert "meta" in d
    assert "posts" in d
    assert "sentiment" in d
    assert "entities" in d
    assert "trend" in d
    assert "top_posts" in d
    assert d["posts"]["total"] == 0
