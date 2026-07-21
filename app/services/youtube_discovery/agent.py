"""
YouTube Discovery Agent -- pencarian video viral/trending OTOMATIS (dipicu
scheduler, lihat app/workers/youtube_discovery_worker.py), DUA mode
berjalan tiap run:

  1. Topic-guided: utk tiap SearchTopic aktif yg platform-nya cakup
     "youtube", cari video BARU terkait keyword topik itu (reuse
     YouTubeDataAPIClient.search_recent() -- pola SAMA dgn
     app/services/youtube/pipeline_service.py::search_recent_uploads()).
  2. Free discovery: cari apa saja yg sedang trending secara umum, TIDAK
     terikat topik manapun (reuse YouTubeDataAPIClient.fetch_popular(),
     mostPopular chart resmi YouTube -- BUKAN LLM menebak query sendiri,
     lebih hemat kuota & datanya real signal trending).

Tiap kandidat (dari KEDUA mode) divalidasi via LLM (OpenRouter, lihat
openrouter_client.py) SEBELUM disimpan ke `posts` -- cek genuinely baru +
data masuk akal (+ relevan ke topik utk mode topic-guided). Hasil disimpan
pakai skema unified (title/tags/media/metrics/language, lihat
app/services/processing/normalizer.py) SUPAYA siap diambil Metadata Agent
(app/services/youtube_metadata/agent.py) -- ditandai `metadata_["source"] =
"youtube_discovery_agent"` sbg penanda "post ini datang dari agent ini".

Sejak 2026-07-18: enrichment di sini SUDAH ambil detail video LENGKAP
(get_videos_full_details(), bukan cuma statistics) + info channel
(get_channels_details(), panggilan baru) -- hasil mentahnya disimpan di
Post.raw_data (video_full/channel_full) SUPAYA Metadata Agent bisa REUSE
data ini langsung tanpa panggil YouTube API lagi utk post yg berasal dari
sini (hindari hit API berulang, permintaan user).

Semua run (sukses/gagal) dicatat di tabel `youtube_discovery_runs`
(app/domain/youtube_discovery/models.py) -- itu yg jadi status monitor DAN
riwayat rinci utk dianalisis (kolom `details`).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.projects.models import Project
from app.domain.search_topics.models import SearchTopic
from app.domain.youtube_discovery.models import YouTubeDiscoveryRun
from app.services.processing.normalizer import _detect_lang, _extract_hashtags, _media_list
from app.services.youtube_discovery import config as cfg
from app.services.youtube_discovery.openrouter_client import DiscoveryRateLimitError, validate_candidate

logger = logging.getLogger(__name__)

FREE_DISCOVERY_KEYWORD_TEXT = "_discovery_free"
FREE_DISCOVERY_MAX_RESULTS = 20
TOPIC_GUIDED_HOURS_BACK = 24
TOPIC_GUIDED_MAX_RESULTS = 15

# Jeda antar panggilan validasi LLM -- ditambahkan 2026-07-18 setelah live
# test kena rate-limit (429) berkali-kali dari provider model gratis
# OpenRouter saat validasi >100 kandidat berturut-turut tanpa jeda. Model
# BERBAYAR biasanya tidak butuh ini (limit jauh lebih tinggi), tapi jeda ini
# tidak mahal (cuma nambah durasi run, bukan nambah panggilan) jadi aman
# dibiarkan aktif utk semua model.
VALIDATE_CALL_DELAY_SECONDS = 2.0


async def _validate_with_fallback(
    api_key: str, model: str, fallback_api_key: str | None, fallback_model: str, **kwargs,
) -> tuple[bool, str, bool]:
    """Bungkus validate_candidate() -- kalau key/model UTAMA kena rate-limit,
    coba key/model CADANGAN ('agent 2') sebelum reject konservatif. Return
    (valid, reason, used_fallback) -- used_fallback dicatat di `details` biar
    kelihatan di riwayat mana yg lolos berkat cadangan."""
    try:
        valid, reason = await validate_candidate(api_key, model, **kwargs)
        return valid, reason, False
    except DiscoveryRateLimitError as exc:
        if not fallback_api_key:
            logger.warning("validate_candidate: rate limit key utama, TIDAK ada fallback diatur, kandidat di-skip")
            return False, f"rate-limit key utama (tanpa fallback): {str(exc)[:150]}", False
        logger.info("validate_candidate: rate limit key utama, coba fallback (agent 2, model=%s)", fallback_model)
        try:
            valid, reason = await validate_candidate(fallback_api_key, fallback_model, **kwargs)
            return valid, reason, True
        except DiscoveryRateLimitError as exc2:
            logger.warning("validate_candidate: fallback JUGA rate limit, kandidat di-skip")
            return False, f"rate-limit key utama+fallback: {str(exc2)[:150]}", False


async def _run_topic_guided_mode(
    db: AsyncSession, client, api_key: str, model: str,
    fallback_api_key: str | None, fallback_model: str, details: list[dict],
) -> dict[str, Any]:
    """Mode topic-guided (cari video baru terkait SearchTopic aktif yg
    platform-nya cakup youtube) -- dipakai OLEH KEDUA agent: Agent 1
    (run_discovery_agent(), gabung dgn mode free) dan Agent 2
    (run_discovery_agent_2(), HANYA mode ini, permintaan user 2026-07-18
    "khusus melakukan scraping baru yang terkait topic-search yg sudah
    ada"). `details` di-mutasi in-place (dipakai caller utk run.details)."""
    topics_checked = 0
    candidates_found = 0
    candidates_validated = 0
    candidates_rejected = 0
    fallback_used = 0
    valid_candidates: list[dict] = []

    # order_by created_at DESC -- SAMA persis dgn urutan tampil di dashboard
    # topic-search (app/api/v1/topic_search.py) supaya topik yg "paling
    # atas" di layar user itu jg yg PERTAMA diproses (permintaan user).
    topics = (await db.scalars(
        select(SearchTopic).where(SearchTopic.is_active == True)  # noqa: E712
        .order_by(SearchTopic.created_at.desc())
    )).all()
    for topic in topics:
        if "youtube" not in (topic.platforms or []):
            continue
        topics_checked += 1
        kw_rows = (await db.execute(text(
            "SELECT keyword_text FROM search_topic_keywords WHERE topic_id = :tid"
        ), {"tid": str(topic.id)})).scalars().all()

        for kw_text in kw_rows:
            try:
                raw_candidates = await _search_topic_candidates(client, kw_text)
            except Exception as exc:
                logger.warning("_run_topic_guided_mode: search_recent gagal utk topik=%r kw=%r: %s", topic.name, kw_text, exc)
                continue

            new_raw = await _filter_already_saved(db, raw_candidates)
            candidates_found += len(new_raw)
            if not new_raw:
                continue

            enriched = await _enrich_candidates_full(client, new_raw)
            await _enrich_channels(client, enriched)
            for cand in enriched:
                valid, reason, used_fallback = await _validate_with_fallback(
                    api_key, model, fallback_api_key, fallback_model,
                    title=cand["title"], description=cand["description"], channel=cand["channel"],
                    published_at=cand["published_at"], views=cand["views"], likes=cand["likes"],
                    comments=cand["comments"], topic=kw_text,
                )
                if used_fallback:
                    fallback_used += 1
                details.append({
                    "mode": "topic", "topic": kw_text, "video_id": cand["video_id"],
                    "title": cand["title"], "valid": valid, "reason": reason, "used_fallback": used_fallback,
                })
                if valid:
                    candidates_validated += 1
                    cand["_topic_keyword"] = kw_text
                    valid_candidates.append(cand)
                else:
                    candidates_rejected += 1
                await asyncio.sleep(VALIDATE_CALL_DELAY_SECONDS)

    return {
        "topics_checked": topics_checked,
        "candidates_found": candidates_found,
        "candidates_validated": candidates_validated,
        "candidates_rejected": candidates_rejected,
        "fallback_used": fallback_used,
        "valid_candidates": valid_candidates,
    }


async def run_discovery_agent(db: AsyncSession) -> dict[str, Any]:
    """Entry point dipanggil worker Celery (lewat lock Redis, cegah
    tumpang tindih -- lihat config.acquire_running_lock()). Return
    ringkasan run, SEKALIGUS sudah tercatat penuh di tabel
    youtube_discovery_runs (dipanggil dari sini, bukan tanggung jawab
    caller)."""
    from app.shared.config import settings

    started_at = datetime.now(timezone.utc)
    run = YouTubeDiscoveryRun(id=uuid.uuid4(), status="running", started_at=started_at, agent_label="agent1")
    db.add(run)
    await db.commit()

    details: list[dict] = []
    topics_checked = 0
    candidates_found = 0
    candidates_validated = 0
    candidates_rejected = 0
    posts_saved = 0
    fallback_used = 0
    model = await cfg.get_model()
    fallback_model = await cfg.get_fallback_model()

    try:
        # Key YouTube Data API KHUSUS agent ini (Redis, switchable dari
        # dashboard) -- jatuh ke settings.youtube_data_api_key (.env, dipakai
        # bersama Metadata Agent dkk) kalau belum diatur, supaya TIDAK breaking
        # utk server yg belum pernah set key khusus ini.
        youtube_api_key = await cfg.get_youtube_api_key() or settings.youtube_data_api_key
        if not youtube_api_key:
            raise RuntimeError("YouTube Data API key belum di-set (lihat PATCH /youtube/discovery-agent/config atau YOUTUBE_DATA_API_KEY di .env)")
        api_key = await cfg.get_api_key()
        if not api_key:
            raise RuntimeError("OpenRouter API key belum diatur (lihat PATCH /youtube/discovery-agent/config)")
        # Tombol ON/OFF fallback ("agent 2") -- kalau dimatikan dari
        # dashboard, PERLAKUKAN spt fallback_api_key kosong (reject
        # konservatif spt biasa saat rate-limit), TANPA menghapus key/model
        # cadangan yg tersimpan (gampang dinyalakan lagi nanti).
        fallback_api_key = await cfg.get_fallback_api_key() if await cfg.get_fallback_enabled() else None

        from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
        client = YouTubeDataAPIClient(api_key=youtube_api_key)

        # ── Mode 1: topic-guided (helper SAMA dgn dipakai Agent 2, lihat
        #    run_discovery_agent_2()) ─────────────────────────────────────────
        topic_result = await _run_topic_guided_mode(db, client, api_key, model, fallback_api_key, fallback_model, details)
        topics_checked = topic_result["topics_checked"]
        candidates_found += topic_result["candidates_found"]
        candidates_validated += topic_result["candidates_validated"]
        candidates_rejected += topic_result["candidates_rejected"]
        fallback_used += topic_result["fallback_used"]
        all_valid_candidates: list[dict] = list(topic_result["valid_candidates"])

        # ── Mode 2: free discovery ───────────────────────────────────────────
        try:
            free_raw = await _fetch_free_discovery_candidates(client)
        except Exception as exc:
            logger.warning("run_discovery_agent: fetch_popular gagal: %s", exc)
            free_raw = []

        new_free = await _filter_already_saved(db, free_raw)
        candidates_found += len(new_free)
        await _enrich_channels(client, new_free)
        for cand in new_free:
            valid, reason, used_fallback = await _validate_with_fallback(
                api_key, model, fallback_api_key, fallback_model,
                title=cand["title"], description=cand["description"], channel=cand["channel"],
                published_at=cand["published_at"], views=cand["views"], likes=cand["likes"],
                comments=cand["comments"], topic=None,
            )
            if used_fallback:
                fallback_used += 1
            details.append({
                "mode": "free", "topic": None, "video_id": cand["video_id"],
                "title": cand["title"], "valid": valid, "reason": reason, "used_fallback": used_fallback,
            })
            if valid:
                candidates_validated += 1
                cand["_topic_keyword"] = None
                all_valid_candidates.append(cand)
            else:
                candidates_rejected += 1
            await asyncio.sleep(VALIDATE_CALL_DELAY_SECONDS)

        # ── Simpan yg lolos validasi ──────────────────────────────────────────
        if all_valid_candidates:
            posts_saved = await _save_candidates(db, all_valid_candidates)

        run.status = "success"
    except Exception as exc:
        logger.error("run_discovery_agent: gagal: %s", exc)
        run.status = "failed"
        run.error_message = str(exc)[:2000]
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.topics_checked = topics_checked
        run.candidates_found = candidates_found
        run.candidates_validated = candidates_validated
        run.candidates_rejected = candidates_rejected
        run.posts_saved = posts_saved
        run.fallback_used = fallback_used
        run.model_used = model
        run.details = details[:200]  # cap -- jaga2 kalau run sangat besar
        await db.commit()

    return {
        "status": run.status,
        "topics_checked": topics_checked,
        "candidates_found": candidates_found,
        "candidates_validated": candidates_validated,
        "candidates_rejected": candidates_rejected,
        "posts_saved": posts_saved,
        "fallback_used": fallback_used,
        "error": run.error_message,
    }


async def run_discovery_agent_2(db: AsyncSession) -> dict[str, Any]:
    """Agent 2 -- DISKOVERI TERPISAH dari Agent 1 (run_discovery_agent()),
    BUKAN sekadar key cadangan. Permintaan user 2026-07-18: "harusnya
    discovery ada dua agent dengan membawa masing-masing api key data
    youtube v3" -- Agent 2 bawa YouTube Data API key SENDIRI (TIDAK jatuh ke
    .env global, lihat agent2_config.get_youtube_api_key()) + OpenRouter
    key/model SENDIRI (kuota 50/hari TERPISAH TOTAL dari Agent 1) + jadwal
    SENDIRI (default tiap 1 jam, lihat workers/youtube_discovery_worker.py).

    HANYA mode topic-guided (cari video BARU terkait topic-search yg SUDAH
    ada di sistem) -- TIDAK ada mode free-discovery (itu tetap tugas Agent
    1). Reuse _run_topic_guided_mode() SAMA PERSIS dgn Agent 1, cuma beda
    client/key/model yg dipakai -- dedup terhadap `posts` (external_id)
    TETAP lintas-agent (Agent 2 tidak akan simpan video yg Agent 1 sudah
    simpan, dan sebaliknya)."""
    from app.services.youtube_discovery import agent2_config as cfg2

    started_at = datetime.now(timezone.utc)
    run = YouTubeDiscoveryRun(id=uuid.uuid4(), status="running", started_at=started_at, agent_label="agent2")
    db.add(run)
    await db.commit()

    details: list[dict] = []
    topics_checked = 0
    candidates_found = 0
    candidates_validated = 0
    candidates_rejected = 0
    posts_saved = 0
    model = await cfg2.get_model()

    try:
        youtube_api_key = await cfg2.get_youtube_api_key()
        if not youtube_api_key:
            raise RuntimeError("Agent 2: YouTube Data API key belum di-set (lihat PATCH /youtube/discovery-agent-2/config)")
        api_key = await cfg2.get_api_key()
        if not api_key:
            raise RuntimeError("Agent 2: OpenRouter API key belum diatur (lihat PATCH /youtube/discovery-agent-2/config)")

        from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
        client = YouTubeDataAPIClient(api_key=youtube_api_key)

        # Agent 2 belum punya fallback SENDIRI (fallback_api_key=None) --
        # kalau nanti key/model-nya jg kena rate-limit, tinggal tambah
        # get_fallback_*() ke agent2_config.py, pola SUDAH ada di
        # _validate_with_fallback() (generic, tidak terikat Agent 1 doang).
        topic_result = await _run_topic_guided_mode(db, client, api_key, model, None, model, details)
        topics_checked = topic_result["topics_checked"]
        candidates_found = topic_result["candidates_found"]
        candidates_validated = topic_result["candidates_validated"]
        candidates_rejected = topic_result["candidates_rejected"]

        if topic_result["valid_candidates"]:
            posts_saved = await _save_candidates(db, topic_result["valid_candidates"])

        run.status = "success"
    except Exception as exc:
        logger.error("run_discovery_agent_2: gagal: %s", exc)
        run.status = "failed"
        run.error_message = str(exc)[:2000]
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.topics_checked = topics_checked
        run.candidates_found = candidates_found
        run.candidates_validated = candidates_validated
        run.candidates_rejected = candidates_rejected
        run.posts_saved = posts_saved
        run.model_used = model
        run.details = details[:200]
        await db.commit()

    return {
        "status": run.status,
        "topics_checked": topics_checked,
        "candidates_found": candidates_found,
        "candidates_validated": candidates_validated,
        "candidates_rejected": candidates_rejected,
        "posts_saved": posts_saved,
        "error": run.error_message,
    }


async def _search_topic_candidates(client, keyword: str) -> list[dict]:
    from app.integrations.youtube.connector import YouTubeConnector

    published_after = datetime.now(timezone.utc) - timedelta(hours=TOPIC_GUIDED_HOURS_BACK)
    raw = await client.search_recent(keyword, published_after=published_after, max_results=TOPIC_GUIDED_MAX_RESULTS)
    connector = YouTubeConnector(client=None)
    items = connector.extract_posts(raw)

    candidates = []
    for item in items:
        snippet = item.get("snippet") or {}
        video_id = item.get("videoId") or item.get("video_id") or ""
        if not video_id:
            continue
        thumbs = snippet.get("thumbnails") or {}
        thumb_url = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        candidates.append({
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "description": snippet.get("description", ""),
            "thumbnail_url": thumb_url,
            "published_at": snippet.get("publishedAt"),
            "views": 0, "likes": 0, "comments": 0,  # search.list TIDAK include statistics, di-enrich terpisah
        })
    return candidates


async def _fetch_free_discovery_candidates(client) -> list[dict]:
    raw = await client.fetch_popular(region_code="ID", max_results=FREE_DISCOVERY_MAX_RESULTS)
    candidates = []
    for item in raw.get("items") or []:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        video_id = item.get("id", "")
        if not video_id:
            continue
        thumbs = snippet.get("thumbnails") or {}
        thumb_url = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        candidates.append({
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId"),
            "description": snippet.get("description", ""),
            "thumbnail_url": thumb_url,
            "published_at": snippet.get("publishedAt"),
            "views": int(stats.get("viewCount", 0) or 0),
            "likes": int(stats.get("likeCount", 0) or 0),
            "comments": int(stats.get("commentCount", 0) or 0),
            # fetch_popular() SUDAH balikin snippet+contentDetails+statistics
            # (part lengkap) -- reuse LANGSUNG sbg raw_data, TIDAK perlu
            # panggil get_videos_full_details() lagi utk mode ini (cuma
            # topicDetails yg tidak ada, trade-off yg diterima drpd
            # panggilan API tambahan). Lihat _save_candidates().
            "_video_item": item,
        })
    return candidates


async def _filter_already_saved(db: AsyncSession, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    ids = [c["video_id"] for c in candidates]
    existing = set((await db.scalars(
        select(Post.external_id).where(Post.platform == "youtube", Post.external_id.in_(ids))
    )).all())
    return [c for c in candidates if c["video_id"] not in existing]


async def _enrich_candidates_full(client, candidates: list[dict]) -> list[dict]:
    """Isi views/likes/comments/durasi/kategori/topicDetails LENGKAP utk
    kandidat topic-guided (search.list cuma balikin snippet) -- ganti dari
    get_videos_statistics() (sempit, cuma statistics) ke
    get_videos_full_details() (SAMA JUMLAH panggilan API, data lebih
    lengkap dari respons yg sama). video_item mentah disimpan di kandidat
    (_video_item) supaya Metadata Agent bisa REUSE tanpa panggil API lagi
    (2026-07-18, permintaan user -- hindari hit YouTube API berulang),
    lihat _save_candidates() -> Post.raw_data."""
    if not candidates:
        return candidates
    video_ids = [c["video_id"] for c in candidates]
    details_by_id = await client.get_videos_full_details(video_ids)
    for c in candidates:
        item = details_by_id.get(c["video_id"])
        if not item:
            continue
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        c["views"] = int(stats.get("viewCount", 0) or 0)
        c["likes"] = int(stats.get("likeCount", 0) or 0)
        c["comments"] = int(stats.get("commentCount", 0) or 0)
        c["channel_id"] = snippet.get("channelId")
        c["_video_item"] = item
    return candidates


async def _enrich_channels(client, candidates: list[dict]) -> None:
    """Lengkapi info channel (subscriber/negara/tanggal dibuat) SEKALI per
    batch kandidat -- panggilan BARU yg Discovery Agent sebelumnya tidak
    pernah buat (dulu diserahkan ke Metadata Agent). channel_item mentah
    disimpan di kandidat (_channel_item), reuse sama spt _video_item."""
    channel_ids = list({c["channel_id"] for c in candidates if c.get("channel_id")})
    if not channel_ids:
        return
    channel_details = await client.get_channels_details(channel_ids)
    for c in candidates:
        cid = c.get("channel_id")
        if cid and cid in channel_details:
            c["_channel_item"] = channel_details[cid]


async def _get_or_create_keyword(db: AsyncSession, keyword_text: str) -> uuid.UUID:
    from app.domain.users.models import User as UserModel

    kw = await db.scalar(select(Keyword).where(func.lower(Keyword.keyword) == keyword_text.lower()).limit(1))
    if kw:
        return kw.id
    project_id = await db.scalar(select(Project.id).where(Project.is_active == True).limit(1))  # noqa: E712
    if not project_id:
        first_user = await db.scalar(select(UserModel.id).limit(1))
        proj = Project(user_id=first_user, name="YouTube Discovery Agent", is_active=True)
        db.add(proj)
        await db.flush()
        project_id = proj.id
    kw = Keyword(project_id=project_id, keyword=keyword_text, is_active=True)
    db.add(kw)
    await db.flush()
    return kw.id


async def _save_candidates(db: AsyncSession, candidates: list[dict]) -> int:
    from app.repositories.post_repository import PostRepository

    # Get-or-create Keyword per kelompok topik (cache biar tidak query berulang)
    kw_cache: dict[str | None, uuid.UUID] = {}
    posts: list[Post] = []
    now = datetime.now(timezone.utc)

    for cand in candidates:
        topic_kw = cand.get("_topic_keyword")
        cache_key = topic_kw or FREE_DISCOVERY_KEYWORD_TEXT
        if cache_key not in kw_cache:
            kw_cache[cache_key] = await _get_or_create_keyword(db, cache_key)
        keyword_id = kw_cache[cache_key]

        title = cand["title"]
        description = cand["description"]
        text_for_lang = f"{title} {description}"

        posts.append(Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=cand["video_id"],
            platform="youtube",
            content=title,
            author=cand["channel"],
            url=f"https://www.youtube.com/watch?v={cand['video_id']}",
            title=title,
            tags=_extract_hashtags(text_for_lang),
            media=_media_list(cand.get("thumbnail_url")),
            metrics={"views": cand["views"], "likes": cand["likes"], "comments": cand["comments"], "shares": 0},
            language=_detect_lang(text_for_lang),
            metadata_={
                "views": cand["views"], "likes": cand["likes"], "comments": cand["comments"],
                "description": description, "thumbnail": cand.get("thumbnail_url", ""),
                "source": "youtube_discovery_agent",
            },
            # video_full/channel_full: hasil API LENGKAP yg sudah didapat
            # saat discovery (2026-07-18) -- Metadata Agent baca dari SINI
            # dulu sebelum mempertimbangkan panggil API sendiri, supaya
            # video BARU dari agent ini TIDAK di-fetch ulang dari YouTube.
            raw_data={
                "_discovery_agent": True,
                "mode": "topic" if topic_kw else "free",
                "video_full": cand.get("_video_item"),
                "channel_full": cand.get("_channel_item"),
            },
            published_at=_parse_iso(cand.get("published_at")),
            collected_at=now,
        ))

    if not posts:
        return 0
    repo = PostRepository(db)
    inserted = await repo.bulk_create(posts)
    await db.commit()
    return inserted


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
