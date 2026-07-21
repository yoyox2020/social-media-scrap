"""
Threads Trend-Recommendation Scrape Service.

BEDA dari TikTok/Facebook/Instagram (yg scrape per-AKUN dari
`related_accounts`) -- Threads search berbasis KEYWORD/TOPIK TEKS langsung
(app/integrations/threads/connector.py::search_by_keyword), jadi pola di
sini LEBIH DEKAT ke News (app/services/news/trend_scrape_service.py):
baca topik dari trend_recommendations, pakai TEKS topiknya langsung sbg
query pencarian -- TIDAK butuh related_accounts sama sekali.

READ-ONLY terhadap trend_recommendations (lihat memory
feedback_trend_recommendations_frozen -- tabel itu FINAL, jangan diubah
skema/logikanya tanpa konfirmasi eksplisit user; di sini cuma BACA
topik+score, TIDAK PERNAH tulis `status` -- lihat catatan Fase 3 di
bawah).

**Fase 3 (2026-07-21, docs/threads-redesign-schema.md §3.1)**: kolom
`trend_recommendations.status` TERBUKTI dibagi bersama SEMUA platform
(dikonfirmasi di docstring lama
app/services/trend_recommendations/service.py::mark_failed_permanent_if_exhausted
-- "hitungan gagal ini LINTAS PLATFORM, bukan per-platform"). Threads
SEKARANG TIDAK LAGI baca/tulis `status` sama sekali -- pakai tabel
PENDAMPING `trend_recommendation_platform_usage` (platform='threads')
utk tracking sendiri, supaya topik yang sudah dipakai/gagal di platform
LAIN (mis. Facebook gagal cari related_accounts) tetap BISA dicoba
Threads (search berbasis keyword, bukan account, jadi kegagalan
platform lain tidak relevan), dan sebaliknya.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.trend_recommendations.models import TrendRecommendation
from app.domain.trend_recommendations.platform_usage_models import TrendRecommendationPlatformUsage
from app.shared.config import settings

logger = logging.getLogger(__name__)

_PLATFORM = "threads"


async def _mark_topic_used_by_threads(db: AsyncSession, topic_id) -> None:
    """Catat "Threads sudah coba topik ini" di tabel PENDAMPING --
    TIDAK menyentuh trend_recommendations.status sama sekali (lihat
    catatan modul). Dipanggil SETELAH 1x percobaan, apa pun hasilnya
    (sukses/gagal/0 hasil) -- Threads cuma coba tiap topik SEKALI."""
    db.add(TrendRecommendationPlatformUsage(
        trend_recommendation_id=topic_id, platform=_PLATFORM,
        used_at=datetime.now(timezone.utc),
    ))
    await db.commit()


async def run_daily_trend_scrape_threads(db: AsyncSession) -> dict:
    """
    Proses batch harian: search Threads maks `threads_trend_daily_budget`
    topik trend_recommendations yang BELUM PERNAH dicoba Threads
    (independen dari `status` global -- lihat catatan Fase 3 di atas),
    urut score tertinggi, pakai TEKS topik langsung sbg keyword pencarian.
    """
    from app.services.threads.pipeline_service import search_threads_posts

    budget = settings.threads_trend_daily_budget
    max_posts = settings.threads_trend_posts_per_topic
    comments_top_n = settings.threads_trend_comments_top_n

    already_used_subq = select(TrendRecommendationPlatformUsage.trend_recommendation_id).where(
        TrendRecommendationPlatformUsage.platform == _PLATFORM
    )
    candidate_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.id.not_in(already_used_subq))
        .order_by(TrendRecommendation.score.desc())
        .limit(budget)
    )).all()

    if not candidate_topics:
        logger.info("run_daily_trend_scrape_threads: tidak ada topik baru utk Threads")
        return {"processed": 0, "results": []}

    results = []
    for topic in candidate_topics:
        started_at = datetime.now(timezone.utc)
        scrape_run = ScrapeRun(
            keyword_text=topic.topic, platform="threads", api_source="ensembledata",
            status="running", triggered_by="celery_beat", started_at=started_at,
        )
        db.add(scrape_run)
        await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

        try:
            result = await search_threads_posts(
                db=db, keyword=topic.topic, max_posts=max_posts,
                comments_top_n=comments_top_n, keyword_id=None,
            )
            posts_found = result.get("posts_found", 0)
            posts_saved = result.get("posts_saved", 0)
            errors = result.get("errors", [])
            verified = posts_found >= 1

            scrape_run.status = "success" if verified else "failed"
            scrape_run.videos_fetched = posts_found
            scrape_run.videos_new = posts_saved
            scrape_run.error_message = "; ".join(str(e) for e in errors[:3]) if errors else None
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()

            results.append({
                "topic": topic.topic, "verified": verified,
                "posts_found": posts_found, "posts_saved": posts_saved,
                "replies_collected": result.get("replies_collected", []),
                "errors": errors,
            })
        except Exception as exc:
            logger.error("run_daily_trend_scrape_threads topic=%s error=%s", topic.topic, exc)
            scrape_run.status = "failed"
            scrape_run.error_message = str(exc)[:1000]
            scrape_run.finished_at = datetime.now(timezone.utc)
            results.append({"topic": topic.topic, "verified": False, "errors": [str(exc)]})

        await db.commit()
        # Tandai "sudah dicoba Threads" APA PUN hasilnya -- tiap topik cuma
        # dicoba SEKALI oleh Threads (budget harian utk topik BARU, bukan retry).
        await _mark_topic_used_by_threads(db, topic.id)

    logger.info("run_daily_trend_scrape_threads: %d topik diproses", len(results))
    return {"processed": len(results), "results": results}


async def get_threads_trend_scrape_summary(db: AsyncSession, recent_limit: int = 10) -> dict:
    """Ringkasan pipeline scrape Threads dari trend_recommendations --
    mirroring get_tiktok_trend_scrape_summary(), dipakai dashboard
    /scraping-status."""
    total_topics = (await db.scalar(select(func.count(TrendRecommendation.id)))) or 0
    used_by_threads = (await db.scalar(
        select(func.count(TrendRecommendationPlatformUsage.id))
        .where(TrendRecommendationPlatformUsage.platform == _PLATFORM)
    )) or 0

    recent_runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "threads")
        .order_by(ScrapeRun.started_at.desc())
        .limit(recent_limit)
    )).all()

    return {
        "daily_budget": settings.threads_trend_daily_budget,
        "posts_per_topic": settings.threads_trend_posts_per_topic,
        "comments_top_n": settings.threads_trend_comments_top_n,
        "schedule": f"{settings.threads_trend_scrape_schedule_hour:02d}:{settings.threads_trend_scrape_schedule_minute:02d} WIB",
        # Fase 3 (2026-07-21): angka ini SEKARANG spesifik-Threads (dari
        # trend_recommendation_platform_usage), BUKAN lagi status global
        # trend_recommendations yg dibagi semua platform -- lihat catatan
        # modul. "topics_used" = topik yg SUDAH dicoba Threads sendiri.
        "topics_pending": max(0, total_topics - used_by_threads),
        "topics_used": used_by_threads,
        "topics_failed_permanent": 0,  # Threads tidak lagi pakai status ini, lihat catatan modul
        "recent_runs": [
            {
                "keyword": r.keyword_text, "status": r.status,
                "posts_fetched": r.videos_fetched, "posts_new": r.videos_new,
                "error_message": r.error_message,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration_seconds": r.duration_seconds,
            }
            for r in recent_runs
        ],
    }


async def get_threads_monitoring_summary(db: AsyncSession, recent_limit: int = 10) -> dict:
    """
    Monitoring MENYELURUH Threads: status scraping SAMPAI sentiment --
    2026-07-19, permintaan user "monitoring dari status scraping hingga
    sentimentnya". `get_threads_trend_scrape_summary()` di atas cuma
    mencakup scraping (budget/jadwal/topik/riwayat run); fungsi ini
    menambahkan bagian DATA (jumlah post+komentar tersimpan) dan SENTIMENT
    (distribusi label lexicon + berapa persen yang SUDAH divalidasi LLM
    Sentiment Agent vs baru lexicon mentah -- lihat catatan
    project_threads_integration: Sentiment Agent BARU cover platform
    'youtube', jadi `llm_reviewed_pct` di sini SELALU 0.0% saat ini,
    ditampilkan APA ADANYA supaya transparan, bukan disembunyikan).
    """
    from app.domain.comments.models import Comment
    from app.domain.posts.models import Post
    from app.domain.youtube_analysis.models import LexiconAnalysis

    scraping = await get_threads_trend_scrape_summary(db, recent_limit=recent_limit)

    total_posts = (await db.scalar(
        select(func.count(Post.id)).where(Post.platform == "threads")
    )) or 0
    total_comments = (await db.scalar(
        select(func.count(Comment.id)).join(Post, Comment.post_id == Post.id).where(Post.platform == "threads")
    )) or 0

    label_rows = list((await db.scalars(
        select(func.coalesce(LexiconAnalysis.final_label, LexiconAnalysis.label))
        .join(Comment, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(Post.platform == "threads")
    )).all())
    reviewed_count = (await db.scalar(
        select(func.count(LexiconAnalysis.id))
        .join(Comment, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(Post.platform == "threads", LexiconAnalysis.llm_checked_at.isnot(None))
    )) or 0

    from collections import Counter
    counter = Counter(label_rows)
    total_analyzed = sum(counter.values())

    sentiment = {
        lbl: {
            "count": counter.get(lbl, 0),
            "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
        }
        for lbl in ["positif", "negatif", "netral"]
    }

    return {
        "scraping": scraping,
        "data": {
            "total_posts": total_posts,
            "total_comments": total_comments,
        },
        "sentiment": {
            **sentiment,
            "dominant": counter.most_common(1)[0][0] if counter else "netral",
            "total_analyzed": total_analyzed,
            "llm_reviewed_count": reviewed_count,
            "llm_reviewed_pct": round(reviewed_count / total_analyzed * 100, 1) if total_analyzed else 0.0,
            "note": (
                "Sentiment Agent (validasi LLM kedua) BARU mencakup platform='youtube' -- "
                "semua sentiment Threads di atas MASIH murni lexicon rule-based, belum tervalidasi LLM."
            ),
        },
    }
