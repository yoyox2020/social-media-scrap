"""Ambil BALASAN komentar TikTok (2026-07-23, permintaan user "cari
provider lain" utk sub-komentar). Provider Apify BARU (aktor
`automation-lab/tiktok-comments-scraper`, TERPISAH dari aktor pencarian
video `clockworks/tiktok-scraper`), TERBUKTI live: 20 komentar + 96
balasan dari 1 video, field `parentCommentId` cocok persis ke
`Comment.parent_comment_id` yg SUDAH ADA di schema (dibangun 2026-07-20
utk Threads, generik lintas platform).

DIPISAH jadi task terjadwal SENDIRI (bukan ditempel ke pipeline
discovery) krn ~100 detik/panggilan -- kalau ditempel ke tiap topik
pencarian (bisa 20 topik/jam), total waktu bisa berjam-jam & bikin
jadwal 1-jam auto-crawl molor parah.

Pemilihan video "viral" (permintaan user "yang viral saja... jumlah
views+like banyak... sy tidak mau berpikir, mau yang PALING viral")
TIDAK pakai angka ambang tetap -- murni urut views+likes TERTINGGI dari
post yg BELUM PERNAH diambil balasannya (metadata.replies_fetched_at
IS NULL). Otomatis yg paling viral duluan, video yg sudah diproses
tidak diulang, dibatasi batch kecil per run demi waktu/biaya
terkendali."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tiktok.crawler_client import is_valid_tiktok_id
from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.services.third_party_apis.service import get_next_available_key, mark_api_error

BATCH_SIZE = 10
MAX_COMMENTS_PER_VIDEO = 100  # permintaan user "max comment harusnya 100 saja"
MAX_REPLIES_PER_COMMENT = 20
ACTOR_URL_TEMPLATE = "https://api.apify.com/v2/acts/automation-lab~tiktok-comments-scraper/run-sync-get-dataset-items?token={token}"


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _select_viral_unenriched_posts(db: AsyncSession, limit: int = BATCH_SIZE) -> list[Post]:
    result = await db.execute(select(Post).where(Post.platform == "tiktok"))
    posts = result.scalars().all()
    candidates = [p for p in posts if not (p.metadata_ or {}).get("replies_fetched_at")]
    candidates.sort(
        key=lambda p: ((p.metrics or {}).get("views", 0), (p.metrics or {}).get("likes", 0)),
        reverse=True,
    )
    return candidates[:limit]


async def _fetch_comments_with_replies(db: AsyncSession, video_url: str) -> tuple[list[dict], str | None]:
    key_entry = await get_next_available_key(db, "Apify")
    if not key_entry:
        return [], "Tidak ada token Apify available"

    body = {
        "videoUrls": [video_url],
        "maxCommentsPerVideo": MAX_COMMENTS_PER_VIDEO,
        "includeReplies": True,
        "maxRepliesPerComment": MAX_REPLIES_PER_COMMENT,
    }
    url = ACTOR_URL_TEMPLATE.format(token=key_entry.api_key)
    try:
        # 280s (bukan 180s) -- diverifikasi live 2026-07-23, 1 run
        # kadang perlu >180s tergantung jumlah komentar+balasan, 180s
        # sempat timeout PADAHAL actor-nya masih jalan normal.
        async with httpx.AsyncClient(timeout=280.0) as client:
            resp = await client.post(url, json=body)
        if resp.status_code not in (200, 201):
            if resp.status_code in (401, 402, 403, 429):
                await mark_api_error(db, key_entry.id, f"HTTP {resp.status_code}: {resp.text[:500]}")
            return [], f"HTTP {resp.status_code}: {resp.text[:300]}"
        return resp.json(), None
    except Exception as exc:
        await mark_api_error(db, key_entry.id, str(exc)[:500])
        return [], str(exc)


async def enrich_viral_posts_with_replies(db: AsyncSession, limit: int = BATCH_SIZE) -> dict:
    posts = await _select_viral_unenriched_posts(db, limit)
    if not posts:
        return {"processed": 0, "comments_saved": 0, "replies_saved": 0}

    total_comments = 0
    total_replies = 0
    processed = 0

    for post in posts:
        items, error = await _fetch_comments_with_replies(db, post.url)
        meta = post.metadata_ or {}
        meta["replies_fetched_at"] = datetime.now(timezone.utc).isoformat()

        if error:
            meta["replies_fetch_error"] = error
            post.metadata_ = meta
            await db.commit()
            processed += 1
            continue
        meta.pop("replies_fetch_error", None)

        top_level = [it for it in items if not it.get("isReply") and is_valid_tiktok_id(str(it.get("id") or ""))]
        replies = [it for it in items if it.get("isReply") and is_valid_tiktok_id(str(it.get("id") or ""))]

        id_map: dict[str, uuid.UUID] = {}
        for it in top_level:
            ext_id = str(it["id"])
            existing = await db.scalar(
                select(Comment).where(Comment.post_id == post.id, Comment.external_id == ext_id)
            )
            if existing:
                id_map[ext_id] = existing.id
                continue
            c = Comment(
                post_id=post.id, external_id=ext_id, content=it.get("text") or "",
                author=it.get("author") or "",
                metadata_={
                    "like_count": it.get("likes", 0), "author_id": it.get("authorId"),
                    "avatar_url": it.get("authorAvatarUrl"), "reply_count": it.get("replies", 0),
                },
                published_at=_parse_dt(it.get("createdAt")),
            )
            db.add(c)
            await db.flush()
            id_map[ext_id] = c.id
            total_comments += 1

        for it in replies:
            ext_id = str(it["id"])
            parent_ext_id = str(it.get("parentCommentId") or "")
            parent_id = id_map.get(parent_ext_id)
            if not parent_id:
                # Induk tidak ketemu (mis. di luar batch maxCommentsPerVideo)
                # -- skip drpd salah kait ke induk yg keliru.
                continue
            existing = await db.scalar(
                select(Comment).where(Comment.post_id == post.id, Comment.external_id == ext_id)
            )
            if existing:
                continue
            db.add(Comment(
                post_id=post.id, parent_comment_id=parent_id, external_id=ext_id,
                content=it.get("text") or "", author=it.get("author") or "",
                metadata_={
                    "like_count": it.get("likes", 0), "author_id": it.get("authorId"),
                    "avatar_url": it.get("authorAvatarUrl"),
                },
                published_at=_parse_dt(it.get("createdAt")),
            ))
            total_replies += 1

        post.metadata_ = meta
        await db.commit()
        processed += 1

    return {"processed": processed, "comments_saved": total_comments, "replies_saved": total_replies}
