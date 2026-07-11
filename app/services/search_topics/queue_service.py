"""
Smart Search -- pemrosesan antrian tier-3 yang di-KONFIRMASI user
(confirm_third_party=true di POST /search/topics atau
POST /search/topics/{id}/search), dijalankan di BACKGROUND (Celery task
workers.search_topics.process_confirmed_queue, lihat
app/workers/search_topics_worker.py) -- BUKAN menunggu di request HTTP
yang sama.

KENAPA: satu panggilan Apify/Firecrawl bisa 15-60+ detik. Kalau topik
punya beberapa keyword yang semuanya perlu tier-3, memproses semuanya
synchronous di dalam satu request HTTP gampang melebihi timeout
browser/reverse-proxy -- persis gejala "kalau satu keyword ketemu, tapi
kalau sudah banyak keyword tidak bisa" yang dilaporkan user. Endpoint
sekarang cuma DAFTARKAN item ke antrian lalu langsung balas "queued",
proses sebenarnya jalan di sini SATU PER SATU berurutan (bukan paralel)
persis seperti diminta user, mirip pola run_daily_search_topic_rescan()
di rescan_service.py tapi dipicu on-demand bukan jadwal harian.

Progress dicek user lewat GET /search/topics/{id} yang SUDAH ADA (posts
baru otomatis kehitung begitu tersimpan) -- TIDAK perlu endpoint status
baru. `SearchTopicKeyword.last_rescanned_at` di-set SAAT MULAI memproses
tiap item (bukan cuma saat selesai) supaya frontend bisa beda-in
"sedang diproses" vs "genuinely belum pernah dicoba".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.search_topics.models import SearchTopicKeyword
from app.services.search_topics import discovery

logger = logging.getLogger(__name__)


async def run_confirmed_search_queue(
    db: AsyncSession,
    items: list[dict[str, Any]],
    topic_id: str | None,
) -> dict:
    """Proses `items` (masing2 `{keyword_text, platform, source_tag, limit}`)
    SATU PER SATU berurutan. `topic_id` opsional -- kalau ada, update
    `SearchTopicKeyword.last_rescanned_at` per keyword yang diproses
    (kosong kalau pencarian ad-hoc, save_topic=false, tidak ada apa pun
    utk di-update)."""
    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="search_topics_confirmed_queue", platform="search_topics", api_source="internal",
        status="running", triggered_by="user_confirm", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    processed = 0
    errors: list[str] = []

    try:
        for item in items:
            kw_text = item["keyword_text"]
            platform = item["platform"]
            source_tag = item.get("source_tag")
            limit = item.get("limit", 10)

            if topic_id:
                await db.execute(
                    update(SearchTopicKeyword)
                    .where(
                        SearchTopicKeyword.topic_id == topic_id,
                        SearchTopicKeyword.keyword_text == kw_text,
                    )
                    .values(last_rescanned_at=datetime.now(timezone.utc))
                )
                await db.commit()

            try:
                result = await discovery.run_tier3_discovery(
                    db, platform, kw_text, max_results=limit, source_tag=source_tag,
                )
                if result.get("error"):
                    errors.append(f"{kw_text}/{platform}: {result['error']}")
            except Exception as exc:
                logger.error("run_confirmed_search_queue: gagal utk %s/%s: %s", kw_text, platform, exc)
                errors.append(f"{kw_text}/{platform}: {exc}")

            processed += 1

        scrape_run.status = "success"
        scrape_run.videos_fetched = processed
        if errors:
            scrape_run.error_message = "; ".join(errors)[:1000]
    except Exception as exc:
        logger.error("run_confirmed_search_queue error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    result = {"processed": processed, "total": len(items), "errors": errors[:10]}
    logger.info("run_confirmed_search_queue: %s", result)
    return result
