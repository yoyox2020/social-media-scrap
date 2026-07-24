"""agent-struktur-data utk News (2026-07-24) -- pola SAMA dgn platform
lain (merge/dedupe/normalize/score/save) TAPI skor DIADAPTASI krn artikel
berita TIDAK PUNYA engagement publik sama sekali (likes/comments/shares/
views SELALU 0, dikonfirmasi dari 184 artikel News lama) -- BUKAN kasus
"data belum lengkap" spt follower yg belum ke-backfill, tapi genuinely
tidak ada sinyal itu utk jenis konten ini.

Formula trend_score DIBOBOT ULANG (freshness 70% + authority 30%,
engagement_score TETAP DIHITUNG APA ADANYA = 0.0, TIDAK disembunyikan)
drpd pura2 py sinyal engagement yg sebenarnya tidak ada."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.domain.posts.models import Post

AGENT_NAME = "agent-struktur-data"
AUTHORITY_SCORE_DEFAULT = 40.0


def _dedupe(articles: list[dict]) -> tuple[list[dict], int]:
    seen: dict[str, dict] = {}
    duplicate_count = 0
    for a in articles:
        aid = a.get("external_id")
        if not aid:
            continue
        if aid in seen:
            duplicate_count += 1
            continue
        seen[aid] = a
    return list(seen.values()), duplicate_count


def _compute_scores(item: dict) -> dict:
    now = datetime.now(timezone.utc)
    published_at = item["published_at"] or now
    hours_since = max((now - published_at).total_seconds() / 3600, 0)

    freshness_score = max(0.0, 100.0 - (hours_since * 2))
    engagement_score = 0.0  # artikel berita tidak punya engagement publik
    authority_score = AUTHORITY_SCORE_DEFAULT

    trend_score = round((freshness_score * 0.7) + (authority_score * 0.3), 2)
    return {
        "trend_score": trend_score,
        "engagement_score": engagement_score,
        "freshness_score": round(freshness_score, 2),
        "authority_score": authority_score,
    }


async def process_and_save(db: AsyncSession, run_id: uuid.UUID, topic: str, articles: list[dict]) -> dict:
    total_before_dedupe = len(articles)
    deduped, duplicate_count = _dedupe(articles)
    await log_activity(
        db, run_id, AGENT_NAME, "merge_dedupe",
        f"Merge {total_before_dedupe} artikel mentah -> {len(deduped)} unik ({duplicate_count} duplikat dihapus)",
    )

    saved_count = 0
    duplicate_in_db = 0
    failed_count = 0
    try:
        for item in deduped:
            if not item.get("external_id") or not item.get("content") or not item.get("url"):
                failed_count += 1
                continue

            existing = await db.scalar(
                select(Post).where(Post.external_id == item["external_id"], Post.platform == "news")
            )
            old_meta = (existing.metadata_ or {}) if existing else {}
            scores = _compute_scores(item)

            prev_topics = old_meta.get("source_topics") or ([old_meta["source_topic"]] if old_meta.get("source_topic") else [])
            source_topics = list(dict.fromkeys([*prev_topics, topic]))
            metadata = {
                "trend_score": scores["trend_score"], "engagement_score": scores["engagement_score"],
                "freshness_score": scores["freshness_score"], "authority_score": scores["authority_score"],
                "title": item.get("title"), "image_url": item.get("image_url"), "source": "firecrawl",
                "source_topic": topic, "source_topics": source_topics,
                "ai_summary": old_meta.get("ai_summary"), "ai_tags": old_meta.get("ai_tags") or [],
            }

            if existing:
                existing.content = item["content"]
                existing.author = item.get("author")
                existing.title = item.get("title")
                existing.url = item["url"]
                existing.metrics = item["metrics"]
                existing.metadata_ = metadata
                existing.raw_data = item["raw_data"]
                existing.collected_at = datetime.now(timezone.utc)
                duplicate_in_db += 1
            else:
                post_row = Post(
                    external_id=item["external_id"], platform="news", title=item.get("title"),
                    content=item["content"], author=item.get("author"), url=item["url"],
                    metrics=item["metrics"], metadata_=metadata,
                    raw_data=item["raw_data"], published_at=item["published_at"],
                    collected_at=datetime.now(timezone.utc), is_processed=False, is_near_duplicate=False,
                )
                db.add(post_row)
                saved_count += 1

        await db.commit()
    except Exception as exc:
        await db.rollback()
        await log_activity(db, run_id, AGENT_NAME, "save_failed", f"Rollback -- gagal simpan: {exc}", level="error")
        raise

    await log_activity(
        db, run_id, AGENT_NAME, "save_done",
        f"Tersimpan: {saved_count} baru, {duplicate_in_db} diperbarui (sudah ada sebelumnya), {failed_count} gagal validasi",
    )

    return {
        "total_post": len(deduped),
        "saved_to_database": saved_count,
        "duplicate_removed": duplicate_count + duplicate_in_db,
        "failed": failed_count,
    }
