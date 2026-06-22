from fastapi import Depends, HTTPException

from app.domain.users.models import User
from app.infrastructure.redis.connection import get_redis
from app.services.auth.dependencies import get_current_user


class RateLimiter:
    """
    Redis counter rate limiter sebagai FastAPI dependency.

    Usage:
        agent_limiter = RateLimiter(max_requests=10, window_seconds=60)

        @router.post("/ask")
        async def ask(current_user: User = Depends(agent_limiter)):
            ...
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(
        self, current_user: User = Depends(get_current_user)
    ) -> User:
        redis = await get_redis()
        key = f"rl:{current_user.id}:{self.max_requests}:{self.window_seconds}"
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
        return current_user
