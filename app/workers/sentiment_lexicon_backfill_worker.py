"""Task terjadwal backfill lexicon utk komentar lama -- lihat
app/services/sentiment/backfill.py. Jadwal SETIAP JAM dgn limit besar
(lexicon lokal/gratis/instan, beda dari backfill komentar platform lain
yg py batasan kuota) -- backlog besar (450rb+) akan habis dlm beberapa
run pertama, sesudahnya jadi jaring pengaman rutin (kalau ada komentar
yg somehow lolos wiring inline)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.sentiment.backfill import backfill_lexicon_analysis
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH_LIMIT = 50000


async def _run() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await backfill_lexicon_analysis(db, limit=BATCH_LIMIT)
        logger.info("[sentiment_lexicon_backfill] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="sentiment.backfill_lexicon")
def backfill_lexicon_task() -> dict:
    return asyncio.run(_run())
