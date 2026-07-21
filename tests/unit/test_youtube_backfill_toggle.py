"""Unit test untuk POST /youtube/backfill-stats/toggle -- lihat
app/api/v1/youtube/router.py toggle_youtube_backfill()."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.youtube.router import BackfillToggleRequest, toggle_youtube_backfill


def _mock_db(already_running_count: int = 0):
    db = AsyncMock()
    db.scalar.return_value = already_running_count
    return db


@pytest.mark.asyncio
async def test_toggle_on_sets_redis_flag_and_triggers_task_when_idle():
    db = _mock_db(already_running_count=0)
    redis = AsyncMock()

    with patch("app.infrastructure.redis.connection.get_redis", AsyncMock(return_value=redis)), \
         patch("app.workers.youtube_worker.backfill_youtube_stats_task") as mock_task:
        result = await toggle_youtube_backfill(
            BackfillToggleRequest(enabled=True), current_user=MagicMock(), db=db,
        )

    redis.set.assert_awaited_once_with("youtube:backfill:enabled", "true")
    mock_task.delay.assert_called_once()
    assert result["data"]["enabled"] is True
    assert result["data"]["task_triggered"] is True


@pytest.mark.asyncio
async def test_toggle_on_does_not_double_trigger_when_already_running():
    """Kalau sudah ada run yg status='running', toggle ON tidak boleh
    trigger task KEDUA -- cegah 2 backfill jalan bersamaan rebutan batch yg sama."""
    db = _mock_db(already_running_count=1)
    redis = AsyncMock()

    with patch("app.infrastructure.redis.connection.get_redis", AsyncMock(return_value=redis)), \
         patch("app.workers.youtube_worker.backfill_youtube_stats_task") as mock_task:
        result = await toggle_youtube_backfill(
            BackfillToggleRequest(enabled=True), current_user=MagicMock(), db=db,
        )

    mock_task.delay.assert_not_called()
    assert result["data"]["task_triggered"] is False


@pytest.mark.asyncio
async def test_toggle_off_only_clears_flag_never_triggers():
    db = _mock_db(already_running_count=1)
    redis = AsyncMock()

    with patch("app.infrastructure.redis.connection.get_redis", AsyncMock(return_value=redis)), \
         patch("app.workers.youtube_worker.backfill_youtube_stats_task") as mock_task:
        result = await toggle_youtube_backfill(
            BackfillToggleRequest(enabled=False), current_user=MagicMock(), db=db,
        )

    redis.set.assert_awaited_once_with("youtube:backfill:enabled", "false")
    mock_task.delay.assert_not_called()
    assert result["data"]["enabled"] is False
