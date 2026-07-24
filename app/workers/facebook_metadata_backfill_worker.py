"""Task terjadwal utk backfill follower+skor post Facebook (2026-07-24).
Lihat app/agents/facebook/metadata_backfill.py. Jadwal MINGGUAN --
kredit SocialCrawl dibagi dgn TikTok+Instagram (1 akun, 1 pool)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.facebook.metadata_backfill import backfill_facebook_metadata
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_backfill() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await backfill_facebook_metadata(db)
        logger.info("[facebook_metadata_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="facebook.backfill_metadata")
def backfill_facebook_metadata_task() -> dict:
    return asyncio.run(_run_backfill())
