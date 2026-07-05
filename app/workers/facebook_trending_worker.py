"""
Celery task untuk Facebook trend-recommendation scraping — Subsistem B
khusus Facebook, terpisah dari Instagram (instagram_trending_worker.py).
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.facebook_trend_recommendation.daily",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def facebook_trend_recommendation_daily_task(self):
    """
    Task harian: scrape topik Facebook dari `trend_recommendations`.

    Ambil maks `settings.facebook_trend_daily_budget` topik dengan
    status='pending' (urut score tertinggi) yang punya related_account
    platform facebook, scrape via provider abstraction (Apify). Verifikasi
    hasil sebelum tandai status='used' — kalau gagal/0 post, tetap 'pending'
    untuk dicoba lagi besok.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.facebook.trend_scrape_service import run_daily_trend_scrape_facebook

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_trend_scrape_facebook(db)

    try:
        result = asyncio.run(_run())
        logger.info("facebook_trend_recommendation_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("facebook_trend_recommendation_daily error: %s", exc)
        raise self.retry(exc=exc)
