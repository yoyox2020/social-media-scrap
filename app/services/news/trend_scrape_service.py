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
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.scrape_runs.models import ScrapeRun

logger = logging.getLogger(__name__)

# Query sapuan harian untuk cari berita trending — generik dengan sengaja
# (bukan satu topik spesifik), gampang diubah tanpa ubah kode lain.
#
# CATATAN PENTING 2026-07-19: query generik ini TERBUKTI LIVE (dites
# langsung ke Firecrawl) SELALU balikin halaman BERANDA situs berita
# (news.detik.com/, kompas.com/, dst) atau halaman TAG/KATEGORI
# (detik.com/tag/viral) -- BUKAN artikel individual. Karena halaman itu
# STABIL (tidak berubah tiap hari), begitu ketemu SEKALI langsung dianggap
# "sudah ada" SELAMANYA oleh dedup -- akibatnya "trending" berhenti
# bertambah sejak semua kombinasi query x situs populer sudah pernah
# ditemukan (root cause "data tidak ada" yg dilaporkan user). Query
# GENERIK ini DIPERTAHANKAN sbg pelengkap/fallback (murah, tetap jalan
# kalau trend_recommendations kosong), tapi SEKARANG DIGABUNG dgn query
# topik SPESIFIK dari trend_recommendations (lihat _get_trending_topic_queries())
# -- dites live, query topik spesifik (mis. "harga BBM naik 2026")
# TERBUKTI balikin artikel individual asli (bbc.com/indonesia/articles/...,
# dst), bukan cuma halaman beranda.
DEFAULT_NEWS_QUERIES = [
    "berita trending hari ini Indonesia",
    "berita viral hari ini",
    "berita terpopuler Indonesia hari ini",
]

# Berapa hari ke belakang topik trend_recommendations masih dianggap
# relevan dipakai sbg query News -- lebih dari 1 hari (bukan cuma
# "hari ini") krn task News jalan jam 06:00 UTC (13:00 WIB), topik hari
# itu mungkin belum sempat disubmit AI eksternal saat itu.
_TRENDING_TOPIC_LOOKBACK_DAYS = 2

# CATATAN 2026-07-19: SEBELUMNYA dibatasi _TRENDING_TOPIC_MAX_QUERIES=5 --
# atas permintaan eksplisit user, cap ini DIHAPUS (unlimited, SEMUA topik
# unik dlm lookback window dipakai). Live-verified 2026-07-19: window 2 hari
# produksi berisi ~57 topik unik -> unlimited berarti puluhan panggilan
# Firecrawl /v1/search per hari (naik dari 5), BUKAN cuma naik sedikit --
# user sudah diberi tahu implikasi kredit Firecrawl ini sebelum konfirmasi.

# Domain yg BUKAN artikel berita sama sekali (medsos, aggregator) --
# walau Firecrawl search kadang balikin ini, JANGAN pernah dianggap
# kandidat artikel (tidak ada isi berita utk di-scrape).
_NON_ARTICLE_DOMAINS = (
    "instagram.com", "tiktok.com", "youtube.com", "x.com", "twitter.com",
    "facebook.com", "news.google.com",
)
# Pola path yg menandakan halaman TAG/KATEGORI (kumpulan artikel, BUKAN
# satu artikel) -- ditemukan live 2026-07-19 sbg mayoritas hasil query
# generik ("detik.com/tag/viral", "okezone.com/tag/viral", dst).
_NON_ARTICLE_PATH_PATTERNS = ("/tag/", "/tags/", "/category/", "/categories/", "/kategori/", "/topic/", "/topics/")


def _looks_like_article_url(url: str) -> bool:
    """Heuristik URL artikel BUKAN halaman beranda/kategori/medsos --
    BUKAN validasi sempurna (beberapa false-positive/negative mungkin
    lolos), tujuannya cuma buang mayoritas "sampah" yg TERBUKTI live jadi
    penyebab utama dedup buntu (lihat catatan DEFAULT_NEWS_QUERIES di
    atas). Halaman BERANDA (path kosong/"/") & TAG/KATEGORI & domain
    medsos DIANGGAP bukan artikel; selain itu (ada path spesifik) DIANGGAP
    kandidat artikel valid."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    domain = parsed.netloc.lower().removeprefix("www.")
    if any(d in domain for d in _NON_ARTICLE_DOMAINS):
        return False
    path = parsed.path.rstrip("/")
    if not path:
        return False
    if any(p in parsed.path for p in _NON_ARTICLE_PATH_PATTERNS):
        return False
    return True


async def _get_trending_topic_queries(db: AsyncSession) -> list[str]:
    """Ambil topik trend_recommendations TERBARU (score tertinggi dulu)
    sbg query News DINAMIS -- READ-ONLY, TIDAK mengubah/menulis apa pun ke
    trend_recommendations (lihat memory feedback_trend_recommendations_frozen
    -- fitur itu FINAL, jangan diubah tanpa konfirmasi eksplisit user; di
    sini cuma BACA topik yg sudah ada, konsisten dgn cara platform lain
    konsumsi trend_recommendations)."""
    from app.domain.trend_recommendations.models import TrendRecommendation

    since = (datetime.now(timezone.utc) - timedelta(days=_TRENDING_TOPIC_LOOKBACK_DAYS)).date()
    rows = (await db.scalars(
        select(TrendRecommendation.topic)
        .where(TrendRecommendation.recommendation_date >= since)
        .order_by(TrendRecommendation.score.desc())
        # TANPA .limit() -- SEMUA topik unik dlm lookback window dipakai
        # (unlimited, permintaan eksplisit user 2026-07-19, lihat catatan
        # _TRENDING_TOPIC_LOOKBACK_DAYS di atas soal implikasi kredit Firecrawl)
    )).all()

    seen: set[str] = set()
    queries: list[str] = []
    for topic in rows:
        topic_clean = (topic or "").strip()
        if not topic_clean or topic_clean.lower() in seen:
            continue
        seen.add(topic_clean.lower())
        queries.append(f"{topic_clean} berita terbaru")
    return queries


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
    rejected_non_article = 0

    try:
        try:
            dynamic_queries = await _get_trending_topic_queries(db)
        except Exception as exc:
            logger.warning("run_daily_news_discovery: gagal ambil trending topic queries: %s", exc)
            dynamic_queries = []

        all_queries = DEFAULT_NEWS_QUERIES + dynamic_queries

        for query in all_queries:
            try:
                results = await search_news_by_keyword(query, max_results=5)
            except Exception as exc:
                logger.warning("run_daily_news_discovery: search gagal untuk query=%r: %s", query, exc)
                continue
            for r in results:
                url = r.get("url")
                if not url or url in seen_urls:
                    continue
                if not _looks_like_article_url(url):
                    rejected_non_article += 1
                    continue
                seen_urls.add(url)
                unique_urls.append(url)

        if rejected_non_article:
            logger.info("run_daily_news_discovery: %d URL ditolak (halaman beranda/kategori/medsos)", rejected_non_article)

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
