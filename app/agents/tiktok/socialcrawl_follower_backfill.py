"""Backfill follower count akun TikTok via SocialCrawl (2026-07-24,
permintaan user "masukkan ini ke list dan gunakan untuk melengkapi
datanya, sisakan satu agent untuk mengawasinya" -- key AWALNYA
didaftarkan ke agent_youtube05, TAPI itu SALAH -- agen platform TikTok
harusnya dipegang agent_tiktok0X sendiri, bukan pinjam nama agent
platform lain (ditemukan+diperbaiki 2026-07-24 saat audit "apakah tiap
platform sudah py agent update sendiri"). Sekarang: `agent_tiktok03`
(slot kosong, key-nya SALINAN persis dari SocialCrawl yg sama -- akun
SocialCrawl cuma 1, kredit tetap dibagi dgn Instagram/agent_instagram02,
BUKAN kuota terpisah).

Gap NYATA (dicek ke DB sebelum dibangun): 111 post TikTok, 82 author
unik, 0 yg py data follower tersimpan -- field ini genuinely tidak ada
di pipeline TikTok manapun sebelumnya (beda dari YouTube yg subscriber
count-nya baru ditambahkan 2026-07-24 juga, lihat completeness_audit.py).

KREDIT SANGAT TERBATAS (2026-07-24, verified live): akun free tier cuma
100 kredit TOTAL (1 kredit/panggilan /v1/tiktok/profile). 82 author =
82 kredit -- SENGAJA TIDAK dijadwalkan agresif (beda dari task lain di
project ini yg jam-an) krn budget habis sekali jalan. `MIN_CREDIT_BUFFER`
sbg jaring pengaman berhenti PALING TIDAK meninggalkan sisa, bukan
dipaksa sampai 0 lalu semua panggilan berikutnya gagal 402.

BUG NYATA ditemukan di response mereka (self-reported via `_warnings`):
`data.author.likes_count` bisa NEGATIF (overflow, mis. -599236476 utk
akun 159 JUTA follower) -- field itu TIDAK dipakai sama sekali di sini
(cuma `followers`), tapi validasi `>= 0` tetap dipasang sbg jaring
pengaman kalau field lain kena bug serupa nanti.

BUG KE-2 ditemukan+diperbaiki 2026-07-24 (audit "apakah semua sudah
smooth"): follower yg baru didapat CUMA disimpan mentah, `trend_score`/
`authority_score` yg SUDAH tersimpan dari waktu scrape awal (biasanya
authority default 40.0 krn follower belum diketahui saat itu) TIDAK
PERNAH dihitung ULANG -- skor jadi basi/salah walau data follower-nya
sendiri sudah benar. Sekarang: SETIAP kali follower berhasil diterapkan
ke 1 post, skornya langsung dihitung ulang pakai `_compute_scores()`
yg SAMA PERSIS dgn app/agents/tiktok/struktur_data.py (di-import
langsung, bukan disalin, supaya formula TIDAK PERNAH bisa beda)."""
from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tiktok.struktur_data import _compute_scores
from app.domain.posts.models import Post
from app.services.agent_registry.service import get_key_for_agent

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev/v1"
# Sengaja jauh di bawah limit riil (99 kredit tersisa saat ditulis) --
# berhenti lebih awal drpd mepet 0, sisa kredit utk author BARU yg
# muncul nanti (bukan cuma sekali backfill lalu macet permanen).
MIN_CREDIT_BUFFER = 5
DEFAULT_LIMIT = 90


async def _get_authors_missing_followers(db: AsyncSession, limit: int) -> list[str]:
    result = await db.execute(
        select(Post.author)
        .where(
            Post.platform == "tiktok",
            Post.author.is_not(None),
            Post.author != "",
            Post.metadata_["author_fans"].astext.is_(None),
        )
        .distinct()
        .limit(limit)
    )
    return [row[0] for row in result.all() if row[0]]


async def backfill_tiktok_author_followers(db: AsyncSession, api_key: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    if not api_key:
        key_info = await get_key_for_agent(db, "agent_tiktok03")
        if not key_info or not key_info.get("api_key"):
            return {"error": "agent_tiktok03 belum punya key SocialCrawl", "checked": 0}
        api_key = key_info["api_key"]

    authors = await _get_authors_missing_followers(db, limit)
    if not authors:
        return {"checked": 0, "authors_updated": 0, "posts_updated": 0, "credits_remaining": None}

    checked = 0
    authors_updated = 0
    posts_updated = 0
    stopped_low_credit = False
    credits_remaining = None

    async with httpx.AsyncClient(timeout=20.0) as client:
        for author in authors:
            if credits_remaining is not None and credits_remaining < MIN_CREDIT_BUFFER:
                stopped_low_credit = True
                break

            checked += 1
            try:
                resp = await client.get(
                    f"{SOCIALCRAWL_BASE_URL}/tiktok/profile", params={"handle": author},
                    headers={"x-api-key": api_key},
                )
            except Exception:
                continue

            remaining_header = resp.headers.get("x-credits-remaining")
            if remaining_header is not None:
                try:
                    credits_remaining = int(remaining_header)
                except ValueError:
                    pass

            if resp.status_code != 200:
                continue
            data = resp.json().get("data", {}).get("author", {})
            followers = data.get("followers")
            # Jaring pengaman: API ini TERBUKTI py bug overflow di field
            # lain (likes_count negatif) -- followers negatif/bukan angka
            # dianggap TIDAK VALID, tidak disimpan drpd nyimpen sampah.
            if not isinstance(followers, int) or followers < 0:
                continue

            result = await db.execute(
                select(Post).where(Post.platform == "tiktok", Post.author == author)
            )
            posts = result.scalars().all()
            if not posts:
                continue
            for post in posts:
                meta = dict(post.metadata_ or {})
                # author_fans (BUKAN author_followers) -- disamakan dgn nama
                # field yg SUDAH dipakai app/agents/tiktok/struktur_data.py
                # (ditemukan 2026-07-24: 2 tempat sempat pakai nama beda utk
                # data yg sama). audience_size = alias seragam lintas platform.
                meta["author_fans"] = followers
                meta["audience_size"] = followers
                meta["author_verified"] = bool(data.get("verified"))

                # Hitung ULANG skor pakai follower yg BARU didapat -- kalau
                # tidak, trend_score/authority_score tetap basi (biasanya
                # authority default 40.0 dari scrape awal saat follower
                # belum diketahui), walau data follower-nya sendiri sudah
                # benar (bug nyata ditemukan 2026-07-24).
                scores = _compute_scores({
                    "metrics": post.metrics or {}, "published_at": post.published_at, "author_fans": followers,
                })
                meta.update(scores)

                post.metadata_ = meta
                posts_updated += 1
            authors_updated += 1
            await db.commit()

    return {
        "checked": checked,
        "authors_updated": authors_updated,
        "posts_updated": posts_updated,
        "credits_remaining": credits_remaining,
        "stopped_low_credit": stopped_low_credit,
    }
