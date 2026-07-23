"""Ambil balasan komentar utk post TikTok PALING viral (2026-07-23),
jadwal terpisah dari discovery krn lambat (~100 detik/video). Lihat
app/agents/tiktok/reply_enrichment.py."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.tiktok.reply_enrichment import enrich_viral_posts_with_replies
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await enrich_viral_posts_with_replies(db)
        logger.info("[tiktok_reply_enrichment] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="tiktok.enrich_viral_replies")
def enrich_viral_replies_task() -> dict:
    return asyncio.run(_run())
