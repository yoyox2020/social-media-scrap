"""Task terjadwal backfill transcript YouTube -- SETIAP JAM (permintaan
user). Lihat app/agents/youtube/transcript_backfill.py."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.youtube.transcript_backfill import backfill_transcripts
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await backfill_transcripts(db)
        logger.info("[youtube_transcript_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="youtube.backfill_transcripts")
def backfill_transcripts_task() -> dict:
    return asyncio.run(_run())
