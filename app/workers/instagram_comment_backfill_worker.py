"""Task terjadwal utk backfill komentar Instagram (2026-07-24). Lihat
app/agents/instagram/comment_backfill.py utk logika. Jadwal MINGGUAN
(bukan jam-an) -- resultsLimit dikunci ke 15 (free tier Apify, TIDAK
menambah biaya), tapi tetap dijadwalkan jarang supaya tidak membebani
pool token Apify yg dipakai bersama Facebook/TikTok/Twitter."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.instagram.comment_backfill import backfill_instagram_comments
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_backfill() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await backfill_instagram_comments(db)
        logger.info("[instagram_comment_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="instagram.backfill_comments")
def backfill_instagram_comments_task() -> dict:
    return asyncio.run(_run_backfill())
