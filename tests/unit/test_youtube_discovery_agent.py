"""Unit test YouTube Discovery Agent -- fokus ke logic yg bisa diisolasi
tanpa DB/Redis nyata (parsing JSON dari LLM, masking API key, keputusan
'sudah waktunya jalan atau belum' di worker task). Skenario yg butuh
Postgres+Redis sungguhan ada di
tests/integration/test_youtube_discovery_agent_manual.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.youtube_discovery.config import mask_api_key
from app.services.youtube_discovery.openrouter_client import _extract_json


def test_extract_json_plain():
    assert _extract_json('{"valid": true, "reason": "ok"}') == {"valid": True, "reason": "ok"}


def test_extract_json_markdown_fenced():
    text = '```json\n{"valid": false, "reason": "old content"}\n```'
    assert _extract_json(text) == {"valid": False, "reason": "old content"}


def test_extract_json_with_surrounding_prose():
    text = 'Here is my answer:\n{"valid": true, "reason": "fresh"}\nHope that helps.'
    assert _extract_json(text) == {"valid": True, "reason": "fresh"}


def test_extract_json_unparseable_returns_none():
    assert _extract_json("I cannot determine this.") is None


def test_mask_api_key_normal():
    key = "sk-or-v1-abcdefgh1234"
    assert mask_api_key(key) == "*" * (len(key) - 4) + "1234"


def test_mask_api_key_short():
    assert mask_api_key("abc") == "***"


def test_mask_api_key_none():
    assert mask_api_key(None) is None


def test_hourly_check_skips_when_not_due_yet():
    """Interval 4 jam, last_run 1 jam lalu -> HARUS skip, run_discovery_agent
    TIDAK boleh dipanggil sama sekali."""
    from app.workers.youtube_discovery_worker import youtube_discovery_hourly_check_task

    last_run = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with patch("app.services.youtube_discovery.config.is_running", AsyncMock(return_value=False)), \
         patch("app.services.youtube_discovery.config.get_interval_hours", AsyncMock(return_value=4)), \
         patch("app.services.youtube_discovery.config.get_last_run_at", AsyncMock(return_value=last_run)), \
         patch("app.services.youtube_discovery.agent.run_discovery_agent", AsyncMock()) as mock_run:

        result = youtube_discovery_hourly_check_task()

    mock_run.assert_not_awaited()
    assert result.get("skipped") == "not_due_yet"


def test_hourly_check_skips_when_already_running():
    from app.workers.youtube_discovery_worker import youtube_discovery_hourly_check_task

    with patch("app.services.youtube_discovery.config.is_running", AsyncMock(return_value=True)), \
         patch("app.services.youtube_discovery.agent.run_discovery_agent", AsyncMock()) as mock_run:

        result = youtube_discovery_hourly_check_task()

    mock_run.assert_not_awaited()
    assert result.get("skipped") == "already_running"


def test_hourly_check_runs_when_due():
    """last_run 5 jam lalu, interval 4 jam -> HARUS jalan, lock diambil+dilepas."""
    from app.workers.youtube_discovery_worker import youtube_discovery_hourly_check_task

    last_run = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()

    with patch("app.services.youtube_discovery.config.is_running", AsyncMock(return_value=False)), \
         patch("app.services.youtube_discovery.config.get_interval_hours", AsyncMock(return_value=4)), \
         patch("app.services.youtube_discovery.config.get_last_run_at", AsyncMock(return_value=last_run)), \
         patch("app.services.youtube_discovery.config.acquire_running_lock", AsyncMock(return_value=True)) as mock_acquire, \
         patch("app.services.youtube_discovery.config.release_running_lock", AsyncMock()) as mock_release, \
         patch("app.services.youtube_discovery.config.set_last_run_at", AsyncMock()) as mock_set_last, \
         patch("app.infrastructure.database.connection.AsyncSessionLocal"), \
         patch("app.services.youtube_discovery.agent.run_discovery_agent", AsyncMock(return_value={"status": "success"})) as mock_run:

        result = youtube_discovery_hourly_check_task()

    mock_acquire.assert_awaited_once()
    mock_run.assert_awaited_once()
    mock_release.assert_awaited_once()
    mock_set_last.assert_awaited_once()
    assert result == {"status": "success"}


def test_hourly_check_runs_when_never_run_before():
    """last_run_at belum pernah ada (None) -> harus jalan (first-ever run)."""
    from app.workers.youtube_discovery_worker import youtube_discovery_hourly_check_task

    with patch("app.services.youtube_discovery.config.is_running", AsyncMock(return_value=False)), \
         patch("app.services.youtube_discovery.config.get_interval_hours", AsyncMock(return_value=4)), \
         patch("app.services.youtube_discovery.config.get_last_run_at", AsyncMock(return_value=None)), \
         patch("app.services.youtube_discovery.config.acquire_running_lock", AsyncMock(return_value=True)), \
         patch("app.services.youtube_discovery.config.release_running_lock", AsyncMock()), \
         patch("app.services.youtube_discovery.config.set_last_run_at", AsyncMock()), \
         patch("app.infrastructure.database.connection.AsyncSessionLocal"), \
         patch("app.services.youtube_discovery.agent.run_discovery_agent", AsyncMock(return_value={"status": "success"})) as mock_run:

        result = youtube_discovery_hourly_check_task()

    mock_run.assert_awaited_once()
    assert result == {"status": "success"}
