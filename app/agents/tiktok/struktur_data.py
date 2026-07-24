"""agent-struktur-data utk TikTok (2026-07-23) -- merge/dedupe/
normalize/score/AI-summary/save, pola SAMA PERSIS dgn versi YouTube
(app/agents/agent_struktur_data.py) tapi field mapping beda krn bentuk
data Apify TikTok beda total (playCount bukan viewCount, authorMeta.fans
bukan subscriberCount, text bukan title+description terpisah, dst).

MIN_VIEWS_FOR_ENGAGEMENT (2026-07-23) diterapkan SEJAK AWAL di sini --
bug yg sama baru ditemukan+diperbaiki di versi YouTube (video nyaris 0
views matematis jadi "engagement 100%"), jadi TIDAK diulang di sini."""
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
from app.services.sentiment.save import analyze_and_queue_lexicon
from app.services.rotation_key_bank.service import get_working_key_for_agent, report_key_failure

AGENT_NAME = "agent-struktur-data"
AI_KEY_FAILURE_STATUS_CODES = {401, 402, 403, 429}
AI_SUMMARY_LIMIT = 10
FALLBACK_AI_MODEL = "openai/gpt-oss-20b:free"
MIN_VIEWS_FOR_ENGAGEMENT = 50


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    text = video.get("text") or ""
    author_meta = video.get("authorMeta") or {}
    if not vid or not (text or author_meta.get("name")):
        return None

    video_meta = video.get("videoMeta") or {}
    thumb_url = video_meta.get("coverUrl")

    return {
        "external_id": vid,
        "platform": "tiktok",
        "title": text[:100] if text else (author_meta.get("name") or vid),
        "content": text,
        "author": author_meta.get("name") or author_meta.get("nickName") or "",
        "author_fans": _safe_int(author_meta.get("fans")),
        "url": video.get("webVideoUrl") or "",
        "media": [{"type": "image", "url": thumb_url}] if thumb_url else [],
        "metrics": {
            "views": _safe_int(video.get("playCount")),
            "likes": _safe_int(video.get("diggCount")),
            "comments": _safe_int(video.get("commentCount")),
            "shares": _safe_int(video.get("shareCount")),
        },
        "published_at": _parse_dt(video.get("createTimeISO")),
        "raw_data": video,
        "comments_raw": video.get("_comments", []),
    }


def _compute_scores(item: dict) -> dict:
    metrics = item["metrics"]
    views = metrics["views"]
    now = datetime.now(timezone.utc)
    published_at = item["published_at"] or now
    hours_since = max((now - published_at).total_seconds() / 3600, 0)

    freshness_score = max(0.0, 100.0 - (hours_since * 2))
    if views < MIN_VIEWS_FOR_ENGAGEMENT:
        engagement_score = 0.0
    else:
        engagement_score = min(
            100.0, ((metrics["likes"] + metrics["comments"] * 2 + metrics["shares"] * 3) / views) * 100
        )

    authority_score = min(100.0, math.log10(item["author_fans"] + 1) * 12) if item["author_fans"] else 40.0

    trend_score = round((freshness_score * 0.4) + (engagement_score * 0.35) + (authority_score * 0.25), 2)

    return {
        "trend_score": trend_score,
        "engagement_score": round(engagement_score, 2),
        "freshness_score": round(freshness_score, 2),
        "authority_score": round(authority_score, 2),
    }


async def _generate_ai_summary(api_key: str, model: str, title: str, content: str) -> dict:
    prompt = (
        f"Caption video TikTok: {title}\n"
        f"Isi: {content[:500]}\n\n"
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


async def process_and_save(db: AsyncSession, run_id: uuid.UUID, topic: str, videos: list[dict]) -> dict:
    total_before_dedupe = len(videos)
    deduped, duplicate_count = _dedupe(videos)
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
        n["scores"] = _compute_scores(n)
        normalized.append(n)

    normalized.sort(key=lambda x: x["scores"]["trend_score"], reverse=True)
    await log_activity(
        db, run_id, AGENT_NAME, "normalize_score",
        f"{len(normalized)} video dinormalisasi+diberi skor, {failed_count} gagal validasi (id/teks kosong)",
    )

    ai_key_info = await get_working_key_for_agent(db, "agent_tiktok")
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
                new_key = await report_key_failure(db, "agent_tiktok", ai_result["error"])
                if new_key:
                    ai_key_info = new_key
                    model = new_key.get("model") or FALLBACK_AI_MODEL
                    if "/" not in model:
                        model = FALLBACK_AI_MODEL
                    retry = await _generate_ai_summary(ai_key_info["api_key"], model, item["title"], item["content"])
                    if "error" not in retry:
                        item["ai_summary"] = retry.get("summary")
                        item["ai_tags"] = retry.get("tags", [])
                        ai_done += 1
        for item in normalized[AI_SUMMARY_LIMIT:]:
            item["ai_summary"] = None
            item["ai_tags"] = []
        await log_activity(
            db, run_id, AGENT_NAME, "ai_summary",
            f"AI summary/tags berhasil utk {ai_done}/{min(len(normalized), AI_SUMMARY_LIMIT)} video, model={model}",
        )
    else:
        for item in normalized:
            item["ai_summary"] = None
            item["ai_tags"] = []
        await log_activity(
            db, run_id, AGENT_NAME, "ai_summary",
            "AI summary dilewati -- agent_tiktok belum punya key aktif", level="warning",
        )

    saved_count = 0
    duplicate_in_db = 0
    try:
        for item in normalized:
            existing = await db.scalar(
                select(Post).where(Post.external_id == item["external_id"], Post.platform == "tiktok")
            )
            old_meta = (existing.metadata_ or {}) if existing else {}
            ai_summary = item["ai_summary"] or old_meta.get("ai_summary")
            ai_tags = item["ai_tags"] or old_meta.get("ai_tags") or []
            prev_topics = old_meta.get("source_topics") or ([old_meta["source_topic"]] if old_meta.get("source_topic") else [])
            source_topics = list(dict.fromkeys([*prev_topics, topic]))
            metadata = {
                "trend_score": item["scores"]["trend_score"],
                "engagement_score": item["scores"]["engagement_score"],
                "freshness_score": item["scores"]["freshness_score"],
                "authority_score": item["scores"]["authority_score"],
                "ai_summary": ai_summary,
                "ai_tags": ai_tags,
                "source_topic": topic,
                "source_topics": source_topics,
                "author_fans": item.get("author_fans"),
                # audience_size (2026-07-24, permintaan user "metadata harus
                # sama dgn platform lain") -- alias SERAGAM lintas platform
                # thd author_fans (TikTok), followers (Facebook/Instagram),
                # channel_subscriber_count (YouTube) -- supaya query lintas
                # platform tidak perlu tau nama field per platform.
                "audience_size": item.get("author_fans"),
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
                    external_id=item["external_id"], platform="tiktok", title=item["title"],
                    content=item["content"], author=item["author"], url=item["url"],
                    media=item["media"], metrics=item["metrics"], metadata_=metadata,
                    raw_data=item["raw_data"], published_at=item["published_at"],
                    collected_at=datetime.now(timezone.utc), is_processed=False, is_near_duplicate=False,
                )
                db.add(post_row)
                await db.flush()
                saved_count += 1

            for c in item.get("comments_raw", []):
                external_comment_id = c.get("cid")
                if not external_comment_id:
                    continue
                existing_comment = await db.scalar(
                    select(Comment).where(Comment.post_id == post_row.id, Comment.external_id == external_comment_id)
                )
                if existing_comment:
                    continue
                comment_row = Comment(
                    id=uuid.uuid4(), post_id=post_row.id, external_id=external_comment_id,
                    content=c.get("text") or "",
                    author=c.get("uniqueId") or "",
                    metadata_={"like_count": c.get("diggCount")},
                    published_at=_parse_dt(c.get("createTimeISO")),
                )
                db.add(comment_row)
                await analyze_and_queue_lexicon(db, comment_row.id, comment_row.content)

        await db.commit()
    except Exception as exc:
        await db.rollback()
        await log_activity(db, run_id, AGENT_NAME, "save_failed", f"Rollback -- gagal simpan: {exc}", level="error")
        raise

    await log_activity(
        db, run_id, AGENT_NAME, "save_done",
        f"Tersimpan: {saved_count} baru, {duplicate_in_db} diperbarui (sudah ada sebelumnya)",
    )

    return {
        "total_video": len(normalized),
        "saved_to_database": saved_count,
        "duplicate_removed": duplicate_count + duplicate_in_db,
        "failed": failed_count,
    }
