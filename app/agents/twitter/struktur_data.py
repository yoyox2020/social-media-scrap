"""agent-struktur-data utk Twitter/X (2026-07-24) -- pola SAMA dgn
Facebook/Instagram/Threads (merge/dedupe/normalize/score/save), TAPI
skor pakai formula BERBASIS VIEWS spt TikTok (bukan formula log-interaksi
Facebook/Instagram/Threads) krn Twitter, SEPERTI TikTok, genuinely
expose view count publik per-tweet (field `views`, lihat crawler_client.py)
-- konsisten dgn 124 post Twitter lama yg sudah py `metadata.views` terisi.
MIN_VIEWS_FOR_ENGAGEMENT dipakai sama persis dgn TikTok/YouTube (BUKAN
angka baru yg diputuskan sendiri di sini, reuse konvensi yg SUDAH ada).

Balasan/komentar (2026-07-24, ditambahkan): `comments_raw` dari
crawler_client.py (`_fetch_replies`/`_normalize_reply`) disimpan ke
tabel `comments`, pola SAMA PERSIS dgn Instagram (dedupe by
external_id, `published_at` sudah di-parse crawler_client.py pakai
format Twitter asli, BUKAN None spt bug lama Instagram)."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.domain.comments.models import Comment
from app.domain.posts.models import Post

AGENT_NAME = "agent-struktur-data"
MIN_VIEWS_FOR_ENGAGEMENT = 50


def _dedupe(posts: list[dict]) -> tuple[list[dict], int]:
    seen: dict[str, dict] = {}
    duplicate_count = 0
    for p in posts:
        pid = p.get("external_id")
        if not pid:
            continue
        if pid in seen:
            duplicate_count += 1
            continue
        seen[pid] = p
    return list(seen.values()), duplicate_count


def _compute_scores(item: dict, existing_followers: int | None) -> dict:
    metrics = item["metrics"]
    views = metrics["views"]
    now = datetime.now(timezone.utc)
    published_at = item["published_at"] or now
    hours_since = max((now - published_at).total_seconds() / 3600, 0)

    freshness_score = max(0.0, 100.0 - (hours_since * 2))
    if views < MIN_VIEWS_FOR_ENGAGEMENT:
        engagement_score = 0.0
    else:
        interactions = metrics["likes"] + metrics["comments"] * 2 + metrics["shares"] * 3 + item.get("quotes", 0) * 2
        engagement_score = min(100.0, (interactions / views) * 100)

    authority_score = min(100.0, math.log10(existing_followers + 1) * 12) if existing_followers else 40.0

    trend_score = round((freshness_score * 0.4) + (engagement_score * 0.35) + (authority_score * 0.25), 2)
    return {
        "trend_score": trend_score,
        "engagement_score": round(engagement_score, 2),
        "freshness_score": round(freshness_score, 2),
        "authority_score": round(authority_score, 2),
    }


async def process_and_save(db: AsyncSession, run_id: uuid.UUID, topic: str, posts: list[dict]) -> dict:
    total_before_dedupe = len(posts)
    deduped, duplicate_count = _dedupe(posts)
    await log_activity(
        db, run_id, AGENT_NAME, "merge_dedupe",
        f"Merge {total_before_dedupe} post mentah -> {len(deduped)} unik ({duplicate_count} duplikat dihapus)",
    )

    saved_count = 0
    duplicate_in_db = 0
    failed_count = 0
    try:
        for item in deduped:
            if not item.get("external_id") or not (item.get("content") or item.get("author")):
                failed_count += 1
                continue

            existing = await db.scalar(
                select(Post).where(Post.external_id == item["external_id"], Post.platform == "twitter")
            )
            old_meta = (existing.metadata_ or {}) if existing else {}
            followers = item.get("author_followers") or old_meta.get("followers")
            scores = _compute_scores(item, followers)

            prev_topics = old_meta.get("source_topics") or ([old_meta["source_topic"]] if old_meta.get("source_topic") else [])
            source_topics = list(dict.fromkeys([*prev_topics, topic]))
            metadata = {
                "trend_score": scores["trend_score"], "engagement_score": scores["engagement_score"],
                "freshness_score": scores["freshness_score"], "authority_score": scores["authority_score"],
                "followers": followers, "audience_size": followers,
                "likes": item["metrics"]["likes"], "retweets": item["metrics"]["shares"],
                "comments": item["metrics"]["comments"], "views": item["metrics"]["views"],
                "quotes": item.get("quotes", 0), "source": "apify",
                "source_topic": topic, "source_topics": source_topics,
                "ai_summary": old_meta.get("ai_summary"), "ai_tags": old_meta.get("ai_tags") or [],
            }

            if existing:
                existing.content = item["content"]
                existing.author = item["author"]
                existing.url = item["url"]
                existing.metrics = item["metrics"]
                existing.metadata_ = metadata
                existing.raw_data = item["raw_data"]
                existing.collected_at = datetime.now(timezone.utc)
                duplicate_in_db += 1
                post_row = existing
            else:
                post_row = Post(
                    external_id=item["external_id"], platform="twitter", title=None,
                    content=item["content"], author=item["author"], url=item["url"],
                    metrics=item["metrics"], metadata_=metadata,
                    raw_data=item["raw_data"], published_at=item["published_at"],
                    collected_at=datetime.now(timezone.utc), is_processed=False, is_near_duplicate=False,
                )
                db.add(post_row)
                await db.flush()
                saved_count += 1

            for c in item.get("comments_raw", []):
                external_comment_id = c.get("external_id")
                if not external_comment_id:
                    continue
                existing_comment = await db.scalar(
                    select(Comment).where(Comment.post_id == post_row.id, Comment.external_id == external_comment_id)
                )
                if existing_comment:
                    continue
                db.add(Comment(
                    post_id=post_row.id, external_id=external_comment_id,
                    content=c.get("content") or "",
                    author=c.get("author") or "",
                    metadata_={"like_count": c.get("likes")},
                    published_at=c.get("published_at"),
                ))

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
