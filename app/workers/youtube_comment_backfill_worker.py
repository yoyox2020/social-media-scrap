"""Task terjadwal utk backfill komentar YouTube yg belum tersimpan
(2026-07-24). Lihat app/agents/youtube/comment_backfill.py utk logika +
alasan kenapa via API (BUKAN ADB/HP -- teks komentar terbukti tidak
bisa diambil dari accessibility tree HP)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.youtube.comment_backfill import backfill_missing_youtube_comments
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

WALL_CLOCK_TIMEOUT_SECONDS = 3300.0


async def _run_backfill() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await asyncio.wait_for(
                backfill_missing_youtube_comments(db), timeout=WALL_CLOCK_TIMEOUT_SECONDS,
            )
        logger.info("[youtube_comment_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="youtube.backfill_missing_comments")
def backfill_missing_youtube_comments_task() -> dict:
    return asyncio.run(_run_backfill())
