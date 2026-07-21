"""Unit test untuk retry di YouTubeDataAPIClient.get_videos_statistics()
-- ditambahkan 2026-07-16 setelah ditemukan ~5.400 post YouTube stuck
views=0 permanen gara-gara kegagalan sesaat tanpa retry, lihat
app/integrations/youtube_data_api/client.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
from app.shared.exceptions import ExternalAPIError


def _mock_response(items: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"items": items}
    return resp


@pytest.mark.asyncio
async def test_get_videos_statistics_succeeds_first_try():
    client = YouTubeDataAPIClient(api_key="fake-key")
    resp = _mock_response([{"id": "abc", "statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "1"}}])

    with patch("app.integrations.youtube_data_api.client._get", AsyncMock(return_value=resp)) as mock_get:
        result = await client.get_videos_statistics(["abc"])

    assert result == {"abc": {"views": 100, "likes": 5, "comments": 1}}
    assert mock_get.await_count == 1


@pytest.mark.asyncio
async def test_get_videos_statistics_retries_then_succeeds():
    """Simulasi rate-limit SESAAT: 2x gagal, percobaan ke-3 berhasil -- HARUS
    tetap dapat data (bukan permanen views=0 seperti sebelum ada retry)."""
    client = YouTubeDataAPIClient(api_key="fake-key")
    resp_ok = _mock_response([{"id": "abc", "statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "1"}}])

    call_count = 0

    async def flaky_get(path, params):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ExternalAPIError(service="YouTubeDataAPI", message="HTTP 429: rate limited")
        return resp_ok

    with patch("app.integrations.youtube_data_api.client._get", flaky_get), \
         patch("asyncio.sleep", AsyncMock()):  # jangan beneran nunggu backoff di test
        result = await client.get_videos_statistics(["abc"])

    assert call_count == 3
    assert result == {"abc": {"views": 100, "likes": 5, "comments": 1}}


@pytest.mark.asyncio
async def test_get_videos_statistics_raises_after_max_retries():
    """Kalau GAGAL TERUS (bukan cuma sesaat), tetap harus raise setelah 3x
    percobaan -- bukan diam-diam kembalikan dict kosong (pemanggil butuh tahu
    ini gagal beneran, lihat penanganan di backfill_youtube_stats_task)."""
    client = YouTubeDataAPIClient(api_key="fake-key")

    always_fail = AsyncMock(side_effect=ExternalAPIError(service="YouTubeDataAPI", message="HTTP 429"))

    with patch("app.integrations.youtube_data_api.client._get", always_fail), \
         patch("tenacity.nap.time.sleep", MagicMock()):
        with pytest.raises(ExternalAPIError):
            await client.get_videos_statistics(["abc"])

    assert always_fail.await_count == 3


@pytest.mark.asyncio
async def test_get_videos_statistics_chunks_by_50():
    """151 video ID harus jadi 4 panggilan (50+50+50+1), bukan 1 panggilan
    raksasa yg melanggar batas resmi YouTube Data API v3."""
    client = YouTubeDataAPIClient(api_key="fake-key")
    video_ids = [f"vid{i}" for i in range(151)]
    resp = _mock_response([])

    with patch("app.integrations.youtube_data_api.client._get", AsyncMock(return_value=resp)) as mock_get:
        await client.get_videos_statistics(video_ids)

    assert mock_get.await_count == 4
    chunk_sizes = [len(call.args[1]["id"].split(",")) for call in mock_get.await_args_list]
    assert chunk_sizes == [50, 50, 50, 1]


# ── search_recent() -- fitur "video paling baru diupload" 2026-07-16 ─────────

@pytest.mark.asyncio
async def test_search_recent_sends_published_after_no_published_before():
    """WAJIB tidak ada publishedBefore -- video yg baru saja diupload (mis.
    1 jam lalu) TIDAK BOLEH ke-exclude, itu justru yg harus pasti ketemu."""
    client = YouTubeDataAPIClient(api_key="fake-key")
    resp = _mock_response([])
    published_after = datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone.utc)

    with patch("app.integrations.youtube_data_api.client._get", AsyncMock(return_value=resp)) as mock_get:
        await client.search_recent("keyword test", published_after=published_after)

    params = mock_get.await_args.args[1]
    assert params["publishedAfter"] == "2026-07-16T10:00:00Z"
    assert "publishedBefore" not in params


@pytest.mark.asyncio
async def test_search_recent_orders_by_date_newest_first():
    """order=date (bukan relevance) -- video terbaru harus di posisi teratas
    supaya 'langsung bisa diketahui' tanpa perlu sort manual di sisi kita."""
    client = YouTubeDataAPIClient(api_key="fake-key")
    resp = _mock_response([])

    with patch("app.integrations.youtube_data_api.client._get", AsyncMock(return_value=resp)) as mock_get:
        await client.search_recent("keyword test", published_after=datetime.now(timezone.utc) - timedelta(hours=24))

    params = mock_get.await_args.args[1]
    assert params["order"] == "date"
    assert params["type"] == "video"


@pytest.mark.asyncio
async def test_search_recent_marks_source_for_normalizer():
    """_source marker WAJIB ada -- ini yg dipakai YouTubeNormalizer/extract_posts
    utk tahu ini format YouTube Data API v3, bukan EnsembleData."""
    client = YouTubeDataAPIClient(api_key="fake-key")
    resp = _mock_response([{"id": {"videoId": "abc"}, "snippet": {"title": "Video Baru"}}])

    with patch("app.integrations.youtube_data_api.client._get", AsyncMock(return_value=resp)):
        result = await client.search_recent("keyword test", published_after=datetime.now(timezone.utc) - timedelta(hours=1))

    assert result["_source"] == "youtube_data_api"
    assert result["data"]["items"][0]["id"]["videoId"] == "abc"
