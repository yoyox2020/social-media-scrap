"""
Celery tasks untuk News — search+scrape artikel via Firecrawl.

Beat schedule (di celery_app.py):
  news-discovery-daily → news_daily_discovery_task

Pipeline MANDIRI (app/services/news/trend_scrape_service.py), TIDAK
tergantung/menyentuh app/ai/llm/viral_discovery_service.py (AI viral
discovery Instagram/Facebook/TikTok/Twitter) sama sekali.
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.news.daily_discovery",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def news_daily_discovery_task(self):
    """
    Task harian: search Firecrawl untuk berita trending, scrape maks
    `settings.news_discovery_daily_budget` artikel BARU, simpan sebagai
    posts (platform='news').
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.news.trend_scrape_service import run_daily_news_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_news_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("news_daily_discovery done: %s", result)
        return result
    except Exception as exc:
        logger.error("news_daily_discovery error: %s", exc)
        raise self.retry(exc=exc)
