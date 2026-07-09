"""
News Trend Discovery — pipeline MANDIRI, terpisah TOTAL dari
app/ai/llm/viral_discovery_service.py (AI viral discovery Instagram dkk).
SENGAJA tidak menyentuh atau bergantung pada file itu sama sekali — supaya
scraping Facebook/Instagram/TikTok/Twitter (yang jalan lewat file itu, sudah
live/otomatis tiap hari) tidak pernah berisiko terganggu oleh perubahan di
sini.

Beda dari discovery topik medsos (yang butuh LLM buat "reasoning" cari akun
dari hasil pencarian bebas): untuk berita, cukup search LANGSUNG ke Firecrawl
dengan query generik ("berita trending hari ini", dst) lalu scrape artikel
hasilnya — TIDAK ADA LLM/AI sama sekali di jalur ini, jadi tidak tergantung
Ollama/Anthropic/OpenAI atau kualitas ekstraksinya.

Dipanggil oleh app/workers/news_worker.py (task Celery terjadwal, lihat
beat_schedule "news-discovery-daily" di app/workers/celery_app.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.scrape_runs.models import ScrapeRun

logger = logging.getLogger(__name__)

# Query sapuan harian untuk cari berita trending — generik dengan sengaja
# (bukan satu topik spesifik), gampang diubah tanpa ubah kode lain.
DEFAULT_NEWS_QUERIES = [
    "berita trending hari ini Indonesia",
    "berita viral hari ini",
    "berita terpopuler Indonesia hari ini",
]


async def run_daily_news_discovery(db: AsyncSession) -> dict:
    """
    Proses harian: search Firecrawl (beberapa query sapuan), kumpulkan URL
    unik, buang yang sudah ada di DB (hemat kuota Firecrawl /v1/scrape),
    scrape isi lengkap maks `settings.news_discovery_daily_budget` URL BARU,
    simpan sebagai posts (platform='news').
    """
    from app.integrations.firecrawl.news import scrape_article, search_news_by_keyword
    from app.services.news.pipeline_service import compute_external_id, save_news_articles
    from app.shared.config import settings

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="news_daily_discovery", platform="news", api_source="firecrawl",
        status="running", triggered_by="celery_beat", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

    seen_urls: set[str] = set()
    unique_urls: list[str] = []

    try:
        for query in DEFAULT_NEWS_QUERIES:
            try:
                results = await search_news_by_keyword(query, max_results=5)
            except Exception as exc:
                logger.warning("run_daily_news_discovery: search gagal untuk query=%r: %s", query, exc)
                continue
            for r in results:
                url = r.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_urls.append(url)

        ext_ids = [compute_external_id(u) for u in unique_urls]
        existing_ext_ids: set[str] = set()
        if ext_ids:
            existing_ext_ids = set((await db.scalars(
                select(Post.external_id).where(Post.platform == "news", Post.external_id.in_(ext_ids))
            )).all())

        budget = settings.news_discovery_daily_budget
        new_urls = [u for u in unique_urls if compute_external_id(u) not in existing_ext_ids][:budget]

        articles = []
        for url in new_urls:
            article = await scrape_article(url)
            if article:
                articles.append(article)

        save_result = await save_news_articles(db, articles)

        scrape_run.status = "success" if save_result["articles_saved"] > 0 else "failed"
        scrape_run.videos_fetched = len(unique_urls)
        scrape_run.videos_new = save_result["articles_saved"]
        if not unique_urls:
            scrape_run.error_message = "Tidak ada URL berita ditemukan hari ini"
    except Exception as exc:
        logger.error("run_daily_news_discovery error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    result = {"urls_found": len(unique_urls), "articles_saved": scrape_run.videos_new}
    logger.info("run_daily_news_discovery: %s", result)
    return result


async def get_news_trend_scrape_summary(db: AsyncSession, recent_limit: int = 10) -> dict:
    """
    Ringkasan pipeline discovery News — dipakai `GET /youtube/monitor-public`
    (dashboard publik `/scraping-status`), mirroring pola
    `get_facebook_trend_scrape_summary()` dkk.

    BEDA dari Facebook/Instagram/TikTok/Twitter: News bukan Subsistem A→B
    (tidak ada konsep topik pending/used di `trend_recommendations`, artikel
    langsung disimpan begitu ditemukan) — jadi ringkasannya cuma riwayat run
    + statistik artikel, bukan pending/used topic count.
    """
    from app.shared.config import settings

    total_articles: int = (await db.scalar(
        select(func.count()).select_from(Post).where(Post.platform == "news")
    )) or 0

    today = datetime.now(timezone.utc).date()
    articles_today: int = (await db.scalar(
        select(func.count()).select_from(Post)
        .where(Post.platform == "news", func.date(Post.collected_at) == today)
    )) or 0

    runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "news")
        .order_by(ScrapeRun.started_at.desc())
        .limit(recent_limit)
    )).all()

    now = datetime.now(timezone.utc)
    running_runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "news", ScrapeRun.status == "running")
        .order_by(ScrapeRun.started_at.desc())
    )).all()

    latest_articles = (await db.scalars(
        select(Post).where(Post.platform == "news").order_by(Post.collected_at.desc()).limit(5)
    )).all()

    return {
        "daily_budget": settings.news_discovery_daily_budget,
        "schedule": (
            f"{settings.news_discovery_schedule_hour:02d}:"
            f"{settings.news_discovery_schedule_minute:02d} WIB otomatis (Celery Beat) — "
            "pipeline mandiri, TIDAK terkait AI viral discovery medsos"
        ),
        "summary": {
            "total_articles": total_articles,
            "articles_today": articles_today,
        },
        "latest_articles": [
            {
                "title": (p.metadata_ or {}).get("title"),
                "url": p.url,
                "collected_at": p.collected_at.isoformat() if p.collected_at else None,
            }
            for p in latest_articles
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
