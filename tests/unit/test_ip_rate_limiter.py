"""Unit test untuk IPRateLimiter -- dipakai endpoint publik (tanpa login)
seperti GET /youtube/trending-public, lihat app/infrastructure/rate_limit/ip_limiter.py."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.infrastructure.rate_limit.ip_limiter import IPRateLimiter


def _make_request(ip: str | None = "203.0.113.5", forwarded_for: str | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = {"x-forwarded-for": forwarded_for} if forwarded_for else {}
    request.client = MagicMock(host=ip) if ip else None
    return request


@pytest.mark.asyncio
async def test_allows_request_under_limit():
    redis = AsyncMock()
    redis.incr.return_value = 1
    with patch("app.infrastructure.rate_limit.ip_limiter.get_redis", AsyncMock(return_value=redis)):
        limiter = IPRateLimiter(max_requests=5, window_seconds=60)
        await limiter(_make_request())  # tidak raise = lulus
    redis.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocks_request_over_limit():
    redis = AsyncMock()
    redis.incr.return_value = 6
    redis.ttl.return_value = 42
    with patch("app.infrastructure.rate_limit.ip_limiter.get_redis", AsyncMock(return_value=redis)):
        limiter = IPRateLimiter(max_requests=5, window_seconds=60)
        with pytest.raises(HTTPException) as exc_info:
            await limiter(_make_request())

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "42"


@pytest.mark.asyncio
async def test_uses_x_forwarded_for_when_present():
    """Server di belakang reverse proxy -- IP asli client ada di X-Forwarded-For,
    bukan request.client.host (itu IP proxy internal)."""
    redis = AsyncMock()
    redis.incr.return_value = 1
    with patch("app.infrastructure.rate_limit.ip_limiter.get_redis", AsyncMock(return_value=redis)):
        limiter = IPRateLimiter(max_requests=5, window_seconds=60)
        await limiter(_make_request(ip="10.0.0.1", forwarded_for="198.51.100.9, 10.0.0.1"))

    key_used = redis.incr.call_args[0][0]
    assert "198.51.100.9" in key_used
    assert "10.0.0.1" not in key_used


@pytest.mark.asyncio
async def test_different_ips_tracked_separately():
    redis = AsyncMock()
    redis.incr.return_value = 1
    with patch("app.infrastructure.rate_limit.ip_limiter.get_redis", AsyncMock(return_value=redis)):
        limiter = IPRateLimiter(max_requests=5, window_seconds=60)
        await limiter(_make_request(ip="203.0.113.5"))
        await limiter(_make_request(ip="203.0.113.6"))

    key1 = redis.incr.call_args_list[0][0][0]
    key2 = redis.incr.call_args_list[1][0][0]
    assert key1 != key2
