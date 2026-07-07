"""
TikTok Pipeline Service — Fase 1 (scrape dasar).

Mirroring app/services/facebook/pipeline_service.py (scrape_facebook_posts_via_provider),
TAPI lebih sederhana karena TikTok cuma butuh SATU actor Apify (lihat
app/integrations/apify/tiktok.py) untuk profil+komentar sekaligus.

Belum ada integrasi trend_recommendations (Subsistem A/B) — itu Fase 2.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.entities.models import Entity
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis

MAX_POSTS = 20
MAX_COMMENTS = 30


async def _analyze_lexicon(db: AsyncSession, comments: list[Comment], keyword_id: uuid.UUID | None) -> None:
    """Sentimen lexicon sederhana untuk komentar — sama seperti Facebook/Instagram,
    BUKAN IndoBERT (itu untuk post, dispatch via Celery, lihat di bawah)."""
    from app.ai.lexicon.service import analyze

    for comment in comments:
        if not comment.content:
            continue
        res = analyze(comment.content)
        db.add(LexiconAnalysis(
            comment_id=comment.id,
            keyword_id=keyword_id,
            matched_positive=res.matched_positive,
            matched_negative=res.matched_negative,
            removed_stopwords=res.removed_stopwords,
            score=res.score,
            label=res.label,
        ))


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


async def scrape_tiktok_posts_via_provider(
    db: AsyncSession,
    identifier: str,
    max_posts: int = 5,
    max_comments: int = 10,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Scrape TikTok via Apify (`clockworks/tiktok-scraper`) untuk satu akun,
    simpan post+komentar+hashtag(entities), dispatch sentimen post (IndoBERT,
    async Celery) + komentar (lexicon, inline).

    Dedup: akun yang sudah discrape HARI INI di-skip (tidak panggil Apify
    lagi) — pola sama dengan Facebook/Instagram
    (app/services/facebook/pipeline_service.py).
    """
    from app.integrations.apify.tiktok import scrape_tiktok_via_apify

    max_posts = min(max_posts, MAX_POSTS)
    max_comments = min(max_comments, MAX_COMMENTS)

    today_count = await db.scalar(
        select(func.count()).select_from(Post).where(
            Post.platform == "tiktok",
            Post.author == identifier,
            func.date(Post.collected_at) == datetime.now(timezone.utc).date(),
        )
    )
    if today_count:
        return {
            "identifier": identifier, "posts_scraped": today_count, "posts_saved": 0,
            "errors": [], "provider_used": "cached_today", "already_scraped_today": True,
        }

    errors: list[str] = []
    try:
        raw_posts = await scrape_tiktok_via_apify(identifier, max_posts, max_comments)
    except Exception as exc:
        errors.append(f"provider: {exc}")
        raw_posts = []

    posts_saved = 0
    for raw in raw_posts:
        ext_id = raw.get("id")
        if not ext_id:
            continue

        existing = await db.scalar(
            select(Post).where(Post.platform == "tiktok", Post.external_id == ext_id)
        )
        if existing is not None:
            continue

        author_meta = raw.get("authorMeta") or {}
        post_obj = Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=ext_id,
            platform="tiktok",
            content=raw.get("text", ""),
            author=author_meta.get("name") or identifier,
            url=raw.get("webVideoUrl") or f"https://www.tiktok.com/@{identifier}/video/{ext_id}",
            published_at=_parse_iso(raw.get("createTimeISO")),
            collected_at=datetime.now(timezone.utc),
            metadata_={
                "likes":     raw.get("diggCount", 0),
                "shares":    raw.get("shareCount", 0),
                "views":     raw.get("playCount", 0),
                "comments":  raw.get("commentCount", 0),
                "collects":  raw.get("collectCount", 0),
                "followers": author_meta.get("fans", 0),
                "source":    "apify",
            },
        )
        db.add(post_obj)
        await db.flush()
        posts_saved += 1

        # Hashtag SUDAH terstruktur dari actor, tidak perlu regex (beda dengan Facebook)
        for tag in raw.get("hashtags") or []:
            name = tag.get("name") if isinstance(tag, dict) else None
            if name:
                db.add(Entity(post_id=post_obj.id, text=name, entity_type="HASHTAG"))

        from app.workers.ai_worker import analyze_post_task
        analyze_post_task.delay(str(post_obj.id), run_sentiment=True, run_ner=False, run_embedding=False)

        # ── Komentar (dataset terpisah, sudah di-fetch oleh scrape_tiktok_via_apify) ──
        raw_comments = raw.get("_comments") or []
        if raw_comments:
            new_comments: list[Comment] = []
            for cmt in raw_comments[:max_comments]:
                cmt_id = cmt.get("cid")
                if not cmt_id:
                    continue
                comment = Comment(
                    post_id=post_obj.id,
                    external_id=str(cmt_id),
                    content=cmt.get("text", ""),
                    # TikTok tidak kasih nama tampilan komentator di actor ini,
                    # cuma uniqueId/uid numerik — keterbatasan data provider.
                    author=cmt.get("uniqueId") or cmt.get("uid") or "",
                    published_at=_parse_iso(cmt.get("createTimeISO")),
                    metadata_={"like_count": cmt.get("diggCount", 0)},
                )
                db.add(comment)
                new_comments.append(comment)

            if new_comments:
                await db.flush()
                await _analyze_lexicon(db, new_comments, keyword_id)

    await db.commit()

    return {
        "identifier":    identifier,
        "posts_scraped": len(raw_posts),
        "posts_saved":   posts_saved,
        "errors":        errors,
        "provider_used": "apify" if raw_posts or not errors else None,
    }
