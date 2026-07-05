"""
Instagram Pipeline Service — via provider pencarian (Apify + fallback EnsembleData).

Orkestrasi scraping Instagram:
  Provider search-by-username (1 call, auto-fallback) → dataset (post+comment per baris)
  → group per post → simpan posts + comments ke DB → lexicon sentiment

Lihat docs/apify-instagram-method.md untuk detail Actor & gotcha input schema.
Lihat docs/trend-recommendations.md untuk alur budget harian trend_recommendations.
Lihat app/services/instagram/providers/ untuk abstraksi provider search.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.services.instagram.providers.registry import search_profile_with_fallback

MAX_POSTS = 12
MAX_COMMENTS = 50

_HASHTAG_RE = re.compile(r"#(\w+)")


def _extract_hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(_HASHTAG_RE.findall(text or "")))


def _shortcode_from_url(url: str) -> str:
    # https://www.instagram.com/p/DZ6Heo5jdbf/ atau /reel/xxx/
    match = re.search(r"/(?:p|reel)/([^/]+)/?", url or "")
    return match.group(1) if match else ""


def _comment_external_id(post_ext_id: str, author: str, text: str, timestamp: str) -> str:
    raw = f"{post_ext_id}|{author}|{text}|{timestamp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def scrape_instagram_posts(
    db: AsyncSession,
    username: str,
    max_posts: int = 1,
    max_comments: int = 10,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Scrape Instagram posts dari username via Apify:
      1. Panggil Actor Apify (1 call) → dataset baris post+comment
      2. Group baris per post (dedup by shortcode/URL)
      3. Simpan post baru ke DB, simpan komentar baru, lexicon sentiment
      4. Return dict: user_info + posts + sentimen global

    Deduplication: post yang sudah ada di DB (external_id = shortcode) dilewati
    untuk insert ulang (tapi tetap dipakai untuk cek komentar baru).
    Komentar dedup via hash (post + author + text + timestamp) karena Apify
    tidak menyediakan comment_id yang stabil.
    """
    max_posts = min(max_posts, MAX_POSTS)
    max_comments = min(max_comments, MAX_COMMENTS)

    errors: list[str] = []
    user_info: dict[str, Any] = {"username": username}
    provider_used: str | None = None

    try:
        rows, provider_used = await search_profile_with_fallback(username, max_posts, max_comments)
    except Exception as exc:
        errors.append(f"provider: {exc}")
        rows = []

    if not rows:
        return {
            "username": username,
            "user_info": user_info,
            "posts_scraped": 0,
            "posts_saved": 0,
            "errors": errors,
            "provider_used": provider_used,
            "_posts_data": [],
        }

    # ── Group baris per post (by postUrl) ─────────────────────────────────────
    posts_by_url: dict[str, dict[str, Any]] = {}
    for row in rows:
        post_url = row.get("postUrl", "")
        if not post_url:
            continue
        bucket = posts_by_url.setdefault(post_url, {"meta": row, "comments": []})
        if row.get("commentText"):
            bucket["comments"].append(row)
        if row.get("profileFollowers"):
            user_info["followers"] = row.get("profileFollowers")
        if row.get("profileDescription"):
            user_info["biography"] = row.get("profileDescription")

    if not posts_by_url:
        return {
            "username": username,
            "user_info": user_info,
            "posts_scraped": 0,
            "posts_saved": 0,
            "errors": errors,
            "_posts_data": [],
        }

    # ── Cek post yang sudah ada di DB ──────────────────────────────────────────
    shortcodes = {url: _shortcode_from_url(url) for url in posts_by_url}
    ext_ids = [sc for sc in shortcodes.values() if sc]

    existing_posts: dict[str, Post] = {}
    if ext_ids:
        rows_existing = (await db.scalars(
            select(Post).where(Post.platform == "instagram", Post.external_id.in_(ext_ids))
        )).all()
        existing_posts = {p.external_id: p for p in rows_existing}

    posts_saved_count = 0
    posts_data: list[dict[str, Any]] = []

    for post_url, bucket in posts_by_url.items():
        meta_row = bucket["meta"]
        shortcode = shortcodes[post_url]
        if not shortcode:
            continue

        post_obj = existing_posts.get(shortcode)
        is_new = post_obj is None

        if is_new:
            caption = meta_row.get("postDescription", "") or ""
            published_at = None
            if meta_row.get("postTimestamp"):
                try:
                    published_at = datetime.fromisoformat(meta_row["postTimestamp"].replace("Z", "+00:00"))
                except ValueError:
                    published_at = None

            post_obj = Post(
                id=uuid.uuid4(),
                keyword_id=keyword_id,
                external_id=shortcode,
                platform="instagram",
                content=caption,
                author=username,
                url=post_url,
                published_at=published_at,
                collected_at=datetime.now(timezone.utc),
                metadata_={
                    "likes":     meta_row.get("postLikesCount", 0),
                    "comments":  meta_row.get("postCommentsCount", 0),
                    "shortcode": shortcode,
                    "hashtags":  _extract_hashtags(caption),
                    "source":    "apify",
                },
            )
            db.add(post_obj)
            await db.flush()
            posts_saved_count += 1

        # ── Komentar baru ──────────────────────────────────────────────────────
        new_comments: list[Comment] = []
        if bucket["comments"] and post_obj.id:
            existing_cmt_ids: set[str] = set(
                (await db.scalars(select(Comment.external_id).where(Comment.post_id == post_obj.id))).all()
            )
            for cmt in bucket["comments"][:max_comments]:
                text_ = cmt.get("commentText", "")
                author = cmt.get("commentAuthor", "")
                timestamp = cmt.get("commentTimestamp", "")
                ext_cmt_id = _comment_external_id(shortcode, author, text_, timestamp)
                if ext_cmt_id in existing_cmt_ids:
                    continue

                comment = Comment(
                    post_id=post_obj.id,
                    external_id=ext_cmt_id,
                    content=text_,
                    author=author,
                    published_at=None,
                    metadata_={
                        "like_count": cmt.get("commentLikesCount", 0),
                    },
                )
                db.add(comment)
                new_comments.append(comment)
                existing_cmt_ids.add(ext_cmt_id)

            if new_comments:
                await db.flush()
                await _analyze_comments_lexicon(db, new_comments, keyword_id)

        posts_data.append({
            "post_obj":  post_obj,
            "comments":  new_comments,
            "shortcode": shortcode,
            "is_new":    is_new,
        })

    await db.commit()

    return {
        "username":      username,
        "user_info":     user_info,
        "posts_scraped": len(posts_by_url),
        "posts_saved":   posts_saved_count,
        "errors":        errors,
        "provider_used": provider_used,
        "_posts_data":   posts_data,
    }


async def _analyze_comments_lexicon(
    db: AsyncSession,
    comments: list[Comment],
    keyword_id: uuid.UUID | None,
) -> int:
    from app.ai.lexicon.service import analyze

    count = 0
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
        count += 1
    return count
