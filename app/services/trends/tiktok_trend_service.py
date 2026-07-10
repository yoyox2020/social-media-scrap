"""
TikTok Trend Discovery — pipeline MANDIRI, bagian dari Multi-Signal Trend
Discovery. TIDAK menyentuh app/services/tiktok/ yang sudah ada sama sekali.

CATATAN METODOLOGI PENTING: TikTok TIDAK PUNYA actor Trend Discovery native
yang reliable -- `clockworks/tiktok-trends-scraper` sudah dicoba live 2x
(2026-07-10, 2 kombinasi parameter beda), keduanya gagal ambil data dari
endpoint TikTok Ads Creative Center (ditolak berulang, actor tetap lapor
"SUCCEEDED" walau 0 hasil -- status MENYESATKAN, jangan percaya status
sukses actor ini tanpa cek item count).

Fallback (dipilih user secara eksplisit): SAPUAN independen via actor yang
SUDAH TERBUKTI jalan di project ini (`search_tiktok_by_keyword`, dipakai
juga oleh POST /tiktok/discover).

PENTING soal query yang dipakai: kalau sapuan ini pakai frasa generik statis
("viral hari ini") sebagai `topic`, hasilnya TIDAK PERNAH bisa dicocokkan
kata-per-kata dengan topik spesifik dari Twitter Trends ("Piala Dunia 2026")
di tahap triangulasi -- tidak ada kata yang sama sama sekali, jadi sinyal
ini jadi sia-sia untuk triangulasi. Makanya urutan sumber query:
  1. Topik trend Twitter native HARI INI (`trend_recommendations` WHERE
     source='twitter_native_trend' AND recommendation_date=hari ini) --
     dipakai sebagai query pencarian TikTok. Ini bikin hasil sapuan jadi
     "cek silang topik spesifik yang SUDAH ditemukan sumber lain", bukan
     tebakan buta -- sinyalnya jauh lebih berarti untuk triangulasi.
  2. Kalau belum ada data Twitter hari ini (belum jalan / gagal / trigger
     manual di luar jadwal), FALLBACK ke `settings.trends_sweep_queries`
     (generik) supaya pipeline TikTok tetap independen dan tidak berhenti
     total kalau Twitter gagal.
Jadwal (14:00 Twitter -> 14:15 TikTok) sengaja dibuat begini supaya kondisi
1 (data Twitter sudah ada) adalah kondisi NORMAL sehari-hari.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun

logger = logging.getLogger(__name__)


async def _todays_twitter_trend_topics(db: AsyncSession, limit: int) -> list[str]:
    """Baca topik trend_recommendations source='twitter_native_trend' hari ini
    (baca-saja, tidak menyentuh/mengubah data) -- dipakai sebagai query
    pencarian TikTok supaya sapuan ini cek silang topik spesifik yang SUDAH
    ditemukan, bukan tebak buta. Lihat docstring modul."""
    from app.domain.trend_recommendations.models import TrendRecommendation

    stmt = (
        select(TrendRecommendation.topic)
        .where(
            TrendRecommendation.source == "twitter_native_trend",
            TrendRecommendation.recommendation_date == date.today(),
        )
        .order_by(TrendRecommendation.score.desc())
        .limit(limit)
    )
    rows = await db.execute(stmt)
    return [r[0] for r in rows.all()]


async def _record_cross_source_confirmation(db: AsyncSession, topic_text: str, confirming_source: str) -> bool:
    """Kalau row trend_recommendations utk topic_text HARI INI sudah ada dari
    sumber LAIN (mis. twitter_native_trend), TIDAK boleh dipanggil lewat
    submit_recommendations() biasa -- upsertnya keyed cuma (topic, date) dan
    akan MENIMPA field `source` row asli, menghancurkan jejak asal sumber
    yang justru dibutuhkan combined_trend_service utk triangulasi. Jadi di
    sini cukup catat bukti konfirmasi ke raw_payload (append-only), source
    dan score asli TIDAK disentuh.

    Return True kalau berhasil menemukan+menandai row yang sudah ada
    (artinya JANGAN submit_recommendations lagi utk topik ini)."""
    from app.domain.trend_recommendations.models import TrendRecommendation

    stmt = select(TrendRecommendation).where(
        TrendRecommendation.topic == topic_text,
        TrendRecommendation.recommendation_date == date.today(),
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is None or existing.source == confirming_source:
        return False

    payload = dict(existing.raw_payload or {})
    confirmed_by = set(payload.get("confirmed_by", []))
    confirmed_by.add(confirming_source)
    payload["confirmed_by"] = sorted(confirmed_by)
    existing.raw_payload = payload
    return True


async def run_tiktok_trend_discovery(db: AsyncSession) -> dict:
    """
    Search TikTok pakai topik trend Twitter hari ini kalau ada (lihat
    `_todays_twitter_trend_topics`), fallback ke sapuan query generik kalau
    belum ada data Twitter (lihat catatan metodologi di docstring modul).
    Tiap query yang menghasilkan post disubmit sebagai topik
    (source='tiktok_hashtag_sweep'), akun asli yang ditemukan ikut
    disertakan.
    """
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem
    from app.integrations.apify.tiktok import search_tiktok_by_keyword
    from app.services.trend_recommendations.service import submit_recommendations
    from app.shared.config import settings

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="tiktok_hashtag_sweep_discovery", platform="tiktok_trends", api_source="apify",
        status="running", triggered_by="celery_beat", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    items = []
    confirmed: list[str] = []
    total_posts_found = 0

    try:
        max_results = settings.trends_max_per_source
        candidate_topics = await _todays_twitter_trend_topics(db, limit=len(settings.trends_sweep_queries))
        queries = candidate_topics or settings.trends_sweep_queries
        is_candidate_run = bool(candidate_topics)
        for query in queries:
            try:
                raw_posts = await search_tiktok_by_keyword(query, max_results=max_results)
            except Exception as exc:
                logger.warning("run_tiktok_trend_discovery: query=%r gagal: %s", query, exc)
                continue

            total_posts_found += len(raw_posts)
            seen: set[str] = set()
            accounts: list[dict] = []
            for post in raw_posts:
                identifier = (post.get("authorMeta") or {}).get("name")
                if identifier and identifier not in seen:
                    seen.add(identifier)
                    accounts.append({"platform": "tiktok", "username": identifier})

            if not accounts:
                continue

            if is_candidate_run and await _record_cross_source_confirmation(db, query, "tiktok_hashtag_sweep"):
                # Row topik ini sudah ada dari sumber lain (twitter_native_trend)
                # hari ini -- sudah ditandai konfirmasi via raw_payload, JANGAN
                # submit_recommendations lagi (akan menimpa source aslinya).
                confirmed.append(query)
                continue

            # Skor dari volume hasil -- sinyal LEMAH (bukan native trending),
            # cuma "ada aktivitas nyata soal query ini", lihat catatan metodologi.
            score = round(min(1.0, len(raw_posts) / max_results) * 0.6, 3)
            items.append(TrendRecommendationItem(topic=query, score=score, related_accounts=accounts))

        result = {"created": [], "updated": [], "evicted": [], "rejected": []}
        if items:
            body = TrendRecommendationBatchCreate(items=items, source="tiktok_hashtag_sweep")
            result = await submit_recommendations(db, body)

        scrape_run.status = "success" if (items or confirmed) else "failed"
        scrape_run.videos_fetched = total_posts_found
        scrape_run.videos_new = len(result.get("created", []))
        if not items and not confirmed:
            scrape_run.error_message = "Tidak ada post/akun ditemukan dari sapuan query hari ini"
    except Exception as exc:
        logger.error("run_tiktok_trend_discovery error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        result = {"error": str(exc)}
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    logger.info("run_tiktok_trend_discovery: found=%d confirmed=%s submitted=%s", total_posts_found, confirmed, result)
    return {"found": total_posts_found, "confirmed": confirmed, "submitted": result}
