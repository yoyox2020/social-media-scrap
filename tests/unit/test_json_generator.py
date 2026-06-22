"""Unit tests untuk JSON report generator."""

import json
import os
import tempfile
import uuid

from app.services.reports.json_generator import JSONReportGenerator
from app.services.reports.schemas import ReportData, SentimentData, TrendData


def _make_data() -> ReportData:
    return ReportData(
        report_id=uuid.uuid4(),
        keyword_id=uuid.uuid4(),
        keyword_text="test_keyword",
        title="Test Report JSON",
        total_posts=50,
        processed_posts=45,
        sentiment=SentimentData(
            distribution={"positive": 30, "negative": 10, "neutral": 5},
            percentages={"positive": 66.7, "negative": 22.2, "neutral": 11.1},
            dominant="positive",
            total_analyzed=45,
        ),
        trend=TrendData(direction="naik", total_posts=50),
    )


def test_json_generator_creates_file():
    gen = JSONReportGenerator()
    data = _make_data()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = gen.generate(data, tmpdir)
        assert os.path.exists(path)
        assert path.endswith(".json")


def test_json_generator_valid_json():
    gen = JSONReportGenerator()
    data = _make_data()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = gen.generate(data, tmpdir)
        with open(path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        assert "meta" in parsed
        assert "sentiment" in parsed
        assert "trend" in parsed


def test_json_generator_correct_values():
    gen = JSONReportGenerator()
    data = _make_data()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = gen.generate(data, tmpdir)
        with open(path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        assert parsed["meta"]["keyword"] == "test_keyword"
        assert parsed["posts"]["total"] == 50
        assert parsed["sentiment"]["dominant"] == "positive"
        assert parsed["trend"]["direction"] == "naik"


def test_json_generator_filename_matches_report_id():
    gen = JSONReportGenerator()
    data = _make_data()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = gen.generate(data, tmpdir)
        filename = os.path.basename(path)
        assert filename == f"{data.report_id}.json"


def test_json_generator_creates_output_dir():
    gen = JSONReportGenerator()
    data = _make_data()
    with tempfile.TemporaryDirectory() as tmpdir:
        nested = os.path.join(tmpdir, "sub", "dir")
        path = gen.generate(data, nested)
        assert os.path.exists(path)
