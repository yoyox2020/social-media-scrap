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

from sqlalchemy import select
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
