"""
Celery tasks untuk Multi-Signal Trend Discovery — lihat
app/services/trends/ untuk metodologi lengkap.

Beat schedule (di celery_app.py):
  twitter-trends-daily   → twitter_trend_discovery_task    (14:00 WIB)
  tiktok-trends-daily    → tiktok_trend_discovery_task      (14:15 WIB)
  instagram-trends-daily → instagram_trend_discovery_task   (14:30 WIB)
  trends-combined-daily  → combined_trend_discovery_task    (15:00 WIB)

Urutan jadwal SENGAJA begini -- TikTok/Instagram sweep pakai topik Twitter
hari ini sbg query pencarian (lihat tiktok/instagram_trend_service.py), dan
triangulasi gabungan butuh ketiganya sudah selesai.

Pipeline MANDIRI (app/services/trends/), TIDAK menyentuh
app/ai/llm/viral_discovery_service.py atau kode platform Twitter/TikTok/
Instagram yang sudah ada sama sekali.
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.trends.twitter_discovery",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def twitter_trend_discovery_task(self):
    """Ambil Trends X native (Indonesia), submit sbg trend_recommendations
    (source='twitter_native_trend')."""
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.trends.twitter_trend_service import run_twitter_trend_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_twitter_trend_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("twitter_trend_discovery done: %s", result)
        return result
    except Exception as exc:
        logger.error("twitter_trend_discovery error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.trends.tiktok_discovery",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def tiktok_trend_discovery_task(self):
    """Search TikTok pakai topik trend Twitter hari ini (fallback sapuan
    generik), submit sbg trend_recommendations (source='tiktok_hashtag_sweep')."""
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.trends.tiktok_trend_service import run_tiktok_trend_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_tiktok_trend_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("tiktok_trend_discovery done: %s", result)
        return result
    except Exception as exc:
        logger.error("tiktok_trend_discovery error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.trends.instagram_discovery",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def instagram_trend_discovery_task(self):
    """Search Instagram pakai topik trend Twitter hari ini (fallback sapuan
    generik), submit sbg trend_recommendations (source='instagram_hashtag_sweep')."""
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.trends.instagram_trend_service import run_instagram_trend_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_instagram_trend_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("instagram_trend_discovery done: %s", result)
        return result
    except Exception as exc:
        logger.error("instagram_trend_discovery error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.trends.combined_discovery",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def combined_trend_discovery_task(self):
    """Triangulasi lintas sumber (Twitter+TikTok+Instagram+Google Trends+
    YouTube TrendingTopic baca-saja) -> confidence_score, lihat
    app/services/trends/combined_trend_service.py."""
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.trends.combined_trend_service import run_combined_trend_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_combined_trend_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("combined_trend_discovery done: %s", result)
        return result
    except Exception as exc:
        logger.error("combined_trend_discovery error: %s", exc)
        raise self.retry(exc=exc)
