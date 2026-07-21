from fastapi import Depends, HTTPException, Request

from app.infrastructure.redis.connection import get_redis


class IPRateLimiter:
    """
    Varian RateLimiter (lihat limiter.py) untuk endpoint TANPA login --
    key dibentuk dari IP client, bukan user.id (yang tidak ada di endpoint
    publik). Dipakai untuk endpoint seperti GET /youtube/trending-public
    yang sengaja no-auth tapi tetap perlu dibatasi supaya tidak disalahgunakan
    (scraping berlebihan/DoS ringan).

    Usage:
        public_limiter = IPRateLimiter(max_requests=30, window_seconds=60)

        @router.get("/trending-public")
        async def trending_public(_: None = Depends(public_limiter)):
            ...
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(self, request: Request) -> None:
        # X-Forwarded-For diprioritaskan (server ini di belakang reverse proxy) --
        # ambil IP PALING KIRI (client asli), fallback ke request.client.host
        # kalau header tidak ada (akses langsung/dev lokal).
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

        redis = await get_redis()
        key = f"rl:ip:{client_ip}:{self.max_requests}:{self.window_seconds}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, self.window_seconds)
        if count > self.max_requests:
            ttl = await redis.ttl(key)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({self.max_requests} req/{self.window_seconds}s). Retry after {ttl}s.",
                headers={"Retry-After": str(ttl)},
            )
