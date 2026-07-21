"""Unit test caching di GET /youtube/search-recent -- ditambahkan 2026-07-17
supaya keyword yang sama dalam 5 menit tidak berulang kali hit YouTube API
(hemat kuota) atau tulis DB berulang. Lihat app/api/v1/youtube/router.py."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.youtube.router import search_recent_youtube


@pytest.mark.asyncio
async def test_cache_hit_skips_search_recent_uploads_entirely():
    """Cache HIT -- search_recent_uploads() (live call + tulis DB) TIDAK BOLEH
    terpanggil sama sekali, cukup kembalikan hasil cache."""
    cached_result = {"status": "ok", "keyword": "berita", "found": 3, "new": 0, "videos": []}

    with patch("app.infrastructure.cache.redis_cache.cache_get", AsyncMock(return_value=cached_result)), \
         patch("app.services.youtube.pipeline_service.search_recent_uploads") as mock_search:
        response = await search_recent_youtube(
            keyword="berita", hours_back=24, max_results=50,
            current_user=MagicMock(), db=AsyncMock(),
        )

    mock_search.assert_not_called()
    assert response["data"] == cached_result


@pytest.mark.asyncio
async def test_cache_miss_calls_search_and_stores_result():
    fresh_result = {"status": "ok", "keyword": "berita", "found": 2, "new": 2, "videos": []}

    with patch("app.infrastructure.cache.redis_cache.cache_get", AsyncMock(return_value=None)), \
         patch("app.infrastructure.cache.redis_cache.cache_set", AsyncMock()) as mock_set, \
         patch("app.services.youtube.pipeline_service.search_recent_uploads", AsyncMock(return_value=fresh_result)) as mock_search:
        response = await search_recent_youtube(
            keyword="berita", hours_back=24, max_results=50,
            current_user=MagicMock(), db=AsyncMock(),
        )

    mock_search.assert_awaited_once()
    mock_set.assert_awaited_once()
    cache_key_used = mock_set.await_args.args[0]
    assert "berita" in cache_key_used and "24" in cache_key_used
    assert mock_set.await_args.kwargs.get("ex") == 300
    assert response["data"] == fresh_result


@pytest.mark.asyncio
async def test_error_result_not_cached():
    """Response error (mis. keyword kosong/API key belum diset) JANGAN
    di-cache -- kalau di-cache, error sesaat bisa nyangkut 5 menit."""
    error_result = {"status": "error", "message": "Keyword tidak boleh kosong"}

    with patch("app.infrastructure.cache.redis_cache.cache_get", AsyncMock(return_value=None)), \
         patch("app.infrastructure.cache.redis_cache.cache_set", AsyncMock()) as mock_set, \
         patch("app.services.youtube.pipeline_service.search_recent_uploads", AsyncMock(return_value=error_result)):
        await search_recent_youtube(
            keyword="berita", hours_back=24, max_results=50,
            current_user=MagicMock(), db=AsyncMock(),
        )

    mock_set.assert_not_called()
