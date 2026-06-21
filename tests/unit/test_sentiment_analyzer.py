"""Unit tests untuk SentimentAnalyzer (mock model inference)."""
from unittest.mock import MagicMock, patch

import pytest

from app.services.ai.schemas import SentimentResult


def _make_pipeline_result(label: str, score: float):
    return [{"label": label, "score": score}]


@patch("app.services.ai.sentiment_analyzer.SentimentAnalyzer.__init__", return_value=None)
def test_sentiment_label_mapping(mock_init):
    """Test bahwa LABEL_0/1/2 dimapping ke negative/neutral/positive."""
    from app.services.ai.sentiment_analyzer import SentimentAnalyzer, _LABEL_MAP

    assert _LABEL_MAP["LABEL_0"] == "negative"
    assert _LABEL_MAP["LABEL_1"] == "neutral"
    assert _LABEL_MAP["LABEL_2"] == "positive"


def test_sentiment_result_schema():
    """SentimentResult dataclass bekerja dengan benar."""
    result = SentimentResult(label="positive", score=0.95, model_version="test-model")
    assert result.label == "positive"
    assert result.score == 0.95
    assert result.model_version == "test-model"


@patch("app.services.ai.sentiment_analyzer.SentimentAnalyzer.__init__", return_value=None)
def test_analyze_empty_text(mock_init):
    """Teks kosong harus return neutral dengan score 0."""
    from app.services.ai.sentiment_analyzer import SentimentAnalyzer

    analyzer = SentimentAnalyzer.__new__(SentimentAnalyzer)
    analyzer.model_name = "test"
    analyzer._pipeline = MagicMock()

    result = analyzer.analyze("")
    assert result.label == "neutral"
    assert result.score == 0.0
    analyzer._pipeline.assert_not_called()


@patch("app.services.ai.sentiment_analyzer.SentimentAnalyzer.__init__", return_value=None)
def test_analyze_positive(mock_init):
    """Test analisis teks positif."""
    from app.services.ai.sentiment_analyzer import SentimentAnalyzer

    analyzer = SentimentAnalyzer.__new__(SentimentAnalyzer)
    analyzer.model_name = "test-model"
    analyzer._pipeline = MagicMock(return_value=[{"label": "LABEL_2", "score": 0.92}])

    result = analyzer.analyze("produk ini sangat bagus dan memuaskan!")
    assert result.label == "positive"
    assert result.score == 0.92
    assert result.model_version == "test-model"


@patch("app.services.ai.sentiment_analyzer.SentimentAnalyzer.__init__", return_value=None)
def test_analyze_negative(mock_init):
    """Test analisis teks negatif."""
    from app.services.ai.sentiment_analyzer import SentimentAnalyzer

    analyzer = SentimentAnalyzer.__new__(SentimentAnalyzer)
    analyzer.model_name = "test-model"
    analyzer._pipeline = MagicMock(return_value=[{"label": "LABEL_0", "score": 0.88}])

    result = analyzer.analyze("produk ini sangat mengecewakan dan tidak berkualitas")
    assert result.label == "negative"
    assert result.score == 0.88


@patch("app.services.ai.sentiment_analyzer.SentimentAnalyzer.__init__", return_value=None)
def test_analyze_batch(mock_init):
    """Test batch analysis."""
    from app.services.ai.sentiment_analyzer import SentimentAnalyzer

    analyzer = SentimentAnalyzer.__new__(SentimentAnalyzer)
    analyzer.model_name = "test-model"
    analyzer._pipeline = MagicMock(return_value=[
        {"label": "LABEL_2", "score": 0.9},
        {"label": "LABEL_0", "score": 0.85},
        {"label": "LABEL_1", "score": 0.7},
    ])

    results = analyzer.analyze_batch(["bagus", "buruk", "biasa"])
    assert len(results) == 3
    assert results[0].label == "positive"
    assert results[1].label == "negative"
    assert results[2].label == "neutral"


@patch("app.services.ai.sentiment_analyzer.SentimentAnalyzer.__init__", return_value=None)
def test_analyze_batch_empty(mock_init):
    """Batch kosong return list kosong."""
    from app.services.ai.sentiment_analyzer import SentimentAnalyzer

    analyzer = SentimentAnalyzer.__new__(SentimentAnalyzer)
    analyzer.model_name = "test"
    result = analyzer.analyze_batch([])
    assert result == []
