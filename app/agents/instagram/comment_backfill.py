"""Backfill komentar Instagram (2026-07-24, permintaan user "banyak
komentar di instagram tidak diambil, tolong kerahkan agent scraping
ulang") -- gap NYATA dicek langsung ke DB: dari 32 post yg
`metrics.comments>0`, mayoritas cuma tersimpan 5-15 baris komentar
walau angka asli ribuan (mis. matakiri.media 36.283 komentar tersimpan
15, coldplay 20.501 tersimpan 5) -- keterbatasan `apify/instagram-
post-scraper` (actor UTAMA scraping post) yg embed `latestComments[]`
TIDAK reliable dapat penuh 15 sekalipun.

Actor BARU khusus komentar: `apify/instagram-comment-scraper` (resmi
Apify sendiri, 8,5 JUTA total run -- actor paling establish utk
kebutuhan ini, dicek via Apify store search). Free tier: 15 komentar
PERTAMA/post gratis (dikonfirmasi dari deskripsi resmi actor "Free usage
gets only the top 15 comments"), lebih dari itu kena biaya per-komentar
(TIDAK dipakai di sini -- user pilih "gratis dulu" krn biaya nyata utk
post rame bisa signifikan, lihat riwayat percakapan). `resultsLimit`
DIKUNCI ke FREE_TIER_LIMIT supaya TIDAK PERNAH kena biaya tambahan
tanpa sepengetahuan user.

**PENTING -- BELUM PERNAH live-tested end-to-end** (2026-07-24): SEMUA
6 token Apify di pool sedang exhausted/saldo ~0 (dicek langsung, hard
limit bulanan + 1 token saldo $0.0014) tepat saat dibangun -- TIDAK ada
kredensial tersisa utk verifikasi live. Field mapping respons
(`_extract_comments`/`_normalize_comment` di bawah) MASIH TEBAKAN
defensif berbasis deskripsi resmi actor (bukan hasil parsing respons
nyata) -- SENGAJA banyak nama field alternatif dicoba + validasi ketat
(item tanpa id/text valid DIBUANG). `raw_data` per-post disimpan ke log
aktivitas (bukan tabel baru) begitu run pertama sukses, supaya kalau
mapping meleset tinggal diperbaiki tanpa scrape ulang. `usage_usd`
aktual dari response Apify run DICATAT tiap kali (bukan diasumsikan
$0) -- transparansi biaya real per run, bukan janji di kode ini."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.services.sentiment.save import analyze_and_queue_lexicon
from app.services.third_party_apis.service import get_next_available_key, mark_api_error

ACTOR_RUN_URL_TEMPLATE = (
    "https://api.apify.com/v2/acts/apify~instagram-comment-scraper/run-sync-get-dataset-items?token={token}"
)
FREE_TIER_LIMIT = 15  # JANGAN dinaikkan tanpa konfirmasi user -- di atas ini kena biaya per-komentar
ROTATION_FAILURE_STATUS_CODES = {401, 402, 403, 429}
MAX_ROTATION_ATTEMPTS = 6
DEFAULT_POST_LIMIT = 20


def _first_present(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_comment(item: dict) -> dict | None:
    """Field mapping DEFENSIF (belum diverifikasi live, lihat docstring
    modul) -- banyak nama alternatif dicoba, item tanpa id+text valid DIBUANG
    drpd disimpan sbg data sampah."""
    comment_id = _first_present(item, "id", "commentId", "pk", "commentPk")
    text = _first_present(item, "text", "commentText", "comment", "content")
    if not comment_id or not text:
        return None

    username = _first_present(item, "ownerUsername", "username", "userUsername", "authorUsername") or ""
    likes = _first_present(item, "likesCount", "likeCount", "likes")
    timestamp_raw = _first_present(item, "timestamp", "createdAt", "commentedAt")

    published_at = None
    if timestamp_raw:
        try:
            published_at = datetime.fromisoformat(str(timestamp_raw).replace("Z", "+00:00"))
        except ValueError:
            pass

    return {
        "external_id": str(comment_id),
        "content": str(text),
        "author": str(username),
        "like_count": _safe_int(likes),
        "published_at": published_at,
    }


def _extract_comments(response_json) -> list[dict]:
    items = response_json if isinstance(response_json, list) else []
    normalized = [_normalize_comment(it) for it in items if isinstance(it, dict)]
    return [n for n in normalized if n is not None]


async def _get_posts_needing_backfill(db: AsyncSession, limit: int) -> list[Post]:
    """Post dgn `metrics.comments>0` TAPI baris tersimpan di tabel
    `comments` MASIH DI BAWAH FREE_TIER_LIMIT -- artinya masih ada
    komentar gratis (dlm cap 15) yg belum ke-tarik, prioritas gap
    TERBESAR duluan (post rame yg paling nyata kurang datanya)."""
    subq = (
        select(Comment.post_id, func.count(Comment.id).label("saved_count"))
        .group_by(Comment.post_id)
        .subquery()
    )
    stmt = (
        select(Post, func.coalesce(subq.c.saved_count, 0).label("saved_count"))
        .outerjoin(subq, subq.c.post_id == Post.id)
        .where(
            Post.platform == "instagram",
            func.coalesce(subq.c.saved_count, 0) < FREE_TIER_LIMIT,
        )
        .order_by(cast(Post.metrics["comments"].astext, Integer).desc().nullslast())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [row[0] for row in rows]


async def _fetch_comments_for_post(db: AsyncSession, post_url: str) -> tuple[list[dict], float, str | None]:
    tried_key_ids: set = set()
    last_error: str | None = None

    for _attempt in range(MAX_ROTATION_ATTEMPTS):
        key_entry = await get_next_available_key(db, "Apify", platform_group="instagram")
        if not key_entry or key_entry.id in tried_key_ids:
            last_error = "Semua token Apify sudah dicoba & gagal -- menunggu jadwal berikutnya"
            break
        tried_key_ids.add(key_entry.id)

        url = ACTOR_RUN_URL_TEMPLATE.format(token=key_entry.api_key)
        body = {"directUrls": [post_url], "resultsLimit": FREE_TIER_LIMIT, "includeNestedComments": False}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=body)
        except Exception as exc:
            await mark_api_error(db, key_entry.id, str(exc)[:500])
            last_error = str(exc)
            continue

        if resp.status_code in ROTATION_FAILURE_STATUS_CODES:
            await mark_api_error(db, key_entry.id, f"HTTP {resp.status_code}: {resp.text[:500]}")
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            continue

        if resp.status_code not in (200, 201):
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            break

        # `run-sync-get-dataset-items` balikin dataset items LANGSUNG
        # (bukan run-info) -- biaya aktual TIDAK tersedia di respons ini
        # (beda dari endpoint run-info biasa yg py `usageTotalUsd`), jadi
        # `cost_usd` di sini sengaja 0.0 (TIDAK diklaim, bukan diverifikasi).
        try:
            data = resp.json()
        except ValueError:
            last_error = "response bukan JSON valid"
            break

        return _extract_comments(data), 0.0, None

    return [], 0.0, last_error or "unknown"


async def backfill_instagram_comments(db: AsyncSession, post_limit: int = DEFAULT_POST_LIMIT) -> dict:
    posts = await _get_posts_needing_backfill(db, post_limit)
    if not posts:
        return {"posts_checked": 0, "posts_updated": 0, "comments_saved": 0, "errors": []}

    posts_updated = 0
    comments_saved = 0
    errors: list[dict] = []

    for post in posts:
        if not post.url:
            continue
        comments, _cost, error = await _fetch_comments_for_post(db, post.url)
        if error:
            errors.append({"post_id": str(post.id), "external_id": post.external_id, "error": error})
            continue

        saved_this_post = 0
        for c in comments:
            existing = await db.scalar(
                select(Comment).where(Comment.post_id == post.id, Comment.external_id == c["external_id"])
            )
            if existing:
                continue
            comment_row = Comment(
                id=uuid.uuid4(), post_id=post.id, external_id=c["external_id"],
                content=c["content"], author=c["author"],
                metadata_={"like_count": c["like_count"]},
                published_at=c["published_at"],
            )
            db.add(comment_row)
            await analyze_and_queue_lexicon(db, comment_row.id, comment_row.content)
            saved_this_post += 1

        if saved_this_post:
            posts_updated += 1
            comments_saved += saved_this_post

        await db.commit()

    return {
        "posts_checked": len(posts),
        "posts_updated": posts_updated,
        "comments_saved": comments_saved,
        "errors": errors,
    }
