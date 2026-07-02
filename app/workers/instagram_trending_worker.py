"""
Celery tasks untuk Instagram trending discovery + scoring + auto-scrape.

Beat schedule (di celery_app.py):
  instagram-trending-daily-09:00 → instagram_trending_daily_task
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
