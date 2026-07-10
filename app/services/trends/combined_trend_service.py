"""
Multi-Signal Trend Discovery — tahap TRIANGULASI (langkah TERAKHIR, jadwal
15:00 WIB, SETELAH Twitter 14:00 / TikTok 14:15 / Instagram 14:30 selesai).
Pipeline MANDIRI, bagian dari app/services/trends/ — TIDAK menyentuh kode
platform manapun yang sudah ada, TIDAK menulis ke app/domain/trending/models.py
(TrendingTopic milik YouTube) sama sekali, cuma BACA read-only dari sana.

METODOLOGI:
Tujuan: ganti "AI menebak topik viral" dengan sinyal OBJEKTIF yang
tervalidasi lintas >1 sumber independen (triangulasi) -- confidence_score
= jumlah_sumber_yang_mengkonfirmasi / jumlah_sumber_yang_BENAR-BENAR_JALAN
hari ini (bukan dibagi 5 secara buta -- kalau satu sumber gagal total hari
itu, itu tidak boleh menurunkan confidence topik lain secara tidak adil,
lihat `_sources_checked_today`).

5 sumber yang dicek:
  1. twitter_native_trend   -- Trends bawaan X (paling objektif)
  2. tiktok_hashtag_sweep   -- sapuan TikTok (lihat tiktok_trend_service.py)
  3. instagram_hashtag_sweep -- sapuan Instagram (lihat instagram_trend_service.py)
  4. google_trends          -- Google Trends RSS, diambil segar di sini
  5. youtube_trending       -- TrendingTopic (baca-saja, ranah YouTube sendiri)

Sumber 2 & 3 SUDAH melakukan pencocokan-persis (exact topic string) terhadap
sumber 1 di pipeline masing-masing (lihat `_record_cross_source_confirmation`
di tiktok/instagram_trend_service.py) -- hasilnya sudah tersimpan di
`raw_payload.confirmed_by` baris trend_recommendations terkait TANPA
menimpa `source` aslinya. Tugas modul ini CUMA menambahkan konfirmasi dari
sumber 4 & 5 (yang belum sempat dicek sumber manapun) via pencocokan kata
(word-overlap, MVP sederhana -- lihat `_topics_match`; upgrade ke embedding
kalau ternyata terlalu banyak topik yang seharusnya sama tapi tidak
ke-match, sesuai arahan user).

Dua jalur output, KEDUANYA tidak pernah menimpa `source`/`score` baris
platform yang sudah ada (upsert trend_recommendations keyed cuma
(topic, date) -- menimpa source row lain adalah bug yang sudah diperbaiki
di tiktok/instagram_trend_service.py, prinsip yang sama dipakai di sini):
  A. Baris platform (twitter/tiktok/instagram) yang SUDAH ADA hari ini --
     dapat tambahan konfirmasi dari Google Trends/YouTube -> HANYA
     raw_payload yang dianotasi (confirmed_by, confidence_score), source
     dan score ASLI tidak disentuh.
  B. Topik yang HANYA ditemukan Google Trends + YouTube (tidak ada baris
     platform existing hari ini) tapi keduanya saling cocok -> baris BARU
     disubmit lewat submit_recommendations() normal, source='multi_signal_trending'
     (aman, tidak ada baris lain yang bisa ketimpa karena topik ini belum
     ada sama sekali hari ini).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun

logger = logging.getLogger(__name__)

_PLATFORM_SWEEP_SOURCES = ("twitter_native_trend", "tiktok_hashtag_sweep", "instagram_hashtag_sweep")
_SOURCE_TO_SCRAPE_RUN_PLATFORM = {
    "twitter_native_trend": "twitter_trends",
    "tiktok_hashtag_sweep": "tiktok_trends",
    "instagram_hashtag_sweep": "instagram_trends",
}

# Kata generik yang muncul di query sapuan sendiri ("viral hari ini") atau
# terlalu umum utk jadi bukti kecocokan topik -- dibuang sebelum matching
# supaya tidak menghasilkan false-positive match.
_STOPWORDS = {
    "yang", "dan", "atau", "di", "ke", "dari", "ini", "itu", "untuk", "dengan",
    "pada", "adalah", "akan", "juga", "tidak", "ada", "saat", "hari", "lagi",
    "rame", "viral", "trending", "indonesia", "the", "a", "an", "of", "in",
    "on", "for", "and", "or", "is", "to", "with",
}


def _normalize(text: str) -> set[str]:
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return {w for w in text.split() if len(w) > 2 and w not in _STOPWORDS}


def _topics_match(a: set[str], b: set[str]) -> bool:
    """MVP sederhana: cocok kalau >=50% kata dari topik yang lebih pendek
    ikut muncul di topik satunya. Cukup ketat utk hindari false-positive
    (mis. dua topik yang cuma sama-sama punya satu kata umum)."""
    if not a or not b:
        return False
    overlap = a & b
    return len(overlap) / min(len(a), len(b)) >= 0.5


async def run_combined_trend_discovery(db: AsyncSession) -> dict:
    """
    Triangulasi lintas 5 sumber, lihat docstring modul. Return ringkasan
    (sources_checked, annotated, created) -- dipakai jalur monitoring
    /trends/status.
    """
    from app.domain.trend_recommendations.models import TrendRecommendation
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem
    from app.domain.trending.models import TrendingTopic
    from app.integrations.google_trends.connector import fetch_trending
    from app.services.trend_recommendations.service import submit_recommendations
    from app.shared.config import settings

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="multi_signal_triangulation", platform="trends_combined", api_source="internal",
        status="running", triggered_by="celery_beat", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    today = date.today()
    annotated: list[str] = []
    created: list[str] = []
    sources_checked: set[str] = set()

    try:
        platform_rows = (
            await db.execute(
                select(TrendRecommendation).where(
                    TrendRecommendation.recommendation_date == today,
                    TrendRecommendation.source.in_(_PLATFORM_SWEEP_SOURCES),
                )
            )
        ).scalars().all()

        ran_platforms = {
            row[0]
            for row in (
                await db.execute(
                    select(ScrapeRun.platform).where(
                        ScrapeRun.platform.in_(_SOURCE_TO_SCRAPE_RUN_PLATFORM.values()),
                        ScrapeRun.status == "success",
                        func.date(ScrapeRun.started_at) == today,
                    )
                )
            ).all()
        }
        for src, plat in _SOURCE_TO_SCRAPE_RUN_PLATFORM.items():
            if plat in ran_platforms:
                sources_checked.add(src)

        google_items = []
        try:
            google_result = fetch_trending(geo=settings.trends_geo, period="24h", limit=settings.trends_max_per_source)
            google_items = google_result.items
            sources_checked.add("google_trends")
        except Exception as exc:
            logger.warning("run_combined_trend_discovery: google trends gagal: %s", exc)

        youtube_rows = (
            await db.execute(
                select(TrendingTopic).where(
                    TrendingTopic.geo == settings.trends_geo,
                    func.date(TrendingTopic.fetched_at) == today,
                )
            )
        ).scalars().all()
        if youtube_rows:
            sources_checked.add("youtube_trending")

        total_sources = len(sources_checked) or 1

        platform_topic_words: list[set[str]] = []
        for row in platform_rows:
            words = _normalize(row.topic)
            platform_topic_words.append(words)

            confirmed: set[str] = set()
            if isinstance(row.raw_payload, dict):
                confirmed.update(row.raw_payload.get("confirmed_by", []))
            confirmed.add(row.source)

            for g in google_items:
                if _topics_match(words, _normalize(g.title)):
                    confirmed.add("google_trends")
                    break
            for y in youtube_rows:
                if _topics_match(words, _normalize(y.title)):
                    confirmed.add("youtube_trending")
                    break

            confidence = round(len(confirmed) / total_sources, 3)
            prev_payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
            if sorted(confirmed) != sorted(prev_payload.get("confirmed_by", [])) or confidence != prev_payload.get("confidence_score"):
                payload = dict(prev_payload)
                payload["confirmed_by"] = sorted(confirmed)
                payload["confidence_score"] = confidence
                payload["sources_checked_today"] = sorted(sources_checked)
                row.raw_payload = payload
                annotated.append(row.topic)

        def _already_covered(words: set[str]) -> bool:
            return any(_topics_match(words, pw) for pw in platform_topic_words)

        new_items = []
        for g in google_items:
            g_words = _normalize(g.title)
            if not g_words or _already_covered(g_words):
                continue
            for y in youtube_rows:
                if _topics_match(g_words, _normalize(y.title)):
                    confirmed = {"google_trends", "youtube_trending"}
                    confidence = round(len(confirmed) / total_sources, 3)
                    new_items.append(
                        TrendRecommendationItem(
                            topic=g.title,
                            score=confidence,
                            related_accounts=[],
                        )
                    )
                    break

        result = {"created": [], "updated": [], "evicted": [], "rejected": []}
        if new_items:
            body = TrendRecommendationBatchCreate(items=new_items, source="multi_signal_trending")
            result = await submit_recommendations(db, body)
            created = result.get("created", [])

        scrape_run.status = "success"
        scrape_run.videos_fetched = len(platform_rows) + len(google_items) + len(youtube_rows)
        scrape_run.videos_new = len(created)
        if not annotated and not created:
            scrape_run.error_message = "Tidak ada topik yang lolos triangulasi hari ini"
    except Exception as exc:
        logger.error("run_combined_trend_discovery error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        result = {"error": str(exc)}
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    logger.info(
        "run_combined_trend_discovery: sources_checked=%s annotated=%s created=%s",
        sorted(sources_checked), annotated, created,
    )
    return {"sources_checked": sorted(sources_checked), "annotated": annotated, "created": created}
