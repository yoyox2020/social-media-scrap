"""
Metadata Agent -- MURNI pengambilan data (BUKAN analisis/AI-judgment spt
Discovery Agent), jalan SETELAH Discovery Agent (atau pipeline lain) simpan
post YouTube baru ke `posts`. Tugas: lengkapi info video+channel dari
YouTube API (videos.list + channels.list), simpan ke tabel terpisah
`youtube_video_metadata` (1 baris per post), SEKALIGUS refresh
posts.metrics/metadata_ dgn angka views/likes/comments terbaru.

TIDAK dirantai langsung ke Discovery Agent -- jalan terjadwal sendiri,
cari post YouTube manapun yg belum py baris di youtube_video_metadata
(NOT EXISTS check), jadi otomatis mencakup post dari sumber manapun, bukan
cuma dari Discovery Agent.

Satu pengecualian "bukan analisis": field `viral_context` diisi LLM
(OpenRouter, model gratis, TANPA pencarian web real-time) -- BUKAN
menilai/memvalidasi kandidat spt Discovery Agent, cuma menjelaskan
KONTEKS video yg SUDAH PASTI disimpan (bukan gerbang lolos/tidak).
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.youtube_video_metadata.models import YouTubeVideoMetadata
from app.services.youtube_metadata import config as cfg
from app.services.youtube_metadata.openrouter_client import generate_viral_context

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

# keyword_id sintetis dari mode free-discovery Discovery Agent (lihat
# app/services/youtube_discovery/agent.py::FREE_DISCOVERY_KEYWORD_TEXT) --
# BUKAN keyword topic-search asli, jangan dianggap keyword_matched.
_SYNTHETIC_KEYWORD_TEXTS = {"_discovery_free"}

# Jeda antar panggilan LLM -- pelajaran SAMA dari Discovery Agent
# (app/services/youtube_discovery/agent.py, 2026-07-18): model gratis
# OpenRouter gampang kena rate-limit (429) kalau dipanggil berturut-turut
# tanpa jeda, bikin run bisa nyangkut lama.
VIRAL_CONTEXT_CALL_DELAY_SECONDS = 2.0

# Ambil komentar "sebanyak-banyaknya" (permintaan user 2026-07-18) TAPI
# dibatasi angka wajar per video -- video mega-viral bisa punya ratusan
# ribu komentar, tanpa batas ini SATU video saja bisa bikin 1 run nyangkut
# lama (tiap page = 1 panggilan API). 200 komentar (5 page x ~40-50/page)
# = cakupan lumayan besar tanpa bikin run tidak wajar durasinya.
COMMENTS_MAX_PER_VIDEO = 200
COMMENTS_MAX_PAGES = 5


def _parse_duration_seconds(iso: str | None) -> int | None:
    if not iso:
        return None
    m = _DURATION_RE.match(iso)
    if not m:
        return None
    h, mnt, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mnt * 60 + s


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


async def run_metadata_agent(db: AsyncSession) -> dict[str, Any]:
    """Entry point dipanggil worker Celery. Return ringkasan run.

    Dua fase per run (2026-07-18, permintaan user "Stage 2" -- agar data
    selalu ter-auto-refresh, tidak cuma diambil sekali lalu basi selamanya):
      1. `_enrich_new_posts()`   -- post YouTube baru yg BELUM py baris
         youtube_video_metadata (spt sebelumnya).
      2. `_refresh_stale_metadata()` -- baris yg SUDAH ada tapi fetched_at-nya
         lebih tua dari `refresh_age_hours` (default 6 jam), di-refresh ulang
         views/likes/comments/subscriber + komentar baru.
    Kedua fase jalan tiap kali task terjadwal terpicu (jadi refresh benar2
    berkelanjutan, bukan sekali jalan), 1 commit gabungan di akhir.
    """
    from app.shared.config import settings

    if not settings.youtube_data_api_key:
        return {"status": "error", "message": "YOUTUBE_DATA_API_KEY belum di-set di server"}

    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
    client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)

    enrich_result = await _enrich_new_posts(db, client)
    refresh_result = await _refresh_stale_metadata(db, client)

    await db.commit()

    result: dict[str, Any] = {"status": "success", **enrich_result, **refresh_result}
    logger.info("run_metadata_agent: %s", result)
    return result


async def _enrich_new_posts(db: AsyncSession, client: Any) -> dict[str, Any]:
    """Fase 1: post YouTube baru yg BELUM py baris youtube_video_metadata."""
    processed = 0
    enriched = 0
    skipped_unavailable = 0
    comments_fetched = 0
    errors: list[str] = []
    model = await cfg.get_model()
    api_key = await cfg.get_api_key()

    # ── Cari post YouTube yg BELUM py baris youtube_video_metadata ────────────
    enrich_batch_size = await cfg.get_enrich_batch_size()
    rows = (await db.execute(text(f"""
        SELECT p.id, p.external_id, p.keyword_id, p.raw_data
        FROM posts p
        WHERE p.platform = 'youtube'
          AND NOT EXISTS (SELECT 1 FROM youtube_video_metadata m WHERE m.post_id = p.id)
        ORDER BY p.collected_at DESC
        LIMIT {enrich_batch_size}
    """))).mappings().all()

    if not rows:
        return {"processed": 0, "enriched": 0, "skipped_unavailable": 0, "reused_from_raw_data": 0, "comments_fetched": 0, "errors": []}

    # ── Reuse data yg SUDAH ada di Post.raw_data (dari Discovery Agent,
    # lihat app/services/youtube_discovery/agent.py) SEBELUM panggil API --
    # cuma post yg BELUM punya video_full di raw_data yg benar2 di-fetch
    # dari YouTube (2026-07-18, permintaan user: hindari hit API berulang).
    video_details: dict[str, Any] = {}
    reused_from_raw_data = 0
    video_ids_needing_api: list[str] = []
    for r in rows:
        raw = r["raw_data"] or {}
        video_full = raw.get("video_full")
        if video_full:
            video_details[r["external_id"]] = video_full
            reused_from_raw_data += 1
        else:
            video_ids_needing_api.append(r["external_id"])

    if video_ids_needing_api:
        try:
            fetched = await client.get_videos_full_details(video_ids_needing_api)
            video_details.update(fetched)
        except Exception as exc:
            logger.error("run_metadata_agent: get_videos_full_details gagal: %s", exc)
            return {
                "processed": 0, "enriched": 0, "skipped_unavailable": 0,
                "reused_from_raw_data": reused_from_raw_data, "comments_fetched": 0,
                "errors": [f"get_videos_full_details: {exc}"],
            }

    # ── Sama utk channel: reuse channel_full dari raw_data kalau ada ─────────
    channel_details: dict[str, Any] = {}
    channel_ids_needing_api: set[str] = set()
    for r in rows:
        raw = r["raw_data"] or {}
        channel_full = raw.get("channel_full")
        if channel_full and channel_full.get("id"):
            channel_details[channel_full["id"]] = channel_full
        else:
            video_item = video_details.get(r["external_id"])
            cid = ((video_item or {}).get("snippet") or {}).get("channelId")
            if cid:
                channel_ids_needing_api.add(cid)
    channel_ids_needing_api -= set(channel_details.keys())

    if channel_ids_needing_api:
        try:
            fetched_channels = await client.get_channels_details(list(channel_ids_needing_api))
            channel_details.update(fetched_channels)
        except Exception as exc:
            logger.warning("run_metadata_agent: get_channels_details gagal (%s), lanjut tanpa info channel", exc)

    # ── keyword_matched: lookup Keyword.keyword lewat post.keyword_id ─────────
    keyword_ids = [r["keyword_id"] for r in rows if r["keyword_id"]]
    keyword_text_by_id: dict[uuid.UUID, str] = {}
    if keyword_ids:
        kw_rows = (await db.execute(
            select(Keyword.id, Keyword.keyword).where(Keyword.id.in_(keyword_ids))
        )).all()
        keyword_text_by_id = {kid: ktext for kid, ktext in kw_rows}

    now = datetime.now(timezone.utc)
    metadata_rows: list[YouTubeVideoMetadata] = []

    for r in rows:
        processed += 1
        post_id = r["id"]
        video_id = r["external_id"]
        video_item = video_details.get(video_id)

        if not video_item:
            # Video sudah dihapus/private/unavailable -- tetap simpan baris
            # kosong (fetched_at terisi) supaya TIDAK di-retry selamanya
            # tiap run (buang-buang kuota).
            skipped_unavailable += 1
            metadata_rows.append(YouTubeVideoMetadata(
                id=uuid.uuid4(), post_id=post_id, video_id=video_id, fetched_at=now,
            ))
            continue

        try:
            snippet = video_item.get("snippet") or {}
            content_details = video_item.get("contentDetails") or {}
            stats = video_item.get("statistics") or {}
            topic_details = video_item.get("topicDetails") or {}

            channel_id = snippet.get("channelId")
            channel_item = channel_details.get(channel_id) if channel_id else None
            channel_snippet = (channel_item or {}).get("snippet") or {}
            channel_stats = (channel_item or {}).get("statistics") or {}

            kw_text = keyword_text_by_id.get(r["keyword_id"])
            keyword_matched = kw_text if kw_text and kw_text not in _SYNTHETIC_KEYWORD_TEXTS else None

            views = int(stats.get("viewCount", 0) or 0)
            likes = int(stats.get("likeCount", 0) or 0)
            comments = int(stats.get("commentCount", 0) or 0)
            tags = snippet.get("tags") or []
            title = snippet.get("title", "")
            description = snippet.get("description", "")

            viral_context = None
            if api_key:
                viral_context = await generate_viral_context(
                    api_key, model,
                    title=title, description=description, tags=tags,
                    views=views, likes=likes, comments=comments,
                )
                await asyncio.sleep(VIRAL_CONTEXT_CALL_DELAY_SECONDS)

            metadata_rows.append(YouTubeVideoMetadata(
                id=uuid.uuid4(),
                post_id=post_id,
                video_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=title,
                description=description,
                published_at=_parse_iso(snippet.get("publishedAt")),
                duration_seconds=_parse_duration_seconds(content_details.get("duration")),
                duration_iso=content_details.get("duration"),
                category_id=snippet.get("categoryId"),
                language=snippet.get("defaultLanguage") or snippet.get("defaultAudioLanguage"),
                channel_id=channel_id,
                channel_name=snippet.get("channelTitle"),
                channel_subscriber_count=int(channel_stats["subscriberCount"]) if channel_stats.get("subscriberCount") is not None else None,
                channel_country=channel_snippet.get("country"),
                channel_created_at=_parse_iso(channel_snippet.get("publishedAt")),
                views=views,
                likes=likes,
                comments=comments,
                favorite_count=int(stats.get("favoriteCount", 0) or 0) if "favoriteCount" in stats else None,
                favorite_available="favoriteCount" in stats,
                tags=tags,
                keyword_matched=keyword_matched,
                topic_categories=topic_details.get("topicCategories") or [],
                viral_context=viral_context,
                viral_context_model=model if viral_context else None,
                # Baseline verifikasi judul: title INI hasil observasi PERTAMA
                # (ground truth saat itu), belum ada apa pun utk dibandingkan.
                title_mismatch=False,
                title_live=None,
                title_checked_at=now,
                fetched_at=now,
            ))

            # Refresh posts.metrics/metadata_ dgn angka terbaru (kesepakatan
            # user 2026-07-18: ya, ikut di-refresh).
            post_obj = await db.get(Post, post_id)
            if post_obj:
                # flag_modified() WAJIB -- mutasi dict in-place pada JSON
                # column TIDAK terdeteksi otomatis oleh SQLAlchemy change
                # tracking utk objek yg SUDAH persisted (beda dari objek
                # baru blm di-flush), ditemukan 2026-07-16 lewat test
                # real-DB, terulang lagi di sini sampai ketahuan test gagal.
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

            # Ambil KONTEN komentar (bukan cuma jumlahnya) -- permintaan user
            # 2026-07-18 "ambil sebanyak-banyaknya". Reuse
            # collect_comments_for_video() yg SUDAH ADA (dipakai jalur lain,
            # pola EnsembleData+YouTube Data API fallback+dedup+pagination
            # sudah teruji) drpd bikin ulang logic serupa. Dibungkus
            # try/except SENDIRI -- gagal ambil komentar TIDAK BOLEH gagalkan
            # baris metadata video yg sudah berhasil disimpan (best-effort,
            # sama spt viral_context).
            try:
                from app.services.youtube.pipeline_service import collect_comments_for_video
                comment_result = await collect_comments_for_video(
                    db, post_id, r["keyword_id"],
                    max_comments=COMMENTS_MAX_PER_VIDEO, max_pages=COMMENTS_MAX_PAGES,
                    # skip_ensemble=True: EnsembleData terkonfirmasi (log
                    # produksi 2026-07-18) SELALU 495/quota habis utk endpoint
                    # komentar -- percuma dicoba dulu tiap halaman, langsung
                    # ke YouTube Data API v3 (fallback yg selama ini toh sukses).
                    skip_ensemble=True,
                )
                comments_fetched += comment_result.comments_new
            except Exception as exc:
                logger.warning("run_metadata_agent: gagal ambil komentar video_id=%s: %s", video_id, exc)

            enriched += 1
        except Exception as exc:
            logger.error("run_metadata_agent: gagal proses video_id=%s: %s", video_id, exc)
            errors.append(f"{video_id}: {exc}")

    db.add_all(metadata_rows)

    return {
        "processed": processed,
        "enriched": enriched,
        "skipped_unavailable": skipped_unavailable,
        "reused_from_raw_data": reused_from_raw_data,  # 0 panggilan API video utk sebanyak ini
        "comments_fetched": comments_fetched,
        "errors": errors[:10],
    }


async def _refresh_stale_metadata(
    db: AsyncSession, client: Any,
    refresh_age_hours: int | None = None, refresh_batch_size: int | None = None,
) -> dict[str, Any]:
    """Fase 2 (Stage 2, 2026-07-18) -- baris youtube_video_metadata yg SUDAH
    ter-enrich tapi fetched_at-nya lebih tua dari `refresh_age_hours`
    di-refresh ulang: views/likes/comments/subscriber TERBARU dari YouTube API
    (TIDAK reuse raw_data -- itu snapshot LAMA, justru sumber datanya basi).
    Komentar BARU sejak fetch terakhir juga diambil (dedup by external_id,
    aman diulang). viral_context TIDAK digenerate ulang (bukan data yg
    berubah seiring waktu, hemat panggilan LLM).

    `refresh_age_hours`/`refresh_batch_size`: kalau None (default), baca dari
    config Metadata Agent sendiri (perilaku lama, tidak berubah). Kalau
    diisi eksplisit -- dipakai caller LAIN (2026-07-18: Views Refresh Agent,
    `app/services/views_refresh_agent/agent.py`, key YouTube API TERPISAH,
    config sendiri) yg reuse fungsi ini tanpa duplikasi logic.
    """
    if refresh_age_hours is None:
        refresh_age_hours = await cfg.get_refresh_age_hours()
    if refresh_batch_size is None:
        refresh_batch_size = await cfg.get_refresh_batch_size()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=refresh_age_hours)

    # with_for_update(skip_locked=True): Metadata Agent (refresh phase-nya
    # sendiri) DAN Views Refresh Agent bisa jalan BERSAMAAN (dua key YouTube
    # API terpisah, 2026-07-18) -- tanpa ini, keduanya bisa ambil baris yg
    # SAMA sbg kandidat dlm jendela waktu yg tumpang tindih (buang kuota
    # dobel, bukan korupsi data, tapi tetap sia-sia). SKIP LOCKED bikin
    # proses KEDUA otomatis lewati baris yg sedang dikunci proses PERTAMA,
    # ambil batch berikutnya -- pola standar utk banyak worker rebutan 1
    # antrian di Postgres. `of=YouTubeVideoMetadata` scope lock cuma ke
    # tabel ini (bukan ikut kunci baris `posts` yg di-JOIN).
    stmt = (
        select(YouTubeVideoMetadata, Post.keyword_id)
        .join(Post, Post.id == YouTubeVideoMetadata.post_id)
        .where(YouTubeVideoMetadata.fetched_at < cutoff)
        .order_by(YouTubeVideoMetadata.fetched_at.asc())
        .limit(refresh_batch_size)
        .with_for_update(skip_locked=True, of=YouTubeVideoMetadata)
    )
    rows = (await db.execute(stmt)).all()

    result: dict[str, Any] = {
        "refresh_candidates": len(rows), "refreshed": 0,
        "refresh_skipped_unavailable": 0, "refresh_comments_fetched": 0,
        "title_mismatches_found": 0,
        "refresh_errors": [],
    }
    if not rows:
        return result

    video_ids = [m.video_id for m, _kw in rows]
    try:
        video_details = await client.get_videos_full_details(video_ids)
    except Exception as exc:
        logger.error("_refresh_stale_metadata: get_videos_full_details gagal: %s", exc)
        result["refresh_errors"].append(f"get_videos_full_details: {exc}")
        return result

    channel_ids: set[str] = {m.channel_id for m, _kw in rows if m.channel_id}
    for m, _kw in rows:
        if not m.channel_id:
            vi = video_details.get(m.video_id)
            cid = ((vi or {}).get("snippet") or {}).get("channelId")
            if cid:
                channel_ids.add(cid)

    channel_details: dict[str, Any] = {}
    if channel_ids:
        try:
            channel_details = await client.get_channels_details(list(channel_ids))
        except Exception as exc:
            logger.warning("_refresh_stale_metadata: get_channels_details gagal (%s), lanjut tanpa update channel", exc)

    now = datetime.now(timezone.utc)
    errors: list[str] = []

    for m, keyword_id in rows:
        video_item = video_details.get(m.video_id)
        if not video_item:
            # Video sudah hilang/private -- bump fetched_at supaya TIDAK
            # nyangkut selamanya di antrian refresh (buang kuota tiap run).
            m.fetched_at = now
            result["refresh_skipped_unavailable"] += 1
            continue

        try:
            snippet = video_item.get("snippet") or {}
            content_details = video_item.get("contentDetails") or {}
            stats = video_item.get("statistics") or {}
            topic_details = video_item.get("topicDetails") or {}

            channel_id = m.channel_id or snippet.get("channelId")
            channel_item = channel_details.get(channel_id) if channel_id else None
            channel_snippet = (channel_item or {}).get("snippet") or {}
            channel_stats = (channel_item or {}).get("statistics") or {}

            views = int(stats.get("viewCount", 0) or 0)
            likes = int(stats.get("likeCount", 0) or 0)
            comments = int(stats.get("commentCount", 0) or 0)

            # Verifikasi judul vs id video (2026-07-18, permintaan user):
            # bandingkan title TERSIMPAN dgn title ASLI YouTube saat ini --
            # OBJEKTIF (bukan tebakan AI). Kalau beda, CUMA ditandai
            # (title_mismatch + title_live) -- title tersimpan SENGAJA TIDAK
            # ditimpa otomatis, biar bisa ditinjau manual dulu (video bisa
            # memang berganti judul/clickbait edit, atau data awal salah).
            live_title = (snippet.get("title") or "").strip()
            stored_title = (m.title or "").strip()
            m.title_checked_at = now
            if live_title and stored_title and live_title != stored_title:
                m.title_mismatch = True
                m.title_live = live_title
                result["title_mismatches_found"] += 1
            else:
                m.title_mismatch = False
                m.title_live = None

            m.description = snippet.get("description") or m.description
            m.duration_seconds = _parse_duration_seconds(content_details.get("duration")) or m.duration_seconds
            m.duration_iso = content_details.get("duration") or m.duration_iso
            m.channel_id = channel_id
            m.channel_name = snippet.get("channelTitle") or m.channel_name
            if channel_stats.get("subscriberCount") is not None:
                m.channel_subscriber_count = int(channel_stats["subscriberCount"])
            m.channel_country = channel_snippet.get("country") or m.channel_country
            m.views = views
            m.likes = likes
            m.comments = comments
            if "favoriteCount" in stats:
                m.favorite_count = int(stats.get("favoriteCount", 0) or 0)
                m.favorite_available = True
            m.tags = snippet.get("tags") or m.tags
            m.topic_categories = topic_details.get("topicCategories") or m.topic_categories
            m.fetched_at = now

            post_obj = await db.get(Post, m.post_id)
            if post_obj:
                # flag_modified() WAJIB, lihat catatan di _enrich_new_posts().
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

            try:
                from app.services.youtube.pipeline_service import collect_comments_for_video
                comment_result = await collect_comments_for_video(
                    db, m.post_id, keyword_id,
                    max_comments=COMMENTS_MAX_PER_VIDEO, max_pages=COMMENTS_MAX_PAGES,
                    skip_ensemble=True,
                )
                result["refresh_comments_fetched"] += comment_result.comments_new
            except Exception as exc:
                logger.warning("_refresh_stale_metadata: gagal ambil komentar baru video_id=%s: %s", m.video_id, exc)

            result["refreshed"] += 1
        except Exception as exc:
            logger.error("_refresh_stale_metadata: gagal refresh video_id=%s: %s", m.video_id, exc)
            errors.append(f"{m.video_id}: {exc}")

    result["refresh_errors"] = errors[:10]
    return result
