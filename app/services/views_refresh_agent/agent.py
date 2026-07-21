"""
Views Refresh Agent -- agent KEDUA (2026-07-18, permintaan user demi kejar
tenggat "hari Minggu"), pakai API KEY YOUTUBE DATA API TERPISAH (project
Google Cloud sendiri, kuota 10.000/hari SENDIRI -- BUKAN berbagi kuota
dgn Metadata Agent).

SCOPE SENGAJA DIPERSEMPIT (revisi 2026-07-18, permintaan user "khusus
views saja dulu"): BEDA dari fase refresh Metadata Agent
(`_refresh_stale_metadata()` di youtube_metadata/agent.py, yg lengkap --
title/description/channel/subscriber/tags/komentar dll), agent ini CUMA
update `views` (+likes/comments krn datang GRATIS di response API yg sama,
tidak nambah kuota) -- TIDAK panggil channels.list (skip info channel),
TIDAK fetch konten komentar (skip collect_comments_for_video -- itu bagian
PALING LAMBAT dari refresh lengkap, 1 video bisa sampai 5 halaman),
TIDAK cek title-mismatch. Tujuannya SATU: angka views ter-update SECEPAT
mungkin, sisanya biar Metadata Agent yg urus scr lengkap (lebih pelan,
tapi menyeluruh).

PRIORITAS (permintaan user "prioritaskan berdasarkan topic dulu"): video
yg `keyword_matched` TERISI (asli dari topic-search, BUKAN mode
free-discovery) diproses LEBIH DULU drpd video tanpa keterkaitan topik --
`ORDER BY (keyword_matched IS NULL) ASC, fetched_at ASC`.

SKIP LOCKED tetap dipakai (`FOR UPDATE OF youtube_video_metadata SKIP
LOCKED`) -- aman jalan bersamaan dgn fase refresh Metadata Agent, baris yg
sedang diproses satu sisi otomatis dilewati sisi lainnya.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.domain.posts.models import Post
from app.services.views_refresh_agent import config as cfg

logger_name = __name__


async def run_views_refresh_agent(db: AsyncSession) -> dict[str, Any]:
    """Entry point dipanggil worker Celery. Return ringkasan run."""
    import logging
    logger = logging.getLogger(logger_name)

    api_key = await cfg.get_api_key()
    if not api_key:
        return {"status": "error", "message": "API key YouTube Data API belum diatur (lihat PATCH /views-refresh-agent/config)"}

    batch_size = await cfg.get_batch_size()
    refresh_age_hours = await cfg.get_refresh_age_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=refresh_age_hours)

    # Prioritas topic-search dulu (keyword_matched terisi), baru sisanya --
    # dalam tiap kelompok, yg PALING LAMA belum dicek duluan (FIFO).
    # FOR UPDATE SKIP LOCKED: lihat docstring modul -- aman berdampingan
    # dgn fase refresh Metadata Agent.
    rows = (await db.execute(text(f"""
        SELECT id, video_id, post_id, keyword_matched
        FROM youtube_video_metadata
        WHERE fetched_at < :cutoff
        ORDER BY (keyword_matched IS NULL) ASC, fetched_at ASC
        LIMIT {batch_size}
        FOR UPDATE SKIP LOCKED
    """), {"cutoff": cutoff})).mappings().all()

    result: dict[str, Any] = {
        "status": "success",
        "refresh_candidates": len(rows),
        "refreshed": 0,
        "refreshed_topic_matched": 0,
        "skipped_unavailable": 0,
        "errors": [],
    }
    if not rows:
        return result

    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
    client = YouTubeDataAPIClient(api_key=api_key)

    video_ids = [r["video_id"] for r in rows]
    try:
        video_details = await client.get_videos_full_details(video_ids)
    except Exception as exc:
        logger.error("run_views_refresh_agent: get_videos_full_details gagal: %s", exc)
        result["status"] = "error"
        result["errors"].append(f"get_videos_full_details: {exc}")
        return result

    now = datetime.now(timezone.utc)
    errors: list[str] = []

    for r in rows:
        video_item = video_details.get(r["video_id"])
        if not video_item:
            # Video hilang/private -- bump fetched_at spy tdk nyangkut
            # selamanya di antrian (sama pola dgn Metadata Agent).
            await db.execute(text(
                "UPDATE youtube_video_metadata SET fetched_at=:now WHERE id=:id"
            ), {"now": now, "id": str(r["id"])})
            result["skipped_unavailable"] += 1
            continue

        try:
            stats = video_item.get("statistics") or {}
            views = int(stats.get("viewCount", 0) or 0)
            likes = int(stats.get("likeCount", 0) or 0)
            comments = int(stats.get("commentCount", 0) or 0)

            await db.execute(text("""
                UPDATE youtube_video_metadata
                SET views=:views, likes=:likes, comments=:comments, fetched_at=:now
                WHERE id=:id
            """), {"views": views, "likes": likes, "comments": comments, "now": now, "id": str(r["id"])})

            post_obj = await db.get(Post, r["post_id"])
            if post_obj:
                # flag_modified() WAJIB utk mutasi in-place kolom JSON pd
                # objek yg SUDAH persisted -- lihat catatan sama di
                # youtube_metadata/agent.py (pola berulang di codebase ini).
                if post_obj.metrics is not None:
                    post_obj.metrics["views"] = views
                    post_obj.metrics["likes"] = likes
                    post_obj.metrics["comments"] = comments
                    flag_modified(post_obj, "metrics")
                if post_obj.metadata_ is not None:
                    post_obj.metadata_["views"] = views
                    post_obj.metadata_["likes"] = likes
                    post_obj.metadata_["comments"] = comments
                    flag_modified(post_obj, "metadata_")

            result["refreshed"] += 1
            if r["keyword_matched"] is not None:
                result["refreshed_topic_matched"] += 1
        except Exception as exc:
            logger.error("run_views_refresh_agent: gagal update video_id=%s: %s", r["video_id"], exc)
            errors.append(f"{r['video_id']}: {exc}")

    result["errors"] = errors[:10]
    await db.commit()
    return result
