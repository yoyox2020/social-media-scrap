"""
Instagram Trend-Recommendation Scrape Service.

Pengganti pipeline discovery-by-hashtag lama (lihat git history untuk versi
lama). Sekarang sumber akun trending Instagram datang dari `trend_recommendations`
(diisi AI eksternal via POST /trend-recommendations), bukan discovery internal.

Alur harian (lihat docs/trend-recommendations.md):
  1. Ambil topik status='pending' yang punya related_account platform instagram,
     urut score tertinggi, maks `settings.instagram_trend_daily_budget` topik.
  2. Per topik: scrape 1 post + komentar + sentimen via Apify (satu akun/topik).
  3. Verifikasi: berhasil kalau Apify mengembalikan >=1 post. Kalau gagal,
     topik TETAP 'pending' (dicoba lagi besok, budget hari ini hangus untuk topik itu)
     -- KECUALI sudah gagal >= FAILED_PERMANENT_THRESHOLD (3x, lintas platform,
     lihat app/services/trend_recommendations/service.py), lalu status ->
     'failed_permanent' supaya berhenti menghabiskan budget harian (ditambahkan
     2026-07-09, sebelumnya Instagram tidak punya proteksi ini).
  4. Kalau berhasil: status -> 'used'. Dicatat juga ke `scrape_runs` untuk monitoring.
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


def _instagram_username(topic: TrendRecommendation) -> str | None:
    for acc in topic.related_accounts or []:
        if acc.get("platform") == "instagram" and acc.get("username"):
            return acc["username"]
    return None


async def get_trend_scrape_summary(db: AsyncSession, recent_limit: int = 10) -> dict:
    """
    Ringkasan pipeline scrape Instagram dari `trend_recommendations` — dipakai
    baik oleh endpoint ber-auth `GET /instagram/trend-scrape/status` maupun
    endpoint publik `GET /youtube/monitor-public` (dashboard `/scraping-status`).
    """
    all_topics = (await db.scalars(select(TrendRecommendation))).all()
    ig_topics = [(t, _instagram_username(t)) for t in all_topics if _instagram_username(t)]
    pending = [(t, u) for t, u in ig_topics if t.status == "pending"]
    used = [(t, u) for t, u in ig_topics if t.status == "used"]
    failed_permanent = [(t, u) for t, u in ig_topics if t.status == "failed_permanent"]
    pending_sorted = sorted(pending, key=lambda tu: tu[0].score, reverse=True)

    runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "instagram")
        .order_by(ScrapeRun.started_at.desc())
        .limit(recent_limit)
    )).all()

    from app.services.trend_recommendations.viral_discovery_scrape_service import get_viral_discovery_trace
    viral_discovery_trace = await get_viral_discovery_trace(db)

    now = datetime.now(timezone.utc)
    running_runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "instagram", ScrapeRun.status == "running")
        .order_by(ScrapeRun.started_at.desc())
    )).all()

    return {
        "daily_budget": settings.instagram_trend_daily_budget,
        "schedule": "09:00 WIB otomatis (Celery Beat) — trigger manual: POST /instagram/trend-scrape/run",
        "summary": {
            "pending_with_instagram_account":          len(pending),
            "used_with_instagram_account":             len(used),
            "failed_permanent_with_instagram_account": len(failed_permanent),
            "total_with_instagram_account":            len(ig_topics),
            "ai_keyword_search_pending":      sum(1 for t, _ in pending if t.source == "ai_keyword_search"),
            "ai_viral_discovery_pending":     sum(1 for t, _ in pending if t.source == "ai_viral_discovery"),
        },
        "pending_topics": [
            {
                "topic":               t.topic,
                "score":               t.score,
                "instagram_username":  u,
                "source":              t.source,
                "is_ai_keyword_search": t.source == "ai_keyword_search",
                "recommendation_date": t.recommendation_date.isoformat(),
                "created_at":          t.created_at.isoformat(),
            }
            for t, u in pending_sorted
        ],
        "failed_permanent_topics": [
            {
                "topic": t.topic, "instagram_username": u, "source": t.source,
                "recommendation_date": t.recommendation_date.isoformat(),
            }
            for t, u in failed_permanent
        ],
        "recent_runs": [
            {
                "topic":            r.keyword_text,
                "status":           r.status,
                "triggered_by":     r.triggered_by,
                "api_source":       r.api_source,
                "videos_fetched":   r.videos_fetched,
                "videos_new":       r.videos_new,
                "duration_seconds": round(r.duration_seconds, 2) if r.duration_seconds is not None else None,
                "error_message":    r.error_message,
                "started_at":       r.started_at.isoformat(),
                "finished_at":      r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
        "viral_discovery_trace": viral_discovery_trace,
        "running_now": [
            {
                "topic":           r.keyword_text,
                "triggered_by":    r.triggered_by,
                "api_source":      r.api_source,
                "started_at":      r.started_at.isoformat(),
                "elapsed_seconds": round((now - r.started_at).total_seconds(), 1),
            }
            for r in running_runs
        ],
    }


async def run_daily_trend_scrape(db: AsyncSession) -> dict:
    """
    Proses batch harian: scrape maks `instagram_trend_daily_budget` topik
    trend_recommendations (status='pending', ada akun instagram), urut score
    tertinggi. Return ringkasan per topik yang diproses.
    """
    from app.services.instagram.pipeline_service import scrape_instagram_posts

    budget = settings.instagram_trend_daily_budget
    max_posts = settings.instagram_trend_posts_per_topic
    max_comments = settings.instagram_trend_comments_per_post

    pending_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.status == "pending")
        .order_by(TrendRecommendation.score.desc())
    )).all()

    # Filter yang punya akun instagram, ambil sejumlah budget
    candidates: list[tuple[TrendRecommendation, str]] = []
    for topic in pending_topics:
        username = _instagram_username(topic)
        if username:
            candidates.append((topic, username))
        if len(candidates) >= budget:
            break

    if not candidates:
        logger.info("run_daily_trend_scrape: tidak ada topik pending dengan akun instagram")
        return {"processed": 0, "results": []}

    results = []
    for topic, username in candidates:
        started_at = datetime.now(timezone.utc)
        scrape_run = ScrapeRun(
            keyword_text=topic.topic,
            platform="instagram",
            api_source="apify",
            status="running",
            triggered_by="celery_beat",
            started_at=started_at,
        )
        db.add(scrape_run)
        await db.commit()  # commit status='running' segera supaya kelihatan di monitor live (bukan cuma flush)

        try:
            result = await scrape_instagram_posts(
                db=db,
                username=username,
                max_posts=max_posts,
                max_comments=max_comments,
                keyword_id=None,
            )
            posts_scraped = result.get("posts_scraped", 0)
            posts_saved = result.get("posts_saved", 0)
            errors = result.get("errors", [])
            verified = posts_scraped >= 1

            scrape_run.status = "success" if verified else "failed"
            scrape_run.videos_fetched = posts_scraped
            scrape_run.videos_new = posts_saved
            scrape_run.error_message = "; ".join(errors[:3]) if errors else None
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()

            if verified:
                topic.status = "used"

            results.append({
                "topic":         topic.topic,
                "username":      username,
                "verified":      verified,
                "posts_scraped": posts_scraped,
                "posts_saved":   posts_saved,
                "errors":        errors,
            })

        except Exception as exc:
            logger.error("run_daily_trend_scrape topic=%s username=%s error=%s", topic.topic, username, exc)
            scrape_run.status = "failed"
            scrape_run.error_message = str(exc)[:1000]
            scrape_run.finished_at = datetime.now(timezone.utc)
            results.append({
                "topic":    topic.topic,
                "username": username,
                "verified": False,
                "errors":   [str(exc)],
            })

        await db.commit()

        # Topik masih 'pending' (belum 'used') -> cek apakah sudah kehabisan
        # jatah percobaan, tandai 'failed_permanent' kalau sudah -- sama pola
        # dengan Facebook/TikTok (app/services/trend_recommendations/service.py),
        # dipasang di sini 2026-07-09 (dikonfirmasi eksplisit user, lihat
        # docs/analisa-gap-instagram.md gap B) supaya topik yang akunnya
        # genuinely tidak bisa discrape (contoh nyata: 'coldplay_jakarta',
        # gagal 3+ hari berturut-turut) berhenti menghabiskan budget harian
        # selamanya.
        if topic.status == "pending":
            from app.services.trend_recommendations.service import mark_failed_permanent_if_exhausted

            became_permanent = await mark_failed_permanent_if_exhausted(db, topic)
            if became_permanent:
                await db.commit()
                logger.warning(
                    "run_daily_trend_scrape: topik '%s' ditandai failed_permanent (gagal berulang, username=%s)",
                    topic.topic, username,
                )

    logger.info("run_daily_trend_scrape: %d topik diproses", len(results))
    return {"processed": len(results), "results": results}
