"""Refresh statistik post YouTube lama, tiap 1 jam (2026-07-23,
permintaan user). Lihat app/agents/youtube/refresh.py utk logika +
alasan kenapa ini aman dari sisi kuota (videos.list 1 unit vs
search.list 100 unit)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.youtube.refresh import refresh_stale_youtube_posts
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_refresh() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await refresh_stale_youtube_posts(db)
        logger.info("[youtube_refresh] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="youtube.refresh_stale_posts")
def refresh_stale_youtube_posts_task() -> dict:
    return asyncio.run(_run_refresh())
