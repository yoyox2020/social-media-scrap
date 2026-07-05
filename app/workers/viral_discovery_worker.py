"""
Celery task: viral discovery harian — Claude (web_search) menyapu berita +
Instagram publik Indonesia untuk topik yang benar-benar viral hari ini, submit
ke trend_recommendations. Terpisah dari instagram_trending_worker.py karena
ini urusan discovery lintas-platform (news + Instagram), bukan scraping
Instagram spesifik.

Beat schedule (di celery_app.py): viral-discovery-daily-07:00
"""
from __future__ import annotations

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.viral_discovery.daily_scan",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def viral_discovery_daily_task(self):
    """
    Task harian: cari topik+akun Instagram yang viral HARI INI via Claude
    (web_search, sapuan terbuka — bukan satu keyword), submit ke
    trend_recommendations (status=pending). Topik yang ditemukan MASUK ANTRIAN
    pipeline scrape Instagram normal (budget harian, jadwal 09:00 WIB
    instagram_trend_recommendation_daily_task) — tidak langsung discrape di sini.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.trend_recommendations.viral_discovery_scrape_service import run_daily_viral_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_viral_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("viral_discovery_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("viral_discovery_daily error: %s", exc)
        raise self.retry(exc=exc)
