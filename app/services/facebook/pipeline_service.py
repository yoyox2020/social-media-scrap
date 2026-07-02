"""
Facebook Pipeline Service.
Scrape posts dari page/user: page_info → posts → comments → lexicon sentiment
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.integrations.facebook.connector import FacebookConnector
from app.shared.config import settings


async def scrape_facebook_posts(
    db: AsyncSession,
    identifier: str,
    max_posts: int = 10,
    max_comments: int = 20,
    keyword_id: uuid.UUID | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """
    Scrape Facebook page/user posts:
      1. Ambil info page/profil
      2. Ambil posts (maks max_posts)
      3. Per post: ambil komentar, simpan, lexicon sentiment
    """
    token = access_token or settings.facebook_access_token
    connector = FacebookConnector(token)
    errors: list[str] = []
    page_info: dict = {}
    page_id: str = identifier

    # ── 1. Page / User info ───────────────────────────────────────────────────
    try:
        raw_info = await connector.get_page_info(identifier)
        page_id = raw_info.get("id") or identifier
        pic_data = (raw_info.get("picture") or {}).get("data", {})
        page_info = {
            "page_id":   page_id,
            "name":      raw_info.get("name", ""),
            "username":  raw_info.get("username", identifier),
            "fans":      raw_info.get("fan_count") or raw_info.get("followers_count", 0),
            "about":     raw_info.get("about", ""),
            "category":  raw_info.get("category", ""),
            "website":   raw_info.get("website", ""),
            "link":      raw_info.get("link", ""),
            "picture":   pic_data.get("url", ""),
        }
    except Exception as exc:
        errors.append(f"get_page_info: {exc}")

    # ── 2. Posts ──────────────────────────────────────────────────────────────
    raw_posts: list[dict] = []
    try:
        raw = await connector.get_page_posts(page_id, limit=max_posts)
        raw_posts = connector.extract_posts(raw)[:max_posts]
    except Exception as exc:
        # Fallback: coba sebagai user feed (untuk personal profile)
        try:
            raw = await connector.get_user_feed(page_id, limit=max_posts)
            raw_posts = connector.extract_posts(raw)[:max_posts]
        except Exception as exc2:
            errors.append(f"get_posts: {exc} | feed: {exc2}")

    # ── 3. Normalize + dedup + save posts ────────────────────────────────────
    ext_ids = [p["id"] for p in raw_posts if p.get("id")]
    existing_ext_ids: set[str] = set()
    if ext_ids:
        existing_ext_ids = set((await db.scalars(
            select(Post.external_id).where(
                Post.platform == "facebook",
                Post.external_id.in_(ext_ids),
            )
        )).all())

    new_posts: list[Post] = []
    ext_to_post: dict[str, Post] = {}

    for raw_p in raw_posts:
        post_id = raw_p.get("id", "")
        if not post_id:
            continue

        likes = (raw_p.get("likes") or {}).get("summary", {}).get("total_count", 0)
        comments_count = (raw_p.get("comments") or {}).get("summary", {}).get("total_count", 0)
        shares = (raw_p.get("shares") or {}).get("count", 0)

        published_at: datetime | None = None
        created_str = raw_p.get("created_time", "")
        if created_str:
            try:
                published_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except Exception:
                pass

        post = Post(
            platform="facebook",
            external_id=post_id,
            content=raw_p.get("message") or raw_p.get("story") or "",
            author=page_info.get("username") or identifier,
            url=raw_p.get("permalink_url") or f"https://www.facebook.com/{post_id}",
            published_at=published_at,
            collected_at=datetime.now(timezone.utc),
            keyword_id=keyword_id,
            metadata_={
                "likes":     likes,
                "comments":  comments_count,
                "shares":    shares,
                "thumbnail": raw_p.get("full_picture", ""),
                "story":     raw_p.get("story", ""),
                "source":    "facebook_scrape",
            },
        )

        if post_id not in existing_ext_ids:
            db.add(post)
            new_posts.append(post)
            ext_to_post[post_id] = post

    if new_posts:
        await db.flush()

    # Ambil yang sudah ada
    if existing_ext_ids:
        existing_posts = (await db.scalars(
            select(Post).where(Post.platform == "facebook", Post.external_id.in_(existing_ext_ids))
        )).all()
        for p in existing_posts:
            ext_to_post[p.external_id] = p

    await db.commit()

    # ── 4. Komentar per post + lexicon ───────────────────────────────────────
    for raw_p in raw_posts:
        post_id = raw_p.get("id", "")
        post_obj = ext_to_post.get(post_id)
        if not post_obj or not post_obj.id or max_comments == 0:
            continue

        comments_raw: list[dict] = []
        try:
            raw_cmts = await connector.get_post_comments(post_id, limit=max_comments)
            comments_raw = connector.extract_comments(raw_cmts)
        except Exception as exc:
            errors.append(f"comments({post_id}): {exc}")
            continue

        existing_cmt_ids: set[str] = set((await db.scalars(
            select(Comment.external_id).where(Comment.post_id == post_obj.id)
        )).all())

        new_cmts: list[Comment] = []
        for cmt in comments_raw[:max_comments]:
            cmt_id = str(cmt.get("id", ""))
            if not cmt_id or cmt_id in existing_cmt_ids:
                continue
            from_user = cmt.get("from") or {}
            comment = Comment(
                post_id=post_obj.id,
                external_id=cmt_id,
                content=cmt.get("message", ""),
                author=from_user.get("name", ""),
                published_at=None,
                metadata_={
                    "like_count": cmt.get("like_count", 0),
                    "author_id":  from_user.get("id", ""),
                },
            )
            db.add(comment)
            new_cmts.append(comment)
            existing_cmt_ids.add(cmt_id)

        if new_cmts:
            await db.flush()
            await _analyze_lexicon(db, new_cmts, keyword_id)
            await db.commit()

    return {
        "identifier":    identifier,
        "page_info":     page_info,
        "posts_scraped": len(raw_posts),
        "posts_saved":   len(new_posts),
        "errors":        errors,
    }


async def _analyze_lexicon(
    db: AsyncSession,
    comments: list[Comment],
    keyword_id: uuid.UUID | None,
) -> None:
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
