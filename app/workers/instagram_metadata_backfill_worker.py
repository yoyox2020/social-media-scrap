"""Task terjadwal utk backfill follower+skor post Instagram (2026-07-24).
Lihat app/agents/instagram/metadata_backfill.py utk logika. Jadwal
MINGGUAN (bukan jam-an) -- kredit SocialCrawl DIBAGI dgn TikTok follower
backfill (1 akun, 1 pool kredit bersama), limit kecil per-run spy tidak
saling menghabiskan."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.instagram.metadata_backfill import backfill_instagram_metadata
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_backfill() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await backfill_instagram_metadata(db)
        logger.info("[instagram_metadata_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="instagram.backfill_metadata")
def backfill_instagram_metadata_task() -> dict:
    return asyncio.run(_run_backfill())
