"""
Twitter/X Trend-Recommendation Scrape Service — Subsistem B khusus Twitter.

Mirroring app/services/facebook/trend_scrape_service.py TAPI TERPISAH TOTAL
(tidak memanggil/mengubah apa pun punya Facebook/Instagram/TikTok yang frozen
atau sudah selesai).

Alur:
  1. Ambil topik status='pending' yang punya related_account platform
     twitter, urut score tertinggi, maks settings.twitter_trend_daily_budget
     topik.
  2. Per topik: scrape via provider abstraction (Apify — lihat
     app/integrations/apify/twitter.py).
  3. Verifikasi: berhasil kalau provider mengembalikan >=1 post. Kalau gagal,
     topik TETAP 'pending' (dicoba lagi besok), atau 'failed_permanent' kalau
     sudah gagal 3x (lihat app/services/trend_recommendations/service.py).
  4. Kalau berhasil: status -> 'used'. Dicatat juga ke `scrape_runs`.
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

DISCOVER_DEFAULT_SCORE = 0.9  # sama seperti Facebook/TikTok, lihat komentar di sana


def _twitter_identifier(topic: TrendRecommendation) -> str | None:
    for acc in topic.related_accounts or []:
        if acc.get("platform") == "twitter" and acc.get("username"):
            return acc["username"]
    return None


async def run_daily_trend_scrape_twitter(db: AsyncSession) -> dict:
    """
    Proses batch harian: scrape maks `twitter_trend_daily_budget` topik
    trend_recommendations (status='pending', ada akun twitter), urut score
    tertinggi. Return ringkasan per topik yang diproses.
    """
    from app.services.twitter.pipeline_service import scrape_twitter_posts_via_provider

    budget = settings.twitter_trend_daily_budget
    max_posts = settings.twitter_trend_posts_per_topic
    max_comments = settings.twitter_trend_comments_per_post

    pending_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.status == "pending")
        .order_by(TrendRecommendation.score.desc())
    )).all()

    candidates: list[tuple[TrendRecommendation, str]] = []
    for topic in pending_topics:
        identifier = _twitter_identifier(topic)
        if identifier:
            candidates.append((topic, identifier))
        if len(candidates) >= budget:
            break

    if not candidates:
        logger.info("run_daily_trend_scrape_twitter: tidak ada topik pending dengan akun twitter")
        return {"processed": 0, "results": []}

    results = []
    for topic, identifier in candidates:
        started_at = datetime.now(timezone.utc)
        scrape_run = ScrapeRun(
            keyword_text=topic.topic, platform="twitter", api_source="apify",
            status="running", triggered_by="celery_beat", started_at=started_at,
        )
        db.add(scrape_run)
        await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

        try:
            result = await scrape_twitter_posts_via_provider(
                db=db, identifier=identifier, max_posts=max_posts,
                max_comments=max_comments, keyword_id=None,
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
                "topic": topic.topic, "identifier": identifier, "verified": verified,
                "posts_scraped": posts_scraped, "posts_saved": posts_saved, "errors": errors,
            })
        except Exception as exc:
            logger.error("run_daily_trend_scrape_twitter topic=%s identifier=%s error=%s", topic.topic, identifier, exc)
            scrape_run.status = "failed"
            scrape_run.error_message = str(exc)[:1000]
            scrape_run.finished_at = datetime.now(timezone.utc)
            results.append({"topic": topic.topic, "identifier": identifier, "verified": False, "errors": [str(exc)]})

        await db.commit()

        # Topik masih 'pending' (belum 'used') -> cek apakah sudah kehabisan
        # jatah percobaan, tandai 'failed_permanent' kalau sudah (lihat
        # app/services/trend_recommendations/service.py).
        if topic.status == "pending":
            from app.services.trend_recommendations.service import mark_failed_permanent_if_exhausted

            became_permanent = await mark_failed_permanent_if_exhausted(db, topic)
            if became_permanent:
                await db.commit()
                logger.warning(
                    "run_daily_trend_scrape_twitter: topik '%s' ditandai failed_permanent (gagal berulang, identifier=%s)",
                    topic.topic, identifier,
                )

    logger.info("run_daily_trend_scrape_twitter: %d topik diproses", len(results))
    return {"processed": len(results), "results": results}


async def get_twitter_trend_scrape_summary(db: AsyncSession, recent_limit: int = 10) -> dict:
    """Ringkasan pipeline scrape Twitter dari `trend_recommendations` — mirroring
    get_facebook_trend_scrape_summary()/get_tiktok_trend_scrape_summary(),
    dipakai GET /twitter/trend-scrape/status + dashboard /scraping-status."""
    all_topics = (await db.scalars(select(TrendRecommendation))).all()
    tw_topics = [(t, _twitter_identifier(t)) for t in all_topics if _twitter_identifier(t)]
    pending = [(t, u) for t, u in tw_topics if t.status == "pending"]
    used = [(t, u) for t, u in tw_topics if t.status == "used"]
    failed_permanent = [(t, u) for t, u in tw_topics if t.status == "failed_permanent"]
    pending_sorted = sorted(pending, key=lambda tu: tu[0].score, reverse=True)

    runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "twitter")
        .order_by(ScrapeRun.started_at.desc())
        .limit(recent_limit)
    )).all()

    now = datetime.now(timezone.utc)
    running_runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "twitter", ScrapeRun.status == "running")
        .order_by(ScrapeRun.started_at.desc())
    )).all()

    # SAMA PERSIS dgn get_trend_scrape_summary() Instagram -- lihat catatan
    # di get_facebook_trend_scrape_summary().
    from app.services.trend_recommendations.viral_discovery_scrape_service import get_viral_discovery_trace
    viral_discovery_trace = await get_viral_discovery_trace(db)

    return {
        "daily_budget": settings.twitter_trend_daily_budget,
        "viral_discovery_trace": viral_discovery_trace,
        "schedule": (
            f"{settings.twitter_trend_scrape_schedule_hour:02d}:"
            f"{settings.twitter_trend_scrape_schedule_minute:02d} WIB otomatis (Celery Beat)"
        ),
        "summary": {
            "pending_with_twitter_account":          len(pending),
            "used_with_twitter_account":              len(used),
            "failed_permanent_with_twitter_account":  len(failed_permanent),
            "total_with_twitter_account":             len(tw_topics),
        },
        "pending_topics": [
            {
                "topic": t.topic, "score": t.score, "twitter_identifier": u, "source": t.source,
                "recommendation_date": t.recommendation_date.isoformat(), "created_at": t.created_at.isoformat(),
            }
            for t, u in pending_sorted
        ],
        "failed_permanent_topics": [
            {
                "topic": t.topic, "twitter_identifier": u, "source": t.source,
                "recommendation_date": t.recommendation_date.isoformat(),
            }
            for t, u in failed_permanent
        ],
        "recent_runs": [
            {
                "topic": r.keyword_text, "status": r.status, "triggered_by": r.triggered_by,
                "api_source": r.api_source, "videos_fetched": r.videos_fetched, "videos_new": r.videos_new,
                "duration_seconds": round(r.duration_seconds, 2) if r.duration_seconds is not None else None,
                "error_message": r.error_message, "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
        "running_now": [
            {
                "topic": r.keyword_text, "triggered_by": r.triggered_by, "api_source": r.api_source,
                "started_at": r.started_at.isoformat(),
                "elapsed_seconds": round((now - r.started_at).total_seconds(), 1),
            }
            for r in running_runs
        ],
    }


async def discover_twitter_topic_by_keyword(
    db: AsyncSession, keyword: str, max_results: int = 10, source: str = "manual_twitter_search",
) -> dict:
    """
    Search Twitter/X LANGSUNG by keyword (Apify `danek/twitter-scraper`, mode
    search) — TIDAK ada AI menebak akun. Akun diambil langsung dari field
    top-level `screen_name` (data terstruktur), sesederhana TikTok (beda
    dengan Facebook yang harus extract identifier dari URL post).

    CATATAN SKEMA (diverifikasi live 08 Juli 2026): mode search punya bentuk
    BEDA dari mode profil (app/integrations/apify/twitter.py) — identifier
    ada di `screen_name` TOP-LEVEL, BUKAN nested `author.screen_name` (field
    `author` tidak ada sama sekali di hasil search, diganti `user_info`).

    Hasil disubmit ke trend_recommendations (source=`source` param, default
    'manual_twitter_search') lewat submit_recommendations() yang SUDAH ADA —
    topiknya ikut antrian budget harian run_daily_trend_scrape_twitter()
    seperti topik AI biasa. Dipakai baik oleh tingkat-3 search_twitter_posts()
    MAUPUN POST /twitter/discover (trigger manual, cari topik BARU) MAUPUN
    app/services/search_topics/discovery.py (Smart Search, source=
    'smart_search_twitter').
    """
    from app.integrations.apify.twitter import search_twitter_by_keyword
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem
    from app.services.trend_recommendations.service import submit_recommendations

    try:
        raw_posts = await search_twitter_by_keyword(keyword, max_results=max_results)
    except Exception as exc:
        logger.error("discover_twitter_topic_by_keyword: search gagal untuk keyword=%r: %s", keyword, exc)
        return {"keyword": keyword, "posts_found": 0, "accounts_found": [], "submitted": None, "error": str(exc)}

    # search_twitter_by_keyword() pakai search_type="Latest" (bukan "Top") supaya
    # tweet yang ketemu genuinely dari HARI INI (lihat catatan di twitter.py) —
    # tapi "Latest" tidak mengurutkan berdasar engagement sama sekali (murni
    # kronologis), jadi diurutkan manual di sini (favorites+retweets tertinggi
    # dulu) supaya akun yang disubmit tetap yang relatif paling "viral" di
    # antara tweet hari ini, bukan sekadar yang paling baru diposting.
    raw_posts = sorted(
        raw_posts,
        key=lambda p: (p.get("favorites") or 0) + (p.get("retweets") or 0),
        reverse=True,
    )

    seen: set[str] = set()
    accounts: list[dict] = []
    sample_posts: list[dict] = []
    for post in raw_posts:
        identifier = post.get("screen_name")
        if identifier and identifier not in seen:
            seen.add(identifier)
            accounts.append({"platform": "twitter", "username": identifier})
        tweet_id = post.get("tweet_id")
        user_info = post.get("user_info") or {}
        sample_posts.append({
            "text": (post.get("text") or "")[:200],
            "author": user_info.get("name") or identifier,
            "url": f"https://x.com/{identifier}/status/{tweet_id}" if identifier and tweet_id else "",
            "identifier_extracted": identifier,
        })

    if not accounts:
        return {
            "keyword": keyword, "posts_found": len(raw_posts), "accounts_found": [],
            "submitted": None, "sample_posts": sample_posts,
            "message": "Tweet ditemukan tapi tidak ada screen_name — cek sample_posts.",
        }

    body = TrendRecommendationBatchCreate(
        items=[TrendRecommendationItem(topic=keyword, score=DISCOVER_DEFAULT_SCORE, related_accounts=accounts)],
        source=source,
    )
    result = await submit_recommendations(db, body)

    logger.info(
        "discover_twitter_topic_by_keyword: keyword=%r posts=%d akun=%d submitted=%s",
        keyword, len(raw_posts), len(accounts), result,
    )
    return {
        "keyword": keyword, "posts_found": len(raw_posts), "accounts_found": accounts,
        "submitted": result, "sample_posts": sample_posts[:5],
    }
