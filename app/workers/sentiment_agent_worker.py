"""Task terjadwal Sentiment Agent (LLM tiebreaker) -- lihat
app/services/sentiment/agent.py utk logika. Lexicon SENDIRI TIDAK
butuh worker (jalan inline, instan, gratis, saat komentar disimpan) --
worker ini KHUSUS lapis LLM (backlog komentar yg lexicon-nya belum
direview). Jadwal tiap 30 menit (pola sama dgn interval default kode
lama), dgn Redis lock (`sentiment_agent:running_lock`) mencegah 2 run
tumpang tindih kalau backlog gede & 1 run belum selesai pas jadwal
berikutnya jatuh tempo."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.sentiment import config as cfg
from app.services.sentiment.agent import run_sentiment_agent
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run() -> dict:
    if await cfg.is_running():
        logger.info("[sentiment_agent] run sebelumnya masih jalan, skip jadwal ini")
        return {"status": "skipped", "reason": "already_running"}

    acquired = await cfg.acquire_running_lock()
    if not acquired:
        return {"status": "skipped", "reason": "lock_race"}

    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await run_sentiment_agent(db)
        logger.info("[sentiment_agent] %s", result)
        return result
    finally:
        await cfg.release_running_lock()
        await engine.dispose()


@celery_app.task(name="sentiment.run_agent")
def run_sentiment_agent_task() -> dict:
    return asyncio.run(_run())
