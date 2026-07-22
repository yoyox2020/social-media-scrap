"""agent-struktur-data (2026-07-22) -- Data Processor. Terima hasil
mentah dari agent_youtube01+agent_youtube02 (via coordinator), lalu:
merge -> dedupe -> normalisasi -> validasi -> lengkapi field kosong ->
hitung skor (trend/engagement/freshness/authority) -> AI summary+tags
(best-effort) -> simpan ke DB (transaksi, rollback kalau gagal).

MVP (versi sederhana): skor pakai formula dasar (bukan model ML),
AI summary/tags DIBATASI ke video dgn trend_score tertinggi (default
10) supaya durasi+biaya LLM terkendali -- video lain tetap tersimpan,
cuma ai_summary/ai_tags-nya kosong. Reuse tabel `posts`/`comments` yg
SUDAH ADA (bukan bikin tabel baru) -- skor+ai_summary/tags disimpan di
kolom `metadata_` (JSON, sudah ada, fleksibel)."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.services.rotation_key_bank.service import get_working_key_for_agent, report_key_failure

AGENT_NAME = "agent-struktur-data"
AI_KEY_FAILURE_STATUS_CODES = {401, 402, 403, 429}
AI_SUMMARY_LIMIT = 10
FALLBACK_AI_MODEL = "openai/gpt-oss-20b:free"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dedupe(videos: list[dict]) -> tuple[list[dict], int]:
    seen: dict[str, dict] = {}
    duplicate_count = 0
    for v in videos:
        vid = v.get("id")
        if not vid:
            continue
        if vid in seen:
            duplicate_count += 1
            continue
        seen[vid] = v
    return list(seen.values()), duplicate_count


def _normalize(video: dict) -> dict | None:
    vid = video.get("id")
    snippet = video.get("snippet") or {}
    statistics = video.get("statistics") or {}
    title = snippet.get("title")
    if not vid or not title:
        return None

    thumbnails = snippet.get("thumbnails") or {}
    thumb_url = None
    for size in ("high", "medium", "default"):
        if thumbnails.get(size, {}).get("url"):
            thumb_url = thumbnails[size]["url"]
            break

    return {
        "external_id": vid,
        "platform": "youtube",
        "title": title,
        "content": snippet.get("description") or "",
        "author": snippet.get("channelTitle") or "",
        "channel_id": snippet.get("channelId"),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "media": [{"type": "image", "url": thumb_url}] if thumb_url else [],
        "metrics": {
            "views": _safe_int(statistics.get("viewCount")),
            "likes": _safe_int(statistics.get("likeCount")),
            "comments": _safe_int(statistics.get("commentCount")),
            "shares": 0,
        },
        "published_at": _parse_dt(snippet.get("publishedAt")),
        "raw_data": video,
        "comments_raw": video.get("_comments", []),
    }


def _compute_scores(item: dict, channels_by_id: dict) -> dict:
    metrics = item["metrics"]
    views = max(metrics["views"], 1)
    now = datetime.now(timezone.utc)
    published_at = item["published_at"] or now
    hours_since = max((now - published_at).total_seconds() / 3600, 0)

    freshness_score = max(0.0, 100.0 - (hours_since * 2))
    engagement_score = min(100.0, ((metrics["likes"] + metrics["comments"] * 2) / views) * 100)

    channel = channels_by_id.get(item.get("channel_id"), {})
    subscriber_count = _safe_int((channel.get("statistics") or {}).get("subscriberCount"))
    authority_score = min(100.0, math.log10(subscriber_count + 1) * 12) if subscriber_count else 40.0

    trend_score = round((freshness_score * 0.4) + (engagement_score * 0.35) + (authority_score * 0.25), 2)

    return {
        "trend_score": trend_score,
        "engagement_score": round(engagement_score, 2),
        "freshness_score": round(freshness_score, 2),
        "authority_score": round(authority_score, 2),
    }


async def _generate_ai_summary(api_key: str, model: str, title: str, content: str) -> dict:
    """Balikin {"summary":..,"tags":..} kalau berhasil, ATAU
    {"error": "<pesan>", "status_code": int|None} kalau gagal --
    status_code dipakai caller utk tentukan apakah ini kegagalan KEY
    (401/402/403/429, layak trigger rotasi) atau kegagalan lain
    (mis. model lagi down sesaat, bukan berarti key-nya mati)."""
    prompt = (
        f"Judul video YouTube: {title}\n"
        f"Deskripsi: {content[:500]}\n\n"
        "Buat ringkasan singkat (maks 2 kalimat, Bahasa Indonesia) dan 3-5 tag topik singkat. "
        "Balas HANYA JSON valid format: {\"summary\": \"...\", \"tags\": [\"...\"]}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
            )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}", "status_code": resp.status_code}
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        import json
        parsed = json.loads(text)
        return {"summary": parsed.get("summary"), "tags": parsed.get("tags", [])}
    except Exception as exc:
        return {"error": str(exc), "status_code": None}


async def process_and_save(
    db: AsyncSession, run_id: uuid.UUID, topic: str, api_videos: list[dict],
    api_channels: dict, crawler_videos: list[dict],
) -> dict:
    all_videos = api_videos + crawler_videos
    total_before_dedupe = len(all_videos)

    deduped, duplicate_count = _dedupe(all_videos)
    await log_activity(
        db, run_id, AGENT_NAME, "merge_dedupe",
        f"Merge {total_before_dedupe} video mentah -> {len(deduped)} unik ({duplicate_count} duplikat dihapus)",
    )

    normalized: list[dict] = []
    failed_count = 0
    for v in deduped:
        n = _normalize(v)
        if n is None:
            failed_count += 1
            continue
        n["scores"] = _compute_scores(n, api_channels)
        normalized.append(n)

    normalized.sort(key=lambda x: x["scores"]["trend_score"], reverse=True)

    await log_activity(
        db, run_id, AGENT_NAME, "normalize_score",
        f"{len(normalized)} video dinormalisasi+diberi skor, {failed_count} gagal validasi (judul/id kosong)",
    )

    ai_key_info = await get_working_key_for_agent(db, "agent_youtube")
    ai_done = 0
    rotated = False
    if ai_key_info and ai_key_info.get("api_key"):
        model = ai_key_info.get("model") or FALLBACK_AI_MODEL
        if "/" not in model:
            model = FALLBACK_AI_MODEL
        for item in normalized[:AI_SUMMARY_LIMIT]:
            ai_result = await _generate_ai_summary(ai_key_info["api_key"], model, item["title"], item["content"])
            if "error" not in ai_result:
                item["ai_summary"] = ai_result.get("summary")
                item["ai_tags"] = ai_result.get("tags", [])
                ai_done += 1
                continue

            item["ai_summary"] = None
            item["ai_tags"] = []
            if not rotated and ai_result.get("status_code") in AI_KEY_FAILURE_STATUS_CODES:
                rotated = True
                new_key = await report_key_failure(db, "agent_youtube", ai_result["error"])
                if new_key:
                    await log_activity(
                        db, run_id, AGENT_NAME, "key_rotated",
                        f"Key agent_youtube gagal (HTTP {ai_result.get('status_code')}), "
                        f"otomatis diganti dgn key baru dari bank rotasi",
                    )
                    ai_key_info = new_key
                    model = new_key.get("model") or FALLBACK_AI_MODEL
                    if "/" not in model:
                        model = FALLBACK_AI_MODEL
                    retry = await _generate_ai_summary(ai_key_info["api_key"], model, item["title"], item["content"])
                    if "error" not in retry:
                        item["ai_summary"] = retry.get("summary")
                        item["ai_tags"] = retry.get("tags", [])
                        ai_done += 1
                else:
                    await log_activity(
                        db, run_id, AGENT_NAME, "key_rotation_failed",
                        f"Key agent_youtube gagal (HTTP {ai_result.get('status_code')}) TAPI bank rotasi kosong "
                        f"(tidak ada key 'available' pengganti)", level="warning",
                    )
        for item in normalized[AI_SUMMARY_LIMIT:]:
            item["ai_summary"] = None
            item["ai_tags"] = []
        await log_activity(
            db, run_id, AGENT_NAME, "ai_summary",
            f"AI summary/tags berhasil utk {ai_done}/{min(len(normalized), AI_SUMMARY_LIMIT)} video "
            f"(dibatasi {AI_SUMMARY_LIMIT} teratas by trend_score), model={model}",
        )
    else:
        for item in normalized:
            item["ai_summary"] = None
            item["ai_tags"] = []
        await log_activity(
            db, run_id, AGENT_NAME, "ai_summary",
            "AI summary dilewati -- agent_youtube belum punya key aktif", level="warning",
        )

    saved_count = 0
    duplicate_in_db = 0
    try:
        for item in normalized:
            existing = await db.scalar(
                select(Post).where(Post.external_id == item["external_id"], Post.platform == "youtube")
            )
            old_meta = (existing.metadata_ or {}) if existing else {}
            # JANGAN timpa ai_summary/ai_tags dgn None kalau run SEBELUMNYA
            # sudah berhasil bikin ringkasan tapi run INI tidak
            # mencakup video ini di top-10-nya sendiri -- pertahankan yg lama.
            ai_summary = item["ai_summary"] or old_meta.get("ai_summary")
            ai_tags = item["ai_tags"] or old_meta.get("ai_tags") or []
            # source_topic jadi daftar SEMUA topik yg pernah nemuin video ini,
            # bukan cuma topik run TERAKHIR (biar histori pencarian tidak hilang).
            prev_topics = old_meta.get("source_topics") or ([old_meta["source_topic"]] if old_meta.get("source_topic") else [])
            source_topics = list(dict.fromkeys([*prev_topics, topic]))
            metadata = {
                "trend_score": item["scores"]["trend_score"],
                "engagement_score": item["scores"]["engagement_score"],
                "freshness_score": item["scores"]["freshness_score"],
                "authority_score": item["scores"]["authority_score"],
                "ai_summary": ai_summary,
                "ai_tags": ai_tags,
                "source_topic": topic,  # topik run TERAKHIR (backward-compat)
                "source_topics": source_topics,  # SEMUA topik yg pernah nemuin video ini
                "channel_id": item.get("channel_id"),
            }
            if existing:
                existing.title = item["title"]
                existing.content = item["content"]
                existing.author = item["author"]
                existing.url = item["url"]
                existing.media = item["media"]
                existing.metrics = item["metrics"]
                existing.metadata_ = metadata
                existing.raw_data = item["raw_data"]
                existing.collected_at = datetime.now(timezone.utc)
                duplicate_in_db += 1
                post_row = existing
            else:
                post_row = Post(
                    external_id=item["external_id"], platform="youtube", title=item["title"],
                    content=item["content"], author=item["author"], url=item["url"],
                    media=item["media"], metrics=item["metrics"], metadata_=metadata,
                    raw_data=item["raw_data"], published_at=item["published_at"],
                    collected_at=datetime.now(timezone.utc), is_processed=False, is_near_duplicate=False,
                )
                db.add(post_row)
                await db.flush()
                saved_count += 1

            for c in item.get("comments_raw", []):
                try:
                    top_comment = c["snippet"]["topLevelComment"]["snippet"]
                    external_comment_id = c["snippet"]["topLevelComment"]["id"]
                except (KeyError, TypeError):
                    continue
                existing_comment = await db.scalar(
                    select(Comment).where(Comment.post_id == post_row.id, Comment.external_id == external_comment_id)
                )
                if existing_comment:
                    continue
                db.add(Comment(
                    post_id=post_row.id, external_id=external_comment_id,
                    content=top_comment.get("textDisplay") or "",
                    author=top_comment.get("authorDisplayName") or "",
                    metadata_={"like_count": top_comment.get("likeCount")},
                    published_at=_parse_dt(top_comment.get("publishedAt")),
                ))

        await db.commit()
    except Exception as exc:
        await db.rollback()
        await log_activity(db, run_id, AGENT_NAME, "save_failed", f"Rollback -- gagal simpan: {exc}", level="error")
        raise

    unique_channels = len({v.get("channel_id") for v in normalized if v.get("channel_id")})

    await log_activity(
        db, run_id, AGENT_NAME, "save_done",
        f"Tersimpan: {saved_count} baru, {duplicate_in_db} diperbarui (sudah ada sebelumnya)",
    )

    return {
        "total_video": len(normalized),
        "total_channel": unique_channels,
        "saved_to_database": saved_count,
        "duplicate_removed": duplicate_count + duplicate_in_db,
        "failed": failed_count,
    }
