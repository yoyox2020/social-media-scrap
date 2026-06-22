import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.sentiments.models import Sentiment
from app.domain.users.models import User
from app.infrastructure.cache.redis_cache import cache_get, cache_set
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/trends", tags=["trends"])


@router.get("/keyword/{keyword_id}", response_model=dict)
async def get_keyword_trends(
    keyword_id: uuid.UUID,
    period: str = Query("day", regex="^(day|week|month)$"),
    platform: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Volume post per periode waktu untuk satu keyword. Cache 5 menit."""
    cache_key = f"trends:keyword:{keyword_id}:{period}:{platform or 'all'}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return build_success_response(cached)

    from app.services.agents.schemas import AgentContext
    from app.services.agents.trend_agent import TrendAgent

    agent = TrendAgent(db, period=period)
    context = AgentContext(
        question="trend volume",
        keyword_id=keyword_id,
        platform=platform,
    )
    result = await agent.run(context)
    await cache_set(cache_key, result.data, ex=300)
    return build_success_response(result.data)


@router.get("/sentiment/{keyword_id}", response_model=dict)
async def get_sentiment_trends(
    keyword_id: uuid.UUID,
    period: str = Query("day", regex="^(day|week|month)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tren sentimen (positive/negative/neutral count) per periode. Cache 5 menit."""
    cache_key = f"trends:sentiment:{keyword_id}:{period}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return build_success_response(cached)

    stmt = (
        select(
            func.date_trunc(period, Post.published_at).label("period"),
            Sentiment.label,
            func.count().label("count"),
        )
        .join(Sentiment, Sentiment.post_id == Post.id)
        .where(
            Post.keyword_id == keyword_id,
            Post.published_at.is_not(None),
        )
        .group_by(text("period"), Sentiment.label)
        .order_by(text("period ASC"))
    )
    result = await db.execute(stmt)
    rows = result.all()

    trend: dict[str, dict] = {}
    for row in rows:
        key = str(row.period.date()) if row.period else "unknown"
        if key not in trend:
            trend[key] = {"period": key, "positive": 0, "negative": 0, "neutral": 0}
        trend[key][row.label] = row.count

    data = {
        "keyword_id": str(keyword_id),
        "period": period,
        "trend": list(trend.values()),
    }
    await cache_set(cache_key, data, ex=300)
    return build_success_response(data)


@router.get("/platforms/{keyword_id}", response_model=dict)
async def get_platform_breakdown(
    keyword_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Jumlah post per platform untuk satu keyword."""
    result = await db.execute(
        select(Post.platform, func.count().label("count"))
        .where(Post.keyword_id == keyword_id)
        .group_by(Post.platform)
        .order_by(func.count().desc())
    )
    return build_success_response({
        "keyword_id": str(keyword_id),
        "breakdown": {row.platform: row.count for row in result.all()},
    })
