"""Audit kelengkapan + backfill metadata SEMUA post YouTube (2026-07-24,
permintaan user "cek kembali kelengkapan dan update metadata postingan
youtube semua akun yang ada... pokoknya semua metadata harus diupdate").

BEDA dari `refresh.py` (SUDAH ADA, jalan tiap jam, `REFRESH_BATCH_SIZE`
post PALING LAMA disentuh) -- file ini KHUSUS audit kelengkapan +
backfill SEMUA post sekaligus dlm 1 run, PRIORITAS ke post yg genuinely
BELUM PERNAH dapat data (bukan cuma "paling lama"), krn ditemukan nyata
(2026-07-24) 7.012/12.601 (55,6%) post YouTube metrics-nya NULL TOTAL
(belum pernah disentuh sama sekali) dan 9.752/12.601 (77%) belum py
trend_score -- urutan "collected_at ASC" di refresh.py TIDAK menjamin
post yg genuinely kosong ini kepilih duluan (video baru pun collected_at
awalnya kecil/lama kalau insert-nya lama, campur sama yg udah lengkap).

TEMUAN TAMBAHAN: tabel `youtube_video_metadata` (channel_subscriber_count
dkk) SUDAH ORPHANED -- tidak ada satupun kode aktif di branch ini yg
nulis/baca tabel itu lagi (agent lama penulisnya sudah dihapus saat
restructure API v2). TIDAK dihidupkan lagi di sini (scope beda, perlu
keputusan terpisah kalau mau dipakai lagi) -- subscriber count malah
DITAMBAHKAN ke `posts.metadata_["channel_subscriber_count"]` (kolom yg
AKTIF dipakai dashboard/API sekarang), krn `_compute_scores()` di
agent_struktur_data.py SUDAH narik subscriberCount dari channels.list
tapi cuma dipakai internal (dibuang stlh authority_score dihitung, tidak
pernah disimpan mentah) -- di sini ditarik ulang & disimpan eksplisit.

Kuota: videos.list + channels.list SAMA-SAMA 1 unit/panggilan, batch 50
ID -- 1x full pass ~12.601 post = ~253 panggilan video + ~130 panggilan
channel (dedup per-batch) = ~383 unit total, SANGAT murah dibanding
search.list (100 unit/panggilan) yg dipakai auto-crawl discovery."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agent_struktur_data import _compute_scores, _safe_int
from app.agents.youtube.api_client import YOUTUBE_API_BASE, looks_like_youtube_key
from app.domain.posts.models import Post
from app.services.agent_registry.service import get_key_for_agent

YOUTUBE_LIST_MAX_IDS = 50
# Commit tiap N batch video (bukan 1x di akhir) -- supaya progress AMAN
# kalau run kepotong (timeout/restart worker), tidak balik ke 0.
COMMIT_EVERY_N_BATCHES = 10


def _priority_order():
    """0 = metrics belum pernah kesentuh (paling prioritas), 1 = metrics
    ada tapi trend_score belum ada, 2 = sudah lengkap semua (giliran
    terakhir, cuma ikut biar tetap ter-refresh kalau ada waktu)."""
    return case(
        (Post.metrics.is_(None), 0),
        (Post.metadata_["trend_score"].astext.is_(None), 1),
        else_=2,
    )


async def _fetch_video_batch(client: httpx.AsyncClient, ids: list[str], api_key: str) -> dict:
    resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params={
        "part": "snippet,statistics", "id": ",".join(ids), "key": api_key,
    })
    if resp.status_code != 200:
        return {}
    return {v["id"]: v for v in resp.json().get("items", []) if v.get("id") in ids}


async def _fetch_channel_batch(client: httpx.AsyncClient, ids: list[str], api_key: str) -> dict:
    if not ids:
        return {}
    resp = await client.get(f"{YOUTUBE_API_BASE}/channels", params={
        "part": "snippet,statistics", "id": ",".join(ids), "key": api_key,
    })
    if resp.status_code != 200:
        return {}
    return {ch["id"]: ch for ch in resp.json().get("items", [])}


async def audit_and_backfill_all_youtube_posts(db: AsyncSession, api_key: str | None = None) -> dict:
    if not api_key:
        key_info = await get_key_for_agent(db, "agent_youtube01")
        if not key_info or not looks_like_youtube_key(key_info.get("api_key")):
            return {"error": "agent_youtube01 tidak punya key YouTube asli", "checked": 0}
        api_key = key_info["api_key"]

    result = await db.execute(
        select(Post).where(Post.platform == "youtube").order_by(_priority_order(), Post.collected_at.asc())
    )
    posts = result.scalars().all()
    total_posts = len(posts)
    if not posts:
        return {"checked": 0, "updated": 0, "not_found": 0, "total_posts": 0}

    now_start = datetime.now(timezone.utc)
    checked = 0
    updated = 0
    not_found = 0
    channel_calls = 0
    video_calls = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for batch_start in range(0, total_posts, YOUTUBE_LIST_MAX_IDS):
            batch_posts = posts[batch_start:batch_start + YOUTUBE_LIST_MAX_IDS]
            video_ids = [p.external_id for p in batch_posts]
            posts_by_id = {p.external_id: p for p in batch_posts}

            videos_by_id = await _fetch_video_batch(client, video_ids, api_key)
            video_calls += 1

            channel_ids = list({
                v.get("snippet", {}).get("channelId") for v in videos_by_id.values()
                if v.get("snippet", {}).get("channelId")
            })
            channels_by_id: dict = {}
            for i in range(0, len(channel_ids), YOUTUBE_LIST_MAX_IDS):
                channels_by_id.update(await _fetch_channel_batch(client, channel_ids[i:i + YOUTUBE_LIST_MAX_IDS], api_key))
                channel_calls += 1

            now = datetime.now(timezone.utc)
            for vid, post in posts_by_id.items():
                checked += 1
                v = videos_by_id.get(vid)
                if not v:
                    # video dihapus/private/region-lock -- catat sudah
                    # dicek (collected_at bump) spy TIDAK diprioritaskan
                    # lagi run berikutnya (ordering di atas jadi netral
                    # ke post lain yg genuinely belum dicek), tapi TIDAK
                    # dianggap "updated" krn datanya tetap kosong.
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
                channel = channels_by_id.get(channel_id, {})
                subscriber_count = _safe_int((channel.get("statistics") or {}).get("subscriberCount"))
                channel_title = (channel.get("snippet") or {}).get("title")

                scores = _compute_scores(
                    {"metrics": metrics, "published_at": post.published_at, "channel_id": channel_id}, channels_by_id,
                )

                post.metrics = metrics
                # dict(...) WAJIB -- lihat [[project_json_column_mutation_bug]]:
                # `post.metadata_ or {}` balikin OBJEK YANG SAMA kalau sudah
                # truthy, mutasi in-place tidak selalu ke-detect SQLAlchemy
                # utk kolom JSON polos, UPDATE diam-diam gagal tersimpan.
                meta = dict(post.metadata_ or {})
                meta.update({
                    "trend_score": scores["trend_score"], "engagement_score": scores["engagement_score"],
                    "freshness_score": scores["freshness_score"], "authority_score": scores["authority_score"],
                    "channel_subscriber_count": subscriber_count,
                })
                if channel_title:
                    meta["channel_title"] = channel_title
                post.metadata_ = meta
                post.collected_at = now
                updated += 1

            if (batch_start // YOUTUBE_LIST_MAX_IDS + 1) % COMMIT_EVERY_N_BATCHES == 0:
                await db.commit()

    await db.commit()

    after_missing_metrics = await db.scalar(
        select(Post.id).where(Post.platform == "youtube", Post.metrics.is_(None)).limit(1)
    )

    return {
        "total_posts": total_posts,
        "checked": checked,
        "updated": updated,
        "not_found": not_found,
        "video_api_calls": video_calls,
        "channel_api_calls": channel_calls,
        "duration_seconds": round((datetime.now(timezone.utc) - now_start).total_seconds(), 1),
        "still_missing_metrics_after_run": after_missing_metrics is not None,
    }
