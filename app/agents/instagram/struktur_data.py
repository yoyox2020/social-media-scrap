"""agent-struktur-data utk Instagram (2026-07-24) -- pola SAMA dgn
Facebook/TikTok (merge/dedupe/normalize/score/AI/save), field mapping
BERBEDA (lihat crawler_client.py utk field yg TERVERIFIKASI, bukan
tebakan): `shortCode` jadi external_id, `caption` jadi content,
`displayUrl` jadi thumbnail (media, BUKAN kosong spt Facebook -- actor
Instagram ini SUDAH terbukti kirim foto asli).

Skor pakai formula SAMA dgn Facebook (log-interaksi, authority dari
followers) krn Instagram jg tidak py views publik utk post foto --
follower diambil dari `post.metadata_.followers` KALAU SUDAH ada
(diisi app/agents/instagram/metadata_backfill.py via SocialCrawl, jalan
terpisah/mingguan) -- fallback authority default 40.0 kalau belum."""
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


def _parse_comment_dt(value) -> datetime | None:
    """Field `timestamp` per-komentar ADA di `latestComments[]` (lihat
    docstring crawler_client.py -- BUKAN "tidak dikirim" spt yg sempat
    diasumsikan salah di sini sebelumnya, ditemukan+diperbaiki 2026-07-24
    saat user tanya kenapa published_at komentar selalu kosong)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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
    now = datetime.now(timezone.utc)
    published_at = item["published_at"] or now
    hours_since = max((now - published_at).total_seconds() / 3600, 0)

    freshness_score = max(0.0, 100.0 - (hours_since * 2))

    interactions = metrics["likes"] + metrics["comments"] * 2
    engagement_score = min(100.0, math.log10(interactions + 1) * 30)

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
                select(Post).where(Post.external_id == item["external_id"], Post.platform == "instagram")
            )
            old_meta = (existing.metadata_ or {}) if existing else {}
            existing_followers = old_meta.get("followers")
            scores = _compute_scores(item, existing_followers)

            prev_topics = old_meta.get("source_topics") or ([old_meta["source_topic"]] if old_meta.get("source_topic") else [])
            source_topics = list(dict.fromkeys([*prev_topics, topic]))
            metadata = {
                "trend_score": scores["trend_score"], "engagement_score": scores["engagement_score"],
                "freshness_score": scores["freshness_score"], "authority_score": scores["authority_score"],
                "followers": existing_followers,  # dijaga apa adanya -- diisi metadata_backfill.py, bukan di sini
                "audience_size": existing_followers,
                "author_full_name": item.get("author_full_name"),
                "source_topic": topic, "source_topics": source_topics,
                "source": "apify_post_scraper",
                "ai_summary": old_meta.get("ai_summary"), "ai_tags": old_meta.get("ai_tags") or [],
            }
            media = [{"type": "image", "url": item["thumbnail"]}] if item.get("thumbnail") else []

            if existing:
                existing.content = item["content"]
                existing.author = item["author"]
                existing.url = item["url"]
                existing.media = media
                existing.metrics = item["metrics"]
                existing.metadata_ = metadata
                existing.raw_data = item["raw_data"]
                existing.collected_at = datetime.now(timezone.utc)
                duplicate_in_db += 1
                post_row = existing
            else:
                post_row = Post(
                    external_id=item["external_id"], platform="instagram", title=None,
                    content=item["content"], author=item["author"], url=item["url"],
                    media=media, metrics=item["metrics"], metadata_=metadata,
                    raw_data=item["raw_data"], published_at=item["published_at"],
                    collected_at=datetime.now(timezone.utc), is_processed=False, is_near_duplicate=False,
                )
                db.add(post_row)
                await db.flush()
                saved_count += 1

            for c in item.get("comments_raw", []):
                external_comment_id = str(c.get("id") or "")
                if not external_comment_id:
                    continue
                existing_comment = await db.scalar(
                    select(Comment).where(Comment.post_id == post_row.id, Comment.external_id == external_comment_id)
                )
                if existing_comment:
                    continue
                db.add(Comment(
                    post_id=post_row.id, external_id=external_comment_id,
                    content=c.get("text") or "",
                    author=c.get("ownerUsername") or "",
                    metadata_={"like_count": c.get("likesCount")},
                    published_at=_parse_comment_dt(c.get("timestamp")),
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
