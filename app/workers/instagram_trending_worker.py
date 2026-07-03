"""
Celery tasks untuk Instagram — scraping via Apify.

Beat schedule (di celery_app.py):
  instagram-trend-recommendation-daily-09:00 → instagram_trend_recommendation_daily_task

On-demand tasks:
  workers.instagram.scrape_username — scrape sembarang username (manual, tanpa budget cap)
  workers.instagram.ai_keyword_research — cari topik+akun via AI saat keyword search miss di DB
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
    Scrape sembarang username Instagram secara async (background), via Apify.
    Manual/on-demand — tidak kena budget harian trend_recommendations.
    Simpan posts + comments + lexicon ke DB.
    Bisa dipanggil dari POST /instagram/scrape.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.instagram.pipeline_service import scrape_instagram_posts

    async def _run():
        async with AsyncSessionLocal() as db:
            return await scrape_instagram_posts(
                db=db,
                username=username.strip().lstrip("@").lower(),
                max_posts=max_posts,
                max_comments=max_comments,
                keyword_id=None,
            )

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


@celery_app.task(
    name="workers.instagram.ai_keyword_research",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def instagram_ai_keyword_research_task(self, keyword: str):
    """
    Dipicu saat GET /instagram/posts/search tidak menemukan apapun di DB.
    Cari topik+akun Instagram nyata terkait `keyword` via Claude (web_search),
    submit ke trend_recommendations (status='pending') — masuk antrian pipeline
    scrape Instagram normal (budget harian, jadwal 09:00 WIB), TIDAK langsung
    di-scrape sekarang. Lihat docs/setting-tool-calling.md.
    """
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate
    from app.ai.llm.service import find_instagram_topics_for_keyword
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.trend_recommendations.service import submit_recommendations

    async def _run():
        items = await find_instagram_topics_for_keyword(keyword)
        if not items:
            return {"keyword": keyword, "found": 0, "submitted": None}

        async with AsyncSessionLocal() as db:
            body = TrendRecommendationBatchCreate(items=items, source="ai_keyword_search")
            result = await submit_recommendations(db, body)
            return {"keyword": keyword, "found": len(items), "submitted": result}

    try:
        result = asyncio.run(_run())
        logger.info("instagram_ai_keyword_research done: %s", result)
        return result
    except Exception as exc:
        logger.error("instagram_ai_keyword_research error: keyword=%s exc=%s", keyword, exc)
        raise self.retry(exc=exc)
