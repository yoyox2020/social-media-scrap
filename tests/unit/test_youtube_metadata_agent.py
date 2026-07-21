"""Unit test Metadata Agent -- fokus ke logic murni tanpa DB/Redis nyata
(parsing durasi ISO8601, masking API key). Skenario lengkap (fetch+simpan)
ada di tests/integration/test_youtube_metadata_agent_manual.py."""
from app.services.youtube_metadata.agent import _parse_duration_seconds
from app.services.youtube_metadata.config import mask_api_key


def test_parse_duration_minutes_seconds():
    assert _parse_duration_seconds("PT10M30S") == 630


def test_parse_duration_hours_minutes_seconds():
    assert _parse_duration_seconds("PT1H2M10S") == 3730


def test_parse_duration_seconds_only():
    assert _parse_duration_seconds("PT45S") == 45


def test_parse_duration_hours_only():
    assert _parse_duration_seconds("PT2H") == 7200


def test_parse_duration_none():
    assert _parse_duration_seconds(None) is None


def test_parse_duration_empty_string():
    assert _parse_duration_seconds("") is None


def test_mask_api_key():
    key = "sk-or-v1-abcdefgh1234"
    assert mask_api_key(key) == "*" * (len(key) - 4) + "1234"


def test_mask_api_key_none():
    assert mask_api_key(None) is None
