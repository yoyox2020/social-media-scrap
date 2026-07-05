"""
Facebook Trend-Recommendation Scrape Service — Subsistem B khusus Facebook.

Mirroring app/services/instagram_trending/trend_scrape_service.py TAPI
TERPISAH TOTAL — tidak memanggil atau mengubah run_daily_trend_scrape()
Instagram yang frozen. Ditambahkan 05 Juli 2026, lihat
docs/flow scrape/flow-scrap-facebook.md.

Alur:
  1. Ambil topik status='pending' yang punya related_account platform
     facebook, urut score tertinggi, maks settings.facebook_trend_daily_budget
     topik.
  2. Per topik: scrape via provider abstraction (Apify, siap auto-switch —
     lihat app/services/facebook/providers/).
  3. Verifikasi: berhasil kalau provider mengembalikan >=1 post. Kalau gagal,
     topik TETAP 'pending' (dicoba lagi besok).
  4. Kalau berhasil: status -> 'used'. Dicatat juga ke `scrape_runs` untuk
     monitoring (platform='facebook', sama tabel dengan Instagram/YouTube).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.trend_recommendations.models import TrendRecommendation
from app.shared.config import settings

logger = logging.getLogger(__name__)


def _facebook_identifier(topic: TrendRecommendation) -> str | None:
    for acc in topic.related_accounts or []:
        if acc.get("platform") == "facebook" and acc.get("username"):
            return acc["username"]
    return None


async def run_daily_trend_scrape_facebook(db: AsyncSession) -> dict:
    """
    Proses batch harian: scrape maks `facebook_trend_daily_budget` topik
    trend_recommendations (status='pending', ada akun facebook), urut score
    tertinggi. Return ringkasan per topik yang diproses.
    """
    from app.services.facebook.pipeline_service import scrape_facebook_posts_via_provider

    budget = settings.facebook_trend_daily_budget
    max_posts = settings.facebook_trend_posts_per_topic
    max_comments = settings.facebook_trend_comments_per_post

    pending_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.status == "pending")
        .order_by(TrendRecommendation.score.desc())
    )).all()

    # Filter yang punya akun facebook, ambil sejumlah budget
    candidates: list[tuple[TrendRecommendation, str]] = []
    for topic in pending_topics:
        identifier = _facebook_identifier(topic)
        if identifier:
            candidates.append((topic, identifier))
        if len(candidates) >= budget:
            break

    if not candidates:
        logger.info("run_daily_trend_scrape_facebook: tidak ada topik pending dengan akun facebook")
        return {"processed": 0, "results": []}

    results = []
    for topic, identifier in candidates:
        started_at = datetime.now(timezone.utc)
        scrape_run = ScrapeRun(
            keyword_text=topic.topic,
            platform="facebook",
            api_source="apify",
            status="running",
            triggered_by="celery_beat",
            started_at=started_at,
        )
        db.add(scrape_run)
        await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

        try:
            result = await scrape_facebook_posts_via_provider(
                db=db,
                identifier=identifier,
                max_posts=max_posts,
                max_comments=max_comments,
                keyword_id=None,
            )
            posts_scraped = result.get("posts_scraped", 0)
            posts_saved = result.get("posts_saved", 0)
            errors = result.get("errors", [])
            verified = posts_scraped >= 1

            scrape_run.status = "success" if verified else "failed"
            scrape_run.api_source = result.get("provider_used") or "apify"
            scrape_run.videos_fetched = posts_scraped
            scrape_run.videos_new = posts_saved
            scrape_run.error_message = "; ".join(errors[:3]) if errors else None
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()

            if verified:
                topic.status = "used"

            results.append({
                "topic":         topic.topic,
                "identifier":    identifier,
                "verified":      verified,
                "posts_scraped": posts_scraped,
                "posts_saved":   posts_saved,
                "errors":        errors,
            })

        except Exception as exc:
            logger.error("run_daily_trend_scrape_facebook topic=%s identifier=%s error=%s", topic.topic, identifier, exc)
            scrape_run.status = "failed"
            scrape_run.error_message = str(exc)[:1000]
            scrape_run.finished_at = datetime.now(timezone.utc)
            results.append({
                "topic":      topic.topic,
                "identifier": identifier,
                "verified":   False,
                "errors":     [str(exc)],
            })

        await db.commit()

    logger.info("run_daily_trend_scrape_facebook: %d topik diproses", len(results))
    return {"processed": len(results), "results": results}
