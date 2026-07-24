"""agent-struktur-data utk Facebook (2026-07-24) -- pola SAMA PERSIS
dgn versi TikTok (app/agents/tiktok/struktur_data.py), field disesuaikan
dgn struktur data Facebook ASLI yg SUDAH diverifikasi dari 50 post yg
ada di DB SEBELUM file ini ditulis (lihat app/services/facebook_metadata/
service.py utk detail lengkap): TIDAK ada "views" (selalu 0, Facebook
tidak expose), `title` SELALU kosong (content = teks utama), author
followers dari halaman/profil (bukan channel subscriber count).

engagement_score TANPA "views" sbg pembagi (beda dari YouTube/TikTok yg
pakai views) -- Facebook tidak punya metrik views publik, jadi dihitung
dari total likes+comments*2+shares*3 murni (skala absolut, DINORMALISASI
log supaya post viral (ribuan interaksi) tidak otomatis 100 sementara
post kecil (1-2 interaksi) tidak otomatis 0 -- beda formula dari
YouTube/TikTok yg emang py pembagi views yg valid)."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
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


def _dedupe(posts: list[dict]) -> tuple[list[dict], int]:
    seen: dict[str, dict] = {}
    duplicate_count = 0
    for p in posts:
        pid = p.get("external_id")
        if not pid:
            continue
        if pid in seen:
            duplicate_count += 1
            continue
        seen[pid] = p
    return list(seen.values()), duplicate_count


def _compute_scores(item: dict) -> dict:
    metrics = item["metrics"]
    now = datetime.now(timezone.utc)
    published_at = item["published_at"] or now
    hours_since = max((now - published_at).total_seconds() / 3600, 0)

    freshness_score = max(0.0, 100.0 - (hours_since * 2))

    interactions = metrics["likes"] + metrics["comments"] * 2 + metrics["shares"] * 3
    # Skala log (bukan rasio thd views spt platform lain, Facebook tidak
    # py views publik) -- ~1000 interaksi -> skor ~90-100, dst.
    engagement_score = min(100.0, math.log10(interactions + 1) * 30)

    followers = item.get("author_followers")
    authority_score = min(100.0, math.log10(followers + 1) * 12) if followers else 40.0

    trend_score = round((freshness_score * 0.4) + (engagement_score * 0.35) + (authority_score * 0.25), 2)

    return {
        "trend_score": trend_score,
        "engagement_score": round(engagement_score, 2),
        "freshness_score": round(freshness_score, 2),
        "authority_score": round(authority_score, 2),
    }


async def _generate_ai_summary(api_key: str, model: str, content: str) -> dict:
    prompt = (
        f"Isi post Facebook: {content[:500]}\n\n"
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


async def process_and_save(db: AsyncSession, run_id: uuid.UUID, topic: str, posts: list[dict]) -> dict:
    total_before_dedupe = len(posts)
    deduped, duplicate_count = _dedupe(posts)
    await log_activity(
        db, run_id, AGENT_NAME, "merge_dedupe",
        f"Merge {total_before_dedupe} post mentah -> {len(deduped)} unik ({duplicate_count} duplikat dihapus)",
    )

    normalized: list[dict] = []
    failed_count = 0
    for p in deduped:
        p["published_at"] = _parse_dt(p.get("published_at_raw"))
        if not p.get("external_id") or not (p.get("content") or p.get("author")):
            failed_count += 1
            continue
        p["scores"] = _compute_scores(p)
        normalized.append(p)

    normalized.sort(key=lambda x: x["scores"]["trend_score"], reverse=True)
    await log_activity(
        db, run_id, AGENT_NAME, "normalize_score",
        f"{len(normalized)} post dinormalisasi+diberi skor, {failed_count} gagal validasi (id/teks/author kosong)",
    )

    ai_key_info = await get_working_key_for_agent(db, "agent_facebook")
    ai_done = 0
    rotated = False
    model = FALLBACK_AI_MODEL
    if ai_key_info and ai_key_info.get("api_key"):
        model = ai_key_info.get("model") or FALLBACK_AI_MODEL
        if "/" not in model:
            model = FALLBACK_AI_MODEL
        for item in normalized[:AI_SUMMARY_LIMIT]:
            ai_result = await _generate_ai_summary(ai_key_info["api_key"], model, item["content"])
            if "error" not in ai_result:
                item["ai_summary"] = ai_result.get("summary")
                item["ai_tags"] = ai_result.get("tags", [])
                ai_done += 1
                continue
            item["ai_summary"] = None
            item["ai_tags"] = []
            if not rotated and ai_result.get("status_code") in AI_KEY_FAILURE_STATUS_CODES:
                rotated = True
                new_key = await report_key_failure(db, "agent_facebook", ai_result["error"])
                if new_key:
                    ai_key_info = new_key
                    model = new_key.get("model") or FALLBACK_AI_MODEL
                    if "/" not in model:
                        model = FALLBACK_AI_MODEL
                    retry = await _generate_ai_summary(ai_key_info["api_key"], model, item["content"])
                    if "error" not in retry:
                        item["ai_summary"] = retry.get("summary")
                        item["ai_tags"] = retry.get("tags", [])
                        ai_done += 1
        for item in normalized[AI_SUMMARY_LIMIT:]:
            item["ai_summary"] = None
            item["ai_tags"] = []
        await log_activity(
            db, run_id, AGENT_NAME, "ai_summary",
            f"AI summary/tags berhasil utk {ai_done}/{min(len(normalized), AI_SUMMARY_LIMIT)} post, model={model}",
        )
    else:
        for item in normalized:
            item["ai_summary"] = None
            item["ai_tags"] = []
        await log_activity(
            db, run_id, AGENT_NAME, "ai_summary",
            "AI summary dilewati -- agent_facebook belum punya key aktif", level="warning",
        )

    saved_count = 0
    duplicate_in_db = 0
    try:
        for item in normalized:
            existing = await db.scalar(
                select(Post).where(Post.external_id == item["external_id"], Post.platform == "facebook")
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
                "followers": item.get("author_followers"),
                "audience_size": item.get("author_followers"),
                "source": "apify",
            }
            if existing:
                existing.content = item["content"]
                existing.author = item["author"]
                existing.url = item["url"]
                existing.metrics = item["metrics"]
                existing.metadata_ = metadata
                existing.raw_data = item["raw_data"]
                existing.collected_at = datetime.now(timezone.utc)
                duplicate_in_db += 1
            else:
                db.add(Post(
                    external_id=item["external_id"], platform="facebook", title=None,
                    content=item["content"], author=item["author"], url=item["url"],
                    media=[], metrics=item["metrics"], metadata_=metadata,
                    raw_data=item["raw_data"], published_at=item["published_at"],
                    collected_at=datetime.now(timezone.utc), is_processed=False, is_near_duplicate=False,
                ))
                saved_count += 1

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
        "total_post": len(normalized),
        "saved_to_database": saved_count,
        "duplicate_removed": duplicate_count + duplicate_in_db,
        "failed": failed_count,
    }
