"""
Celery tasks untuk Facebook — scraping via Apify.

Beat schedule (di celery_app.py):
  facebook-trend-recommendation-daily → facebook_trend_recommendation_daily_task

On-demand tasks:
  workers.facebook.scrape_identifier — scrape sembarang page/profile (manual)
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


@celery_app.task(
    name="workers.facebook.scrape_identifier",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def facebook_scrape_identifier_task(
    self,
    identifier: str,
    max_posts: int = 5,
    max_comments: int = 5,
):
    """
    Scrape sembarang page/profile Facebook secara async (background), via
    provider abstraction (Apify — lihat app/services/facebook/providers/).
    Manual/on-demand, mirroring instagram_scrape_username_task. Simpan
    posts + comments + hashtag(entities) + lexicon ke DB. Bisa dipanggil
    dari POST /facebook/scrape.
    """
    from datetime import datetime, timezone

    from app.domain.scrape_runs.models import ScrapeRun
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.facebook.pipeline_service import scrape_facebook_posts_via_provider

    clean_identifier = identifier.strip().lstrip("@")

    async def _run():
        async with AsyncSessionLocal() as db:
            started_at = datetime.now(timezone.utc)
            scrape_run = ScrapeRun(
                keyword_text=f"search:{clean_identifier}", platform="facebook", api_source="apify",
                status="running", triggered_by="manual_cli", started_at=started_at,
            )
            db.add(scrape_run)
            await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

            result = await scrape_facebook_posts_via_provider(
                db=db,
                identifier=clean_identifier,
                max_posts=max_posts,
                max_comments=max_comments,
                keyword_id=None,
            )

            scrape_run.status = "success" if result.get("posts_scraped", 0) > 0 else "failed"
            scrape_run.api_source = result.get("provider_used") or "apify"
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
            "facebook_scrape_identifier done: identifier=%s posts_saved=%s errors=%s",
            identifier, result.get("posts_saved"), result.get("errors"),
        )
        return {
            "identifier":    result.get("identifier"),
            "posts_scraped": result.get("posts_scraped"),
            "posts_saved":   result.get("posts_saved"),
            "errors":        result.get("errors", []),
        }
    except Exception as exc:
        logger.error("facebook_scrape_identifier error: identifier=%s exc=%s", identifier, exc)
        raise self.retry(exc=exc)
