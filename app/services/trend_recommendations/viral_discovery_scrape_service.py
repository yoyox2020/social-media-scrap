"""
Orkestrasi viral discovery harian — file TERPISAH dari trend_scrape_service.py
(yang dibekukan, jangan disentuh) supaya frozen file itu tidak pernah perlu
diubah untuk fitur ini.

Alur: Claude (web_search) cari topik+akun Instagram viral hari ini
(app/ai/llm/viral_discovery_service.py) → submit ke trend_recommendations via
submit_recommendations() yang SUDAH ADA (dipanggil apa adanya, bukan
dimodifikasi) → catat satu ScrapeRun sebagai "bukti status pencarian" hari itu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.trend_recommendations.models import TrendRecommendation
from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate

logger = logging.getLogger(__name__)


async def run_daily_viral_discovery(db: AsyncSession) -> dict:
    """
    Jalankan satu putaran viral discovery: cari topik viral hari ini via AI,
    submit ke trend_recommendations (fungsi frozen, dipanggil apa adanya),
    catat hasilnya sebagai satu baris scrape_runs.
    """
    from app.ai.llm.viral_discovery_service import find_daily_viral_topics
    from app.services.trend_recommendations.service import submit_recommendations
    from app.shared.config import settings

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="ai_viral_discovery",
        platform="instagram",
        # api_source = provider AI yang SEDANG aktif (settings.ai_discovery_provider,
        # bisa "anthropic"/"openai"/"ollama") — BUKAN hardcode "anthropic_web_search"
        # lagi, supaya dashboard /scraping-status kelihatan benar kalau provider
        # di .env diganti (lihat memory project_ollama_websearch_quality).
        api_source=settings.ai_discovery_provider,
        status="running",
        triggered_by="celery_beat",
        started_at=started_at,
    )
    db.add(scrape_run)
    await db.flush()

    items: list[dict] = []
    result: dict = {"created": [], "updated": [], "evicted": [], "rejected": []}
    collected_urls: list[dict] = []  # diisi side-effect oleh find_daily_viral_topics() -- News Fase 2

    try:
        items = await find_daily_viral_topics(collected_urls=collected_urls)
        if items:
            body = TrendRecommendationBatchCreate(items=items, source="ai_viral_discovery")
            result = await submit_recommendations(db, body)

        scrape_run.status = "success" if items else "failed"
        scrape_run.videos_fetched = len(items)
        scrape_run.videos_new = len(result.get("created", []))
        if not items:
            scrape_run.error_message = "Tidak ada topik viral ditemukan hari ini"
    except Exception as exc:
        logger.error("run_daily_viral_discovery error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        result = {"error": str(exc)}
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    # ── News Fase 2: simpan artikel yang GENUINELY ditemukan selama pencarian
    # topik di atas (URL asli dari Firecrawl, cuma terisi kalau provider
    # Ollama yang jalan) — SENGAJA di luar try/except di atas & dibungkus
    # try/except SENDIRI: kegagalan di sini TIDAK BOLEH mengubah status/hasil
    # run_daily_viral_discovery() sama sekali, fitur topik+akun medsos yang
    # SUDAH ADA harus tetap berperilaku identik walau bagian ini gagal total.
    if collected_urls:
        try:
            await _save_discovered_news_articles(db, collected_urls)
        except Exception as exc:
            logger.warning("run_daily_viral_discovery: gagal simpan artikel news (%s)", exc)

    logger.info("run_daily_viral_discovery: found=%d submitted=%s", len(items), result)
    return {"found": len(items), "submitted": result}


async def _save_discovered_news_articles(db: AsyncSession, collected_urls: list[dict]) -> None:
    """
    News Fase 2 — simpan artikel berita yang genuinely ditemukan selama
    pencarian topik viral hari ini (URL asli dari Firecrawl, BUKAN snippet
    yang sudah diringkas AI) sebagai `posts` (platform='news').

    Dedup URL DULU terhadap `posts.external_id` yang sudah ada SEBELUM scrape
    isi lengkap — hemat kuota Firecrawl `/v1/scrape` (berbayar), jangan
    scrape ulang artikel yang sudah tersimpan. Dibatasi
    `settings.news_discovery_daily_budget` artikel BARU per run.
    """
    from sqlalchemy import select

    from app.domain.posts.models import Post
    from app.integrations.firecrawl.news import scrape_article
    from app.services.news.pipeline_service import compute_external_id, save_news_articles
    from app.shared.config import settings

    seen_urls: set[str] = set()
    unique_urls: list[str] = []
    for item in collected_urls:
        url = item.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_urls.append(url)

    if not unique_urls:
        return

    ext_ids = [compute_external_id(u) for u in unique_urls]
    existing_ext_ids = set((await db.scalars(
        select(Post.external_id).where(Post.platform == "news", Post.external_id.in_(ext_ids))
    )).all())
    new_urls = [u for u in unique_urls if compute_external_id(u) not in existing_ext_ids]

    if not new_urls:
        logger.info("run_daily_viral_discovery: news Fase 2 -- %d URL ditemukan, semua sudah tersimpan", len(unique_urls))
        return

    budget = settings.news_discovery_daily_budget
    new_urls = new_urls[:budget]

    articles = []
    for url in new_urls:
        article = await scrape_article(url)
        if article:
            articles.append(article)

    save_result = await save_news_articles(db, articles)
    logger.info(
        "run_daily_viral_discovery: news Fase 2 -- %d URL ditemukan, %d baru (budget=%d), %d artikel tersimpan",
        len(unique_urls), len(new_urls), budget, save_result["articles_saved"],
    )


async def get_viral_discovery_trace(db: AsyncSession) -> dict:
    """
    Lacak batch topik dari RUN AI DISCOVERY TERAKHIR (Subsistem A) ke status
    scrape-nya masing-masing di Subsistem B — bukan cuma "status terakhir
    tiap subsistem" yang independen, tapi benar-benar topik yang sama diikuti
    dari A ke B. Fungsi baca-saja, tidak menulis apapun.

    Pencocokan tanpa foreign key baru:
    1. Ambil ScrapeRun 'ai_viral_discovery' terakhir → rentang waktu run itu.
    2. Topik trend_recommendations dengan source='ai_viral_discovery' yang
       created_at jatuh dalam rentang waktu run itu = batch topik dari run ini
       (run_daily_viral_discovery() submit+commit dalam satu eksekusi, jadi
       rentang waktu run = batas batch yang akurat).
    3. Untuk tiap topik: kalau status='used', cari ScrapeRun dengan
       keyword_text SAMA PERSIS (run_daily_trend_scrape() selalu membuat satu
       ScrapeRun per topik dengan keyword_text=topic) untuk tahu kapan/provider
       mana/berapa lama Subsistem B memprosesnya.
    """
    ai_run = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.keyword_text == "ai_viral_discovery")
        .order_by(ScrapeRun.started_at.desc())
        .limit(1)
    )).first()

    if ai_run is None:
        return {"ai_run": None, "topics": []}

    window_end = (ai_run.finished_at or ai_run.started_at) + timedelta(seconds=5)
    batch_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(
            TrendRecommendation.source == "ai_viral_discovery",
            TrendRecommendation.created_at >= ai_run.started_at,
            TrendRecommendation.created_at <= window_end,
        )
        .order_by(TrendRecommendation.score.desc())
    )).all()

    traced = []
    for topic in batch_topics:
        # Cari upaya scrape TERBARU untuk topik ini, apapun hasilnya — supaya
        # topik yang masih 'pending' karena GAGAL discrape (bukan cuma belum
        # kebagian giliran) tetap kelihatan alasannya, bukan diam-diam kosong.
        # TIDAK filter platform di sini (dulu cuma "instagram", jadi topik yang
        # akunnya cuma Facebook tidak pernah ketemu attempt-nya walau genuinely
        # sudah diproses Subsistem B Facebook) — keyword_text=topic.topic sudah
        # cukup unik per hari untuk pencocokan lintas platform.
        run = (await db.scalars(
            select(ScrapeRun)
            .where(
                ScrapeRun.keyword_text == topic.topic,
                ScrapeRun.started_at > ai_run.started_at,
            )
            .order_by(ScrapeRun.started_at.desc())
            .limit(1)
        )).first()
        scrape_attempt = None
        if run:
            scrape_attempt = {
                "status":           run.status,
                "api_source":       run.api_source,
                "started_at":       run.started_at.isoformat(),
                "duration_seconds": round(run.duration_seconds, 2) if run.duration_seconds is not None else None,
                "error_message":    run.error_message,
            }
        traced.append({
            "topic":           topic.topic,
            "current_status":  topic.status,
            "scrape_attempt":  scrape_attempt,
        })

    return {
        "ai_run": {
            "status":      ai_run.status,
            "api_source":  ai_run.api_source,  # provider AI yang sebenarnya jalan (anthropic/openai/ollama)
            "started_at":  ai_run.started_at.isoformat(),
            "finished_at": ai_run.finished_at.isoformat() if ai_run.finished_at else None,
            "error_message": ai_run.error_message,
        },
        "topics": traced,
    }
