"""
Celery task untuk Smart Search — pemindaian berkala harian.

Beat schedule (di celery_app.py):
  search-topic-rescan-daily → search_topics_daily_rescan_task
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
