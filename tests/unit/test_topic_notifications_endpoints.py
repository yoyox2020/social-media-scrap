"""Unit test endpoint notifikasi (app/api/v1/topic_search.py) -- mock DB,
fokus ke validasi request/response, bukan query DB (itu sudah dites real-DB
di tests/integration/test_topic_notifications_manual.py)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.topic_search import (
    LookbackDaysUpdateRequest,
    ThresholdUpdateRequest,
    get_notification_lookback_days,
    get_notification_thresholds,
    mark_notification_read,
    unread_notification_count,
    update_notification_lookback_days,
    update_notification_threshold,
)
from app.shared.exceptions import NotFoundError, ValidationError


@pytest.mark.asyncio
async def test_unread_count_filters_is_read_false():
    db = AsyncMock()
    db.scalar.return_value = 7

    result = await unread_notification_count(topic_id=None, current_user=MagicMock(), db=db)

    assert result["data"]["unread_count"] == 7


@pytest.mark.asyncio
async def test_mark_notification_read_success():
    db = AsyncMock()
    notif = MagicMock(id=uuid.uuid4(), is_read=False)
    db.get.return_value = notif

    result = await mark_notification_read(notif.id, current_user=MagicMock(), db=db)

    assert notif.is_read is True
    db.commit.assert_awaited_once()
    assert result["data"]["is_read"] is True


@pytest.mark.asyncio
async def test_mark_notification_read_not_found_raises():
    db = AsyncMock()
    db.get.return_value = None

    with pytest.raises(NotFoundError):
        await mark_notification_read(uuid.uuid4(), current_user=MagicMock(), db=db)


@pytest.mark.asyncio
async def test_get_thresholds_returns_all_platforms():
    fake_thresholds = {
        "youtube": {"metric": "views", "value": 1_000_000},
        "tiktok": {"metric": "views", "value": 500_000},
        "twitter": {"metric": "likes", "value": 10_000},
        "facebook": {"metric": "likes", "value": 5_000},
        "instagram": {"metric": "likes", "value": 5_000},
    }
    with patch(
        "app.services.search_topics.notification_service.get_all_thresholds",
        AsyncMock(return_value=fake_thresholds),
    ):
        result = await get_notification_thresholds(current_user=MagicMock())

    assert result["data"]["thresholds"] == fake_thresholds


@pytest.mark.asyncio
async def test_update_threshold_success():
    with patch(
        "app.services.search_topics.notification_service.set_threshold",
        AsyncMock(return_value={"metric": "likes", "value": 20_000}),
    ) as mock_set:
        result = await update_notification_threshold(
            ThresholdUpdateRequest(platform="twitter", metric="likes", value=20_000),
            current_user=MagicMock(),
        )

    mock_set.assert_awaited_once_with("twitter", "likes", 20_000)
    assert result["data"]["platform"] == "twitter"
    assert result["data"]["value"] == 20_000


@pytest.mark.asyncio
async def test_update_threshold_invalid_platform_raises_validation_error():
    """ValueError dari service HARUS diterjemahkan ke ValidationError (HTTP 400
    lewat error handler global), bukan bocor jadi 500 internal error."""
    with patch(
        "app.services.search_topics.notification_service.set_threshold",
        AsyncMock(side_effect=ValueError("Platform 'news' tidak didukung")),
    ):
        with pytest.raises(ValidationError):
            await update_notification_threshold(
                ThresholdUpdateRequest(platform="news", metric="views", value=100),
                current_user=MagicMock(),
            )


@pytest.mark.asyncio
async def test_get_lookback_days_returns_current_value():
    with patch(
        "app.services.search_topics.notification_service.get_lookback_days",
        AsyncMock(return_value=30),
    ):
        result = await get_notification_lookback_days(current_user=MagicMock())

    assert result["data"]["lookback_days"] == 30


@pytest.mark.asyncio
async def test_update_lookback_days_success():
    with patch(
        "app.services.search_topics.notification_service.set_lookback_days",
        AsyncMock(return_value=7),
    ) as mock_set:
        result = await update_notification_lookback_days(
            LookbackDaysUpdateRequest(days=7),
            current_user=MagicMock(),
        )

    mock_set.assert_awaited_once_with(7)
    assert result["data"]["lookback_days"] == 7


@pytest.mark.asyncio
async def test_update_lookback_days_invalid_raises_validation_error():
    """ValueError dari service (mis. days <= 0) HARUS jadi ValidationError (HTTP 400),
    bukan bocor jadi 500 internal error -- sama seperti update_threshold."""
    with patch(
        "app.services.search_topics.notification_service.set_lookback_days",
        AsyncMock(side_effect=ValueError("days harus > 0")),
    ):
        with pytest.raises(ValidationError):
            await update_notification_lookback_days(
                LookbackDaysUpdateRequest(days=1),
                current_user=MagicMock(),
            )
