"""
Smart Search tier-3 dispatch -- SATU tempat yang tahu platform mana pakai
model ACCOUNT-DISCOVERY (submit ke trend_recommendations, TIDAK langsung
scrape -- Facebook/TikTok/Twitter) vs model DIRECT-POST (post langsung
tersimpan, TIDAK PERNAH lewat trend_recommendations sama sekali --
Instagram/News) vs YouTube (jalur Keyword-nya sendiri, tidak Apify).

TIDAK menulis ulang logic scraping platform manapun -- cuma memanggil
fungsi yang SUDAH ADA dan SUDAH terbukti (dipakai jalur /posts/search
interaktif tiap platform). TANPA AI/LLM sama sekali -- search keyword
LANGSUNG ke Apify/Firecrawl, sesuai keputusan user.

Tier-2 (cek trend_recommendations SEBELUM panggil tier-3 yang mahal) SENGAJA
TIDAK ada di file ini -- itu tanggung jawab pemanggil (rescan_service.py
utk jadwal berkala, tempat cost-control paling penting; topic_search.py
utk pencarian awal cukup tier-1 -> tier-3 langsung).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ACCOUNT_DISCOVERY_PLATFORMS = {"facebook", "tiktok", "twitter"}
# Threads ditambahkan 2026-07-19 -- search berbasis keyword LANGSUNG (via
# EnsembleData), post tersimpan seketika, TIDAK butuh tahap "temukan akun"
# spt Facebook/TikTok/Twitter -- pola sama dgn Instagram/News.
DIRECT_POST_PLATFORMS = {"instagram", "news", "threads"}
ALL_SMART_SEARCH_PLATFORMS = ACCOUNT_DISCOVERY_PLATFORMS | DIRECT_POST_PLATFORMS | {"youtube"}


async def run_tier3_discovery(
    db: AsyncSession,
    platform: str,
    keyword_text: str,
    max_results: int = 10,
    source_tag: str | None = None,
) -> dict[str, Any]:
    """
    Jalankan tier-3 (search third-party LANGSUNG, TANPA AI/LLM) utk SATU
    platform+keyword. `source_tag` opsional -- dipakai Smart Search utk tag
    'smart_search_<platform>' (lihat mekanisme reserved-slot di
    submit_recommendations()); default None berarti fungsi platform pakai
    default masing2 (mis. 'manual_facebook_search') supaya perilaku caller
    lain (endpoint /posts/search interaktif tiap platform) TIDAK berubah.
    """
    if platform == "facebook":
        from app.services.facebook.trend_scrape_service import discover_facebook_topic_by_keyword
        kwargs = {"source": source_tag} if source_tag else {}
        return await discover_facebook_topic_by_keyword(db, keyword_text, max_results=max_results, **kwargs)

    if platform == "tiktok":
        from app.services.tiktok.trend_scrape_service import discover_tiktok_topic_by_keyword
        kwargs = {"source": source_tag} if source_tag else {}
        return await discover_tiktok_topic_by_keyword(db, keyword_text, max_results=max_results, **kwargs)

    if platform == "twitter":
        from app.services.twitter.trend_scrape_service import discover_twitter_topic_by_keyword
        kwargs = {"source": source_tag} if source_tag else {}
        return await discover_twitter_topic_by_keyword(db, keyword_text, max_results=max_results, **kwargs)

    if platform == "instagram":
        return await _discover_instagram(db, keyword_text, max_results)

    if platform == "news":
        return await _discover_news(db, keyword_text, max_results)

    if platform == "threads":
        return await _discover_threads(db, keyword_text, max_results)

    if platform == "youtube":
        return await _discover_youtube(db, keyword_text)

    return {"keyword": keyword_text, "posts_found": 0, "error": f"platform '{platform}' tidak didukung Smart Search"}


async def _discover_instagram(db: AsyncSession, keyword_text: str, max_results: int) -> dict[str, Any]:
    """Direct-post model -- posts tersimpan langsung, TIDAK lewat
    trend_recommendations sama sekali."""
    from app.integrations.apify.instagram_search import search_instagram_posts_by_keyword
    from app.services.instagram.pipeline_service import save_instagram_keyword_search_results

    try:
        raw_items = await search_instagram_posts_by_keyword(keyword_text, max_results=max_results)
    except Exception as exc:
        logger.error("run_tier3_discovery[instagram]: gagal utk keyword=%r: %s", keyword_text, exc)
        return {"keyword": keyword_text, "posts_found": 0, "error": str(exc)}

    # Actor instagram-hashtag-scraper bisa return 1 item marker error
    # ({"error":"no_items",...}, TANPA shortCode) walau status SUCCEEDED --
    # lihat docs/analisa-gap-instagram.md gap C.
    real_items = [it for it in raw_items if it.get("shortCode")]
    save_result = await save_instagram_keyword_search_results(db, real_items)
    return {"keyword": keyword_text, "posts_found": len(real_items), "saved": save_result}


async def _discover_news(db: AsyncSession, keyword_text: str, max_results: int) -> dict[str, Any]:
    """Direct-post model, SEPARATE dari trend_recommendations -- News
    SENGAJA diisolasi dari alur AI-discovery platform lain (keputusan
    eksplisit sebelumnya, lihat app/api/v1/news/router.py)."""
    from app.integrations.firecrawl.news import scrape_article, search_news_by_keyword
    from app.services.news.pipeline_service import save_news_articles

    try:
        search_results = await search_news_by_keyword(keyword_text, max_results=max_results)
    except Exception as exc:
        logger.error("run_tier3_discovery[news]: search gagal utk keyword=%r: %s", keyword_text, exc)
        return {"keyword": keyword_text, "posts_found": 0, "error": str(exc)}

    articles = []
    for r in search_results:
        url = r.get("url")
        if not url:
            continue
        article = await scrape_article(url)
        if article:
            articles.append(article)

    save_result = await save_news_articles(db, articles)
    return {"keyword": keyword_text, "posts_found": len(articles), "saved": save_result}


async def _discover_threads(db: AsyncSession, keyword_text: str, max_results: int) -> dict[str, Any]:
    """Direct-post model -- posts+balasan tersimpan langsung via EnsembleData
    (app/services/threads/pipeline_service.py), TIDAK lewat trend_recommendations
    sama sekali di jalur INI (beda dari jadwal harian Threads yg baca
    trend_recommendations, lihat app/services/threads/trend_scrape_service.py
    -- keduanya SAMA-SAMA memanggil search_threads_posts(), cuma sumber
    keyword-nya beda: sini dari Smart Search user, jadwal harian dari topik
    AI-discovery). comments_top_n dibuat kecil (1) krn ini jalur interaktif
    (bisa dipicu user kapan saja), bukan batch terjadwal -- kendali biaya
    EnsembleData (lihat catatan kuota di pipeline_service.py)."""
    from app.services.threads.pipeline_service import search_threads_posts

    try:
        result = await search_threads_posts(db, keyword=keyword_text, max_posts=max_results, comments_top_n=1)
    except Exception as exc:
        logger.error("run_tier3_discovery[threads]: gagal utk keyword=%r: %s", keyword_text, exc)
        return {"keyword": keyword_text, "posts_found": 0, "error": str(exc)}

    return {"keyword": keyword_text, "posts_found": result.get("posts_found", 0), "saved": result}


async def _discover_youtube(db: AsyncSession, keyword_text: str) -> dict[str, Any]:
    """
    YouTube TIDAK pakai model account-discovery/direct-post seperti platform
    lain -- pipeline-nya sendiri berbasis `Keyword` row (YouTube Data
    API/EnsembleData search LANGSUNG pakai teks keyword, BUKAN Apify). Reuse
    `collect_youtube_pipeline_task` yang SUDAH ADA (persis pola `_queue_crawl()`
    lama di topic_search.py), TIDAK diubah sama sekali.
    """
    from sqlalchemy import func, select

    from app.domain.keywords.models import Keyword
    from app.domain.projects.models import Project
    from app.workers.youtube_worker import collect_youtube_pipeline_task

    kw = await db.scalar(
        select(Keyword).where(func.lower(Keyword.keyword) == keyword_text.strip().lower()).limit(1)
    )
    if not kw:
        project = await db.scalar(select(Project).limit(1))
        if not project:
            return {"keyword": keyword_text, "posts_found": 0, "error": "Tidak ada project di DB"}
        kw = Keyword(project_id=project.id, keyword=keyword_text, is_active=True)
        db.add(kw)
        await db.flush()
        await db.refresh(kw)

    # TIDAK pakai queue="default" -- bukan nama antrian nyata yang dikonsumsi
    # worker manapun (social_intel_worker/-ai listen di "collector,processing,
    # reports,celery" / "ai,celery", tidak ada "default"). Callers lain yang
    # TERBUKTI jalan (app/services/youtube/pipeline_service.py) pakai
    # .delay() TANPA argumen queue -- otomatis masuk antrian default Celery
    # sendiri ("celery"), yang MEMANG dikonsumsi semua worker container.
    collect_youtube_pipeline_task.apply_async(
        kwargs={"keyword_id": str(kw.id), "max_pages": 2, "max_comment_pages": 2, "max_comments_per_video": 50},
    )
    return {
        "keyword": keyword_text, "keyword_id": str(kw.id), "status": "crawling",
        "message": f"Keyword '{keyword_text}' dicrawl di background (YouTube)",
    }
