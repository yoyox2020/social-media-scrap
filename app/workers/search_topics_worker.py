"""
Celery task untuk Smart Search — pemindaian berkala harian + antrian
pencarian yang di-konfirmasi user secara langsung.

Beat schedule (di celery_app.py):
  search-topic-rescan-daily → search_topics_daily_rescan_task

Dipicu on-demand (dari app/api/v1/topic_search.py saat confirm_third_party=true):
  workers.search_topics.process_confirmed_queue → process_confirmed_search_queue_task
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.search_topics.daily_rescan",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def search_topics_daily_rescan_task(self):
    """
    Task harian: scan ulang semua SearchTopic yang `schedule_recurring=True`
    dan masih dalam window `schedule_expires_at`, lihat
    app/services/search_topics/rescan_service.py utk detail logic
    cooldown/cost-control per platform.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.search_topics.rescan_service import run_daily_search_topic_rescan

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_search_topic_rescan(db)

    try:
        result = asyncio.run(_run())
        logger.info("search_topics_daily_rescan done: %s", result)
        return result
    except Exception as exc:
        logger.error("search_topics_daily_rescan error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.search_topics.process_confirmed_queue",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def process_confirmed_search_queue_task(self, items: list[dict], topic_id: str | None = None):
    """
    Dipicu SAAT ITU JUGA (bukan jadwal) saat user confirm_third_party=true
    di POST /search/topics atau POST /search/topics/{id}/search -- proses
    `items` (keyword+platform yang perlu dicari ke third-party) SATU PER
    SATU berurutan, lihat app/services/search_topics/queue_service.py.
    Endpoint pemanggil TIDAK menunggu task ini -- langsung balas "queued"
    supaya request HTTP tidak timeout menunggu Apify/Firecrawl (bisa
    15-60+ detik per panggilan, apalagi kalau item-nya banyak).
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.search_topics.queue_service import run_confirmed_search_queue

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_confirmed_search_queue(db, items, topic_id)

    try:
        result = asyncio.run(_run())
        logger.info("process_confirmed_search_queue done: %s", result)
        return result
    except Exception as exc:
        logger.error("process_confirmed_search_queue error: %s", exc)
        raise self.retry(exc=exc)
