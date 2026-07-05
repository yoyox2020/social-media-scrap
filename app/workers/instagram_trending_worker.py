"""
Celery tasks untuk Instagram — scraping via Apify.

Beat schedule (di celery_app.py):
  instagram-trend-recommendation-daily-09:00 → instagram_trend_recommendation_daily_task

On-demand tasks:
  workers.instagram.scrape_username — scrape sembarang username (manual, tanpa budget cap)
"""
from __future__ import annotations

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.instagram_trend_recommendation.daily",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def instagram_trend_recommendation_daily_task(self):
    """
    Task harian: scrape topik Instagram dari `trend_recommendations`.

    Ambil maks `settings.instagram_trend_daily_budget` topik dengan
    status='pending' (urut score tertinggi) yang punya related_account
    platform instagram, scrape 1 post + komentar + sentimen per topik via
    Apify. Verifikasi hasil sebelum tandai status='used' — kalau gagal/0 post,
    tetap 'pending' untuk dicoba lagi besok (lihat docs/trend-recommendations.md).
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.instagram_trending.trend_scrape_service import run_daily_trend_scrape

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_trend_scrape(db)

    try:
        result = asyncio.run(_run())
        logger.info("instagram_trend_recommendation_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("instagram_trend_recommendation_daily error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.instagram.scrape_username",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def instagram_scrape_username_task(
    self,
    username: str,
    max_posts: int = 5,
    max_comments: int = 5,
):
    """
    Scrape sembarang username Instagram secara async (background), via provider
    search-and-scrape (Apify + fallback EnsembleData — lihat
    app/services/instagram/providers/). Manual/on-demand — tidak kena budget
    harian trend_recommendations, tapi kena kuota harian bersama
    (app/services/instagram/quota_service.py). Simpan posts + comments +
    lexicon ke DB. Bisa dipanggil dari POST /instagram/scrape.
    """
    from datetime import datetime, timezone

    from app.domain.scrape_runs.models import ScrapeRun
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.instagram.pipeline_service import scrape_instagram_posts
    from app.services.instagram.quota_service import enforce_quota

    clean_username = username.strip().lstrip("@").lower()

    async def _run():
        async with AsyncSessionLocal() as db:
            await enforce_quota(db, operation="search")

            started_at = datetime.now(timezone.utc)
            scrape_run = ScrapeRun(
                keyword_text=f"search:{clean_username}", platform="instagram", api_source="provider_fallback",
                status="running", triggered_by="manual_cli", started_at=started_at,
            )
            db.add(scrape_run)
            await db.commit()  # commit status='running' segera supaya kelihatan di monitor live (bukan cuma flush)

            result = await scrape_instagram_posts(
                db=db,
                username=clean_username,
                max_posts=max_posts,
                max_comments=max_comments,
                keyword_id=None,
            )

            scrape_run.status = "success" if result.get("posts_scraped", 0) > 0 else "failed"
            scrape_run.api_source = result.get("provider_used") or "provider_fallback"
            scrape_run.videos_fetched = result.get("posts_scraped", 0)
            scrape_run.videos_new = result.get("posts_saved", 0)
            scrape_run.error_message = "; ".join(result.get("errors", [])[:3]) or None
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
            await db.commit()
            return result

    try:
        result = asyncio.run(_run())
        logger.info(
            "instagram_scrape_username done: username=%s posts_saved=%s errors=%s",
            username, result.get("posts_saved"), result.get("errors"),
        )
        return {
            "username":     result.get("username"),
            "posts_scraped": result.get("posts_scraped"),
            "posts_saved":  result.get("posts_saved"),
            "errors":       result.get("errors", []),
        }
    except Exception as exc:
        logger.error("instagram_scrape_username error: username=%s exc=%s", username, exc)
        raise self.retry(exc=exc)
