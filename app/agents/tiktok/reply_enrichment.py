"""Ambil BALASAN komentar TikTok (2026-07-23/24). Provider Apify:
`xtracto/tiktok-comments-scraper` (GANTI dari `automation-lab/...` yg
konsisten gagal ABORTED -- xtracto TERBUKTI live: 20 komentar+15 item
py balasan dari 1 video, ~17 detik/panggilan jauh lebih cepat dari
~100 detik aktor lama). Field `_replies` (array nested di tiap
komentar top-level) + `reply_id` (= `cid` milik induknya) dipetakan ke
`Comment.parent_comment_id` yg SUDAH ADA di schema.

Pemilihan post (2026-07-24, DIBALIK dari versi awal atas permintaan
user "bukan yang paling sedikit tapi maksimalkan semua pengambilan
komentar dimulai dari yang terviral terbanyak views dan semua harus
terupdate"): urut `metrics.views` TERBESAR dulu -- post PALING VIRAL
diproses PALING AWAL tiap batch (bukan post "murah" komentar-sedikit
spt versi awal), krn prioritas user adalah komentar dari konten paling
berdampak duluan. BATCH_SIZE tetap besar ("unlimited") & 1 panggilan
cuma ~17-55 detik, jadi post non-viral (views kecil, di urutan bawah)
tetap kebagian giliran dlm batch yg sama selama masih di bawah
BATCH_SIZE -- semua post TikTok yg belum pernah diambil replies-nya
tercakup, bukan cuma yg viral.

DIPISAH jadi task terjadwal SENDIRI (bukan ditempel ke pipeline
discovery) -- meski aktor baru ini jauh lebih cepat, tetap best-practice
biar tidak mencampur "cari topik baru" dgn "lengkapi komentar lama"."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tiktok.crawler_client import is_valid_tiktok_id
from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.services.third_party_apis.service import get_next_available_key, mark_api_error

BATCH_SIZE = 200  # "unlimited" praktis -- cover total post TikTok saat ini (111) + pertumbuhan wajar dlm 1 jam
# Verified live 2026-07-24: max_results=5000 balikin 412 komentar+291
# balasan (703 total) dlm 55 detik -- BUKAN aktor yg membatasi, itu
# semua yg TERSEDIA di video itu. Permintaan user "tidak dibatasin
# untuk akun yang viral" -- dipasang tinggi (jauh di atas video TikTok
# manapun yg wajar) supaya praktis "ambil semua", bukan angka
# sembarangan yg gampang kepotong utk post yg genuinely viral.
MAX_COMMENTS_PER_VIDEO = 10000
MAX_REPLIES_PER_COMMENT = 200
ACTOR_URL_TEMPLATE = "https://api.apify.com/v2/acts/xtracto~tiktok-comments-scraper/run-sync-get-dataset-items?token={token}"


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


async def _select_most_viral_unenriched_posts(db: AsyncSession, limit: int = BATCH_SIZE) -> list[Post]:
    result = await db.execute(select(Post).where(Post.platform == "tiktok"))
    posts = result.scalars().all()
    candidates = [p for p in posts if not (p.metadata_ or {}).get("replies_fetched_at")]
    candidates.sort(key=lambda p: (p.metrics or {}).get("views", 0), reverse=True)
    return candidates[:limit]


async def _fetch_comments_with_replies(db: AsyncSession, video_id: str) -> tuple[list[dict], str | None]:
    key_entry = await get_next_available_key(db, "Apify")
    if not key_entry:
        return [], "Tidak ada token Apify available"

    body = {
        "video_id": video_id,
        "include_replies": True,
        "max_replies_per_comment": MAX_REPLIES_PER_COMMENT,
        "max_results": MAX_COMMENTS_PER_VIDEO,
    }
    url = ACTOR_URL_TEMPLATE.format(token=key_entry.api_key)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body)
        if resp.status_code not in (200, 201):
            if resp.status_code in (401, 402, 403, 429):
                await mark_api_error(db, key_entry.id, f"HTTP {resp.status_code}: {resp.text[:500]}")
            return [], f"HTTP {resp.status_code}: {resp.text[:300]}"
        data = resp.json()
        return data if isinstance(data, list) else [], None
    except Exception as exc:
        await mark_api_error(db, key_entry.id, str(exc)[:500])
        return [], str(exc)


async def enrich_viral_posts_with_replies(db: AsyncSession, limit: int = BATCH_SIZE) -> dict:
    posts = await _select_most_viral_unenriched_posts(db, limit)
    if not posts:
        return {"processed": 0, "comments_saved": 0, "replies_saved": 0}

    total_comments = 0
    total_replies = 0
    processed = 0

    for post in posts:
        items, error = await _fetch_comments_with_replies(db, post.external_id)
        # dict(...) BUKAN cuma `post.metadata_ or {}` -- kalau metadata_
        # sudah truthy, `or {}` balikin OBJEK YANG SAMA (referensi).
        # Mutasi in-place lalu assign balik ke atribut yg SAMA tidak
        # selalu ke-detect SQLAlchemy sbg perubahan (kolom JSON polos) --
        # replies_fetched_at DIAM-DIAM GAGAL tersimpan, post yg sama
        # diproses ULANG terus tiap jam (ditemukan 2026-07-24, live).
        meta = dict(post.metadata_ or {})
        meta["replies_fetched_at"] = datetime.now(timezone.utc).isoformat()

        if error:
            meta["replies_fetch_error"] = error
            post.metadata_ = meta
            await db.commit()
            processed += 1
            continue
        meta.pop("replies_fetch_error", None)

        for item in items:
            cid = str(item.get("cid") or "")
            if not is_valid_tiktok_id(cid):
                continue
            user = item.get("user") or {}

            existing = await db.scalar(
                select(Comment).where(Comment.post_id == post.id, Comment.external_id == cid)
            )
            if existing:
                parent_id = existing.id
            else:
                c = Comment(
                    post_id=post.id, external_id=cid, content=item.get("text") or "",
                    author=user.get("unique_id") or user.get("nickname") or "",
                    metadata_={
                        "like_count": item.get("digg_count", 0), "author_id": user.get("uid"),
                        "reply_count": item.get("reply_comment_total", 0),
                    },
                    published_at=_parse_dt(item.get("create_time")),
                )
                db.add(c)
                await db.flush()
                parent_id = c.id
                total_comments += 1

            for reply in (item.get("_replies") or []):
                r_cid = str(reply.get("cid") or "")
                if not is_valid_tiktok_id(r_cid):
                    continue
                r_existing = await db.scalar(
                    select(Comment).where(Comment.post_id == post.id, Comment.external_id == r_cid)
                )
                if r_existing:
                    continue
                r_user = reply.get("user") or {}
                db.add(Comment(
                    post_id=post.id, parent_comment_id=parent_id, external_id=r_cid,
                    content=reply.get("text") or "",
                    author=r_user.get("unique_id") or r_user.get("nickname") or "",
                    metadata_={"like_count": reply.get("digg_count", 0), "author_id": r_user.get("uid")},
                    published_at=_parse_dt(reply.get("create_time")),
                ))
                total_replies += 1

        post.metadata_ = meta
        await db.commit()
        processed += 1

    return {"processed": processed, "comments_saved": total_comments, "replies_saved": total_replies}
