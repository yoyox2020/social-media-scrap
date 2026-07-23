"""Backfill komentar YouTube yg BELUM PERNAH tersimpan (2026-07-24,
lanjutan permintaan user "ambil semua komentar dan masukkan ke tabel").

KEPUTUSAN PENTING (dijelaskan ke user dulu sebelum dibangun): komentar
TIDAK diambil lewat HP/ADB -- sudah dicoba live, teks komentar YouTube
genuinely TIDAK ADA di accessibility tree (cuma nama author+like count
yg keluar, isi komentarnya sendiri tidak direpresentasikan sama sekali
di node manapun, bukan disembunyikan/NAF, benar2 tidak ada). Dipakai
`fetch_comments_for_video()` yg SUDAH ADA di api_client.py (YouTube Data
API commentThreads.list, teks lengkap+reliable) -- pola insert Comment
disamakan PERSIS dgn yg sudah dipakai agent_struktur_data.py (dedup
external_id, field content/author/metadata_.like_count/published_at)
spy konsisten dgn data yg sudah ada.

Gap NYATA ditemukan (2026-07-24): dari 12.606 post YouTube, 3.114 py
metrics.comments > 0 (artinya YouTube BILANG ada komentar) TAPI 0 baris
tersimpan di tabel `comments` kita -- kemungkinan besar post yg masuk
lewat jalur curl (crawler_client.py) yg historisnya tidak selalu
memanggil fetch_comments_for_video, atau gagal diam2 saat insert awal.

Kuota: `commentThreads.list` bisa sampai 5 panggilan/video (paginasi
MAX_COMMENTS_PER_VIDEO=500, lihat api_client.py) -- JAUH lebih mahal
drpd videos.list/channels.list (1 unit tapi batch 50). BATCH_SIZE
sengaja dibatasi per-run (lihat konstanta di bawah) spy tidak
menghabiskan kuota harian sendirian -- backlog 3.114 post dicicil
beberapa run (jadwal jam-an), BUKAN sekali jalan spt completeness_audit.py."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import BigInteger, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agent_struktur_data import _parse_dt
from app.agents.youtube.api_client import fetch_comments_for_video, looks_like_youtube_key
from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.services.agent_registry.service import get_key_for_agent

# ~300 post/run x rata-rata 1-2 panggilan (kebanyakan video BUKAN
# super-viral, jarang butuh full 5 panggilan paginasi) -- estimasi
# 300-1500 unit/run, aman dijalankan tiap jam berdampingan dgn
# refresh.py (~40 unit) + completeness_audit.py (~500 unit/hari).
BATCH_SIZE = 300


async def backfill_missing_youtube_comments(db: AsyncSession, api_key: str | None = None, limit: int = BATCH_SIZE) -> dict:
    if not api_key:
        key_info = await get_key_for_agent(db, "agent_youtube01")
        if not key_info or not looks_like_youtube_key(key_info.get("api_key")):
            return {"error": "agent_youtube01 tidak punya key YouTube asli", "checked": 0}
        api_key = key_info["api_key"]

    # Post YouTube yg metrics.comments > 0 (YouTube bilang ADA komentar)
    # TAPI belum py baris comments SAMA SEKALI -- prioritas komentar
    # TERBANYAK dulu (paling bernilai utk sentiment/analisis).
    result = await db.execute(
        select(Post)
        .where(
            Post.platform == "youtube",
            Post.metrics["comments"].astext.cast(BigInteger) > 0,
            ~select(Comment.id).where(Comment.post_id == Post.id).exists(),
        )
        .order_by(Post.metrics["comments"].astext.cast(BigInteger).desc())
        .limit(limit)
    )
    posts = result.scalars().all()
    if not posts:
        return {"checked": 0, "posts_backfilled": 0, "comments_saved": 0}

    checked = 0
    posts_backfilled = 0
    comments_saved = 0
    posts_still_empty = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for post in posts:
            checked += 1
            comment_threads = await fetch_comments_for_video(client, api_key, post.external_id)
            if not comment_threads:
                posts_still_empty += 1
                continue

            saved_for_this_post = 0
            for c in comment_threads:
                try:
                    top_comment = c["snippet"]["topLevelComment"]["snippet"]
                    external_comment_id = c["snippet"]["topLevelComment"]["id"]
                except (KeyError, TypeError):
                    continue
                existing = await db.scalar(
                    select(Comment).where(Comment.post_id == post.id, Comment.external_id == external_comment_id)
                )
                if existing:
                    continue
                db.add(Comment(
                    post_id=post.id, external_id=external_comment_id,
                    content=top_comment.get("textDisplay") or "",
                    author=top_comment.get("authorDisplayName") or "",
                    metadata_={"like_count": top_comment.get("likeCount")},
                    published_at=_parse_dt(top_comment.get("publishedAt")),
                ))
                saved_for_this_post += 1

            if saved_for_this_post:
                posts_backfilled += 1
                comments_saved += saved_for_this_post
            await db.commit()

    return {
        "checked": checked,
        "posts_backfilled": posts_backfilled,
        "comments_saved": comments_saved,
        "posts_still_empty_after_fetch": posts_still_empty,
    }
