"""
Celery tasks untuk Instagram trending discovery + scoring + auto-scrape.

Beat schedule (di celery_app.py):
  instagram-trending-daily-09:00 → instagram_trending_daily_task

On-demand tasks:
  workers.instagram_trending.scrape_account — scrape 1 akun trending (by UUID)
  workers.instagram.scrape_username         — scrape sembarang username (generic)
"""
from __future__ import annotations

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.instagram_trending.daily",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def instagram_trending_daily_task(self, provider: str = "ensembledata"):
    """
    Task harian: discover → score → scrape top 5 Instagram trending accounts.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.instagram_trending.service import run_daily_trending

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await run_daily_trending(db, provider_name=provider)
            return result

    try:
        result = asyncio.run(_run())
        logger.info("instagram_trending_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("instagram_trending_daily error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(name="workers.instagram_trending.scrape_account")
def instagram_trending_scrape_account_task(account_id: str):
    """
    Scrape satu akun trending secara on-demand (by UUID).
    """
    import uuid
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.domain.instagram_trending.models import InstagramTrendingAccount
    from app.services.instagram_trending.service import run_scrape_account
    from sqlalchemy import select

    async def _run():
        async with AsyncSessionLocal() as db:
            account = await db.scalar(
                select(InstagramTrendingAccount).where(
                    InstagramTrendingAccount.id == uuid.UUID(account_id)
                )
            )
            if not account:
                return {"error": f"account {account_id} tidak ditemukan"}
            return await run_scrape_account(db, account)

    return asyncio.run(_run())


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
    Scrape sembarang username Instagram secara async (background).
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
