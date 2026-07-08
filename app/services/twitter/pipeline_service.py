"""
Twitter/X Pipeline Service — Fase 1 (scrape dasar).

Mirroring app/services/tiktok/pipeline_service.py, TAPI actor Twitter butuh
CALL TERPISAH per tweet untuk ambil balasan (beda dengan TikTok yang cukup 1
dataset URL tambahan) — lihat app/integrations/apify/twitter.py.

Hashtag Twitter DIAMBIL VIA REGEX dari `text` (BUKAN dari field `entities`
actor ini) karena struktur entities untuk hashtag belum terkonfirmasi live
(kosong di semua sampel yang diuji, 08 Juli 2026).

Belum ada integrasi batch harian trend_recommendations (Subsistem B) —
menyusul Fase 2, mirroring app/services/facebook/trend_scrape_service.py.
"""
from __future__ import annotations

import re
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
# Dibatasi lebih rendah dari TikTok (30) karena tiap balasan butuh 1 actor
# call TERPISAH per tweet (biaya lebih tinggi, lihat app/integrations/apify/twitter.py)
MAX_COMMENTS = 20

_HASHTAG_RE = re.compile(r"#(\w+)")


async def _analyze_lexicon(db: AsyncSession, comments: list[Comment], keyword_id: uuid.UUID | None) -> None:
    """Sentimen lexicon sederhana untuk balasan — sama seperti Facebook/TikTok,
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


def _parse_twitter_date(ts: str | None) -> datetime | None:
    """Format asli Twitter: 'Tue Jul 07 10:15:23 +0000 2026' — BUKAN ISO,
    format klasik Twitter API v1.1 (lihat docstring integrations/apify/twitter.py)."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


async def scrape_twitter_posts_via_provider(
    db: AsyncSession,
    identifier: str,
    max_posts: int = 5,
    max_comments: int = 10,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Scrape Twitter/X via Apify (`danek/twitter-scraper`) untuk satu akun,
    simpan tweet+balasan+hashtag(regex dari text), dispatch sentimen post
    (IndoBERT, async Celery) + balasan (lexicon, inline).

    Dedup: akun yang sudah discrape HARI INI di-skip (tidak panggil Apify
    lagi) — pola sama dengan Facebook/TikTok.
    """
    from app.integrations.apify.twitter import scrape_twitter_via_apify

    max_posts = min(max_posts, MAX_POSTS)
    max_comments = min(max_comments, MAX_COMMENTS)

    today_count = await db.scalar(
        select(func.count()).select_from(Post).where(
            Post.platform == "twitter",
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
        raw_posts = await scrape_twitter_via_apify(identifier, max_posts, max_comments)
    except Exception as exc:
        errors.append(f"provider: {exc}")
        raw_posts = []

    posts_saved = 0
    posts_found = 0
    for raw in raw_posts:
        ext_id = raw.get("tweet_id")
        if not ext_id:
            continue
        posts_found += 1

        existing = await db.scalar(
            select(Post).where(Post.platform == "twitter", Post.external_id == ext_id)
        )
        if existing is not None:
            continue

        author = raw.get("author") or {}
        screen_name = author.get("screen_name") or identifier
        content = raw.get("text", "")
        post_obj = Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=ext_id,
            platform="twitter",
            content=content,
            author=screen_name,
            url=f"https://x.com/{screen_name}/status/{ext_id}",
            published_at=_parse_twitter_date(raw.get("created_at")),
            collected_at=datetime.now(timezone.utc),
            metadata_={
                "likes":     _to_int(raw.get("favorites")),
                "retweets":  _to_int(raw.get("retweets")),
                "quotes":    _to_int(raw.get("quotes")),
                "views":     _to_int(raw.get("views")),
                "comments":  _to_int(raw.get("replies")),
                "followers": _to_int(author.get("followers_count")),
                "source":    "apify",
            },
        )
        db.add(post_obj)
        await db.flush()
        posts_saved += 1

        for tag in dict.fromkeys(_HASHTAG_RE.findall(content)):
            db.add(Entity(post_id=post_obj.id, text=tag, entity_type="HASHTAG"))

        from app.workers.ai_worker import analyze_post_task
        analyze_post_task.delay(str(post_obj.id), run_sentiment=True, run_ner=False, run_embedding=False)

        # ── Balasan (call terpisah per tweet, sudah di-fetch oleh scrape_twitter_via_apify) ──
        raw_replies = raw.get("_replies") or []
        if raw_replies:
            new_comments: list[Comment] = []
            for rep in raw_replies[:max_comments]:
                rep_id = rep.get("id")
                if not rep_id:
                    continue
                rep_author = rep.get("author") or {}
                comment = Comment(
                    post_id=post_obj.id,
                    external_id=str(rep_id),
                    content=rep.get("text") or rep.get("display_text", ""),
                    author=rep_author.get("screen_name", ""),
                    published_at=_parse_twitter_date(rep.get("created_at")),
                    metadata_={"like_count": _to_int(rep.get("likes"))},
                )
                db.add(comment)
                new_comments.append(comment)

            if new_comments:
                await db.flush()
                await _analyze_lexicon(db, new_comments, keyword_id)

    await db.commit()

    return {
        "identifier":    identifier,
        "posts_scraped": posts_found,
        "posts_saved":   posts_saved,
        "errors":        errors,
        "provider_used": "apify" if raw_posts or not errors else None,
    }
