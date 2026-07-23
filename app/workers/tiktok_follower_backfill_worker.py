"""Task terjadwal utk backfill follower count TikTok via SocialCrawl
(2026-07-24). Lihat app/agents/tiktok/socialcrawl_follower_backfill.py
utk logika + alasan kenapa jadwalnya MINGGUAN (bukan jam-an spt task
lain) -- akun cuma py 100 kredit total, backfill awal (82 author) sudah
makan hampir semua, sisanya dijaga utk author BARU yg muncul pelan2."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.tiktok.socialcrawl_follower_backfill import backfill_tiktok_author_followers
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Batch kecil per-run (BUKAN DEFAULT_LIMIT penuh) -- jadwal mingguan +
# limit 10 author/run = ~10 kredit/minggu, jauh lebih hemat drpd
# backfill awal yg sekali jalan besar (manual, bukan lewat jadwal ini).
WEEKLY_LIMIT = 10


async def _run_backfill() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await backfill_tiktok_author_followers(db, limit=WEEKLY_LIMIT)
        logger.info("[tiktok_follower_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="tiktok.backfill_author_followers")
def backfill_tiktok_author_followers_task() -> dict:
    return asyncio.run(_run_backfill())
