"""Refresh statistik post YouTube LAMA (2026-07-23, permintaan user
"perlu agent buat update data youtube") -- BUKAN agent baru, cuma tugas
terjadwal kecil yg reuse `_compute_scores` dari agent_struktur_data.py
(SATU formula, tidak dobel-tulis) supaya angka trend_score dst tetap
konsisten dgn jalur discovery.

Prioritas: post yg PALING LAMA tidak disentuh (collected_at ASC) --
`collected_at` sudah otomatis ke-bump tiap kali post disentuh proses
apa pun (discovery baru MAUPUN refresh ini sendiri), jadi urutan ini
genuinely "paling basi duluan".

Kenapa ini AMAN dari sisi kuota (beda dari search.list yg 100 unit):
`videos.list`/`channels.list` cuma 1 unit/panggilan DAN bisa batch 50
ID sekaligus -- refresh 1000 post cuma makan puluhan unit total, hampir
tidak bersaing dgn kuota search.list yg dipakai auto-crawl discovery.

BATCH_SIZE dinaikkan 100->1000 (2026-07-24, ditemukan nyata: dari
12.601 post YouTube, 6.663/53% belum disentuh >7 hari krn 100/jam jauh
lebih kecil drpd total backlog -- sama pola dgn masalah batch TikTok
sebelumnya). 1000 post = ~20 panggilan videos.list (batch 50 ID),
selesai dlm hitungan detik (BUKAN spt aktor komentar TikTok yg lambat),
jadi aman dinaikkan jauh lebih tinggi drpd sebelumnya."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agent_struktur_data import _compute_scores, _safe_int
from app.agents.youtube.api_client import YOUTUBE_API_BASE, get_youtube_api_key
from app.domain.posts.models import Post

REFRESH_BATCH_SIZE = 1000
YOUTUBE_LIST_MAX_IDS = 50


async def refresh_stale_youtube_posts(db: AsyncSession, limit: int = REFRESH_BATCH_SIZE) -> dict:
    api_key = await get_youtube_api_key(db)
    if not api_key:
        return {"refreshed": 0, "not_found": 0, "total_checked": 0, "error": "Tidak ada key YouTube Data API tersedia (grup 'youtube' kosong & agent_youtube01 jg belum punya)"}

    result = await db.execute(
        select(Post).where(Post.platform == "youtube").order_by(Post.collected_at.asc()).limit(limit)
    )
    posts = result.scalars().all()
    if not posts:
        return {"refreshed": 0, "not_found": 0, "total_checked": 0}

    video_ids = [p.external_id for p in posts]
    posts_by_id = {p.external_id: p for p in posts}
    stats_by_id: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(video_ids), YOUTUBE_LIST_MAX_IDS):
            batch = video_ids[i:i + YOUTUBE_LIST_MAX_IDS]
            resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params={
                "part": "snippet,statistics", "id": ",".join(batch), "key": api_key,
            })
            if resp.status_code != 200:
                continue
            for v in resp.json().get("items", []):
                if v.get("id") in posts_by_id:  # jangan terima video yg tidak diminta
                    stats_by_id[v["id"]] = v

        channel_ids = list({
            v.get("snippet", {}).get("channelId") for v in stats_by_id.values()
            if v.get("snippet", {}).get("channelId")
        })
        channels_by_id: dict = {}
        for i in range(0, len(channel_ids), YOUTUBE_LIST_MAX_IDS):
            batch = channel_ids[i:i + YOUTUBE_LIST_MAX_IDS]
            resp = await client.get(f"{YOUTUBE_API_BASE}/channels", params={
                "part": "snippet,statistics", "id": ",".join(batch), "key": api_key,
            })
            if resp.status_code == 200:
                for ch in resp.json().get("items", []):
                    channels_by_id[ch["id"]] = ch

    now = datetime.now(timezone.utc)
    refreshed = 0
    not_found = 0
    for vid, post in posts_by_id.items():
        v = stats_by_id.get(vid)
        if not v:
            # Video dihapus/private/salah region -- BUKAN error, tapi
            # jangan dicoba ulang tiap jam terus-menerus (buang-buang
            # kuota). Bump collected_at spy giliran refresh berikutnya
            # jatuh ke post lain dulu.
            not_found += 1
            post.collected_at = now
            continue

        stats = v.get("statistics", {})
        metrics = {
            "views": _safe_int(stats.get("viewCount")),
            "likes": _safe_int(stats.get("likeCount")),
            "comments": _safe_int(stats.get("commentCount")),
            "shares": 0,
        }
        channel_id = v.get("snippet", {}).get("channelId")
        scores = _compute_scores(
            {"metrics": metrics, "published_at": post.published_at, "channel_id": channel_id}, channels_by_id,
        )

        post.metrics = metrics
        # dict(...) BUKAN cuma `post.metadata_ or {}` -- kalau metadata_
        # sudah truthy, `or {}` balikin OBJEK YANG SAMA (referensi, bukan
        # salinan). Mutasi in-place lalu "assign balik" ke atribut yg
        # SAMA persis tidak selalu ke-detect SQLAlchemy sbg perubahan
        # utk kolom JSON polos -- trend_score dkk DIAM-DIAM GAGAL
        # tersimpan (ditemukan 2026-07-24, verified live: metrics ter-
        # update tapi trend_score tidak). dict() paksa jadi objek BARU.
        meta = dict(post.metadata_ or {})
        meta.update({
            "trend_score": scores["trend_score"], "engagement_score": scores["engagement_score"],
            "freshness_score": scores["freshness_score"], "authority_score": scores["authority_score"],
            "channel_subscriber_count": scores.get("channel_subscriber_count"),
            "audience_size": scores.get("audience_size"),
        })
        post.metadata_ = meta
        post.collected_at = now
        refreshed += 1

    await db.commit()
    return {"refreshed": refreshed, "not_found": not_found, "total_checked": len(posts)}
