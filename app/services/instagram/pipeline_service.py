"""
Instagram Pipeline Service.

Orkestrasi scraping Instagram:
  get_user_info → get_user_posts (max 10) → get_post_comments per post
  → simpan posts + comments ke DB → lexicon sentiment
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.instagram.connector import InstagramConnector
from app.services.processing.normalizer import InstagramNormalizer

MAX_POSTS = 10
MAX_COMMENTS = 50


async def scrape_instagram_posts(
    db: AsyncSession,
    username: str,
    max_posts: int = 10,
    max_comments: int = 20,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Scrape Instagram posts dari username:
      1. Ambil user info → user_id, followers, bio
      2. Ambil posts (maks max_posts)
      3. Per post: simpan ke DB, ambil komentar, simpan komentar, lexicon sentiment
      4. Return dict: user_info + posts + sentimen global

    Deduplication: post yang sudah ada di DB (external_id match) dilewati.
    Komentar yang sudah ada (external_id match) dilewati.
    """
    max_posts = min(max_posts, MAX_POSTS)
    max_comments = min(max_comments, MAX_COMMENTS)
    normalizer = InstagramNormalizer()

    user_info: dict[str, Any] = {}
    posts_saved: list[dict] = []
    errors: list[str] = []

    async with EnsembleDataClient() as client:
        connector = InstagramConnector(client)

        # ── 1. User info ──────────────────────────────────────────────────────
        try:
            raw_info = await connector.get_user_info(username)
            user_data = connector.extract_user_info(raw_info)
            user_id = (
                user_data.get("pk")
                or user_data.get("id")
                or user_data.get("user_id")
            )
            user_info = {
                "user_id":        str(user_id) if user_id else None,
                "username":       user_data.get("username", username),
                "full_name":      user_data.get("full_name", ""),
                "biography":      user_data.get("biography", ""),
                "followers":      user_data.get("follower_count") or user_data.get("edge_followed_by", {}).get("count", 0),
                "following":      user_data.get("following_count") or user_data.get("edge_follow", {}).get("count", 0),
                "post_count":     user_data.get("media_count") or user_data.get("edge_owner_to_timeline_media", {}).get("count", 0),
                "profile_pic_url": user_data.get("profile_pic_url", ""),
                "is_verified":    user_data.get("is_verified", False),
                "is_private":     user_data.get("is_private", False),
            }
        except Exception as exc:
            errors.append(f"get_user_info: {exc}")
            user_id = None

        if not user_id:
            return {
                "username": username,
                "user_info": user_info,
                "posts_scraped": 0,
                "posts_saved": 0,
                "errors": errors,
                "items": [],
            }

        # ── 2. User posts ─────────────────────────────────────────────────────
        raw_items: list[dict] = []
        try:
            raw_posts = await connector.get_user_posts(user_id, depth=1)
            raw_items = connector.extract_posts(raw_posts)[:max_posts]
        except Exception as exc:
            errors.append(f"get_user_posts: {exc}")

        if not raw_items:
            return {
                "username": username,
                "user_info": user_info,
                "posts_scraped": 0,
                "posts_saved": 0,
                "errors": errors,
                "items": [],
            }

        # ── 3. Normalize + dedup + save posts ─────────────────────────────────
        posts_normalized = normalizer.normalize(raw_items, keyword_id)
        ext_ids = [p.external_id for p in posts_normalized]

        existing_ext_ids: set[str] = set(
            (await db.scalars(
                select(Post.external_id).where(
                    Post.platform == "instagram",
                    Post.external_id.in_(ext_ids),
                )
            )).all()
        )

        new_posts = [p for p in posts_normalized if p.external_id not in existing_ext_ids]

        # Tag source di metadata
        for p in new_posts:
            meta = p.metadata_ or {}
            meta["source"] = "instagram_scrape"
            p.metadata_ = meta

        if new_posts:
            db.add_all(new_posts)
            await db.flush()

        # Map external_id → Post (gabung yang baru + yang sudah ada)
        all_ext_to_post: dict[str, Post] = {p.external_id: p for p in new_posts}

        # Ambil yang sudah ada dari DB untuk populate response
        if existing_ext_ids:
            existing_posts = (await db.scalars(
                select(Post).where(
                    Post.platform == "instagram",
                    Post.external_id.in_(existing_ext_ids),
                )
            )).all()
            for p in existing_posts:
                all_ext_to_post[p.external_id] = p

        await db.commit()

        # ── 4. Per post: ambil komentar + lexicon ─────────────────────────────
        for raw_item in raw_items:
            media_id = connector.extract_post_id(raw_item)
            shortcode = connector.extract_shortcode(raw_item)
            post_obj = all_ext_to_post.get(media_id)
            if not post_obj or not post_obj.id:
                continue

            comments_data: list[dict] = []
            if max_comments > 0:
                try:
                    raw_cmts = await connector.get_post_comments(
                        media_id=media_id,
                        sorting="popular",
                    )
                    comments_data = connector.extract_comments(raw_cmts)[:max_comments]
                except Exception as exc:
                    errors.append(f"get_post_comments({media_id}): {exc}")

            # Simpan komentar baru
            new_comments: list[Comment] = []
            if comments_data:
                existing_cmt_ids: set[str] = set(
                    (await db.scalars(
                        select(Comment.external_id).where(
                            Comment.post_id == post_obj.id
                        )
                    )).all()
                )

                for cmt in comments_data:
                    ext_cmt_id = str(
                        cmt.get("pk") or cmt.get("id") or cmt.get("comment_id") or ""
                    )
                    if not ext_cmt_id or ext_cmt_id in existing_cmt_ids:
                        continue

                    text = (
                        cmt.get("text")
                        or (cmt.get("caption") or {}).get("text", "")
                        or ""
                    )
                    author = (cmt.get("user") or {}).get("username", "") or cmt.get("username", "")
                    user_pk = str((cmt.get("user") or {}).get("pk", ""))

                    comment = Comment(
                        post_id=post_obj.id,
                        external_id=ext_cmt_id,
                        content=text,
                        author=author,
                        published_at=None,
                        metadata_={
                            "like_count":      cmt.get("comment_like_count", 0),
                            "child_comment_count": cmt.get("child_comment_count", 0),
                            "author_user_id":  user_pk,
                        },
                    )
                    db.add(comment)
                    new_comments.append(comment)
                    existing_cmt_ids.add(ext_cmt_id)

                if new_comments:
                    await db.flush()
                    await _analyze_comments_lexicon(db, new_comments, keyword_id)
                    await db.commit()

            posts_saved.append({
                "post_obj":   post_obj,
                "comments":   new_comments,
                "raw_item":   raw_item,
                "media_id":   media_id,
                "shortcode":  shortcode,
                "is_new":     media_id not in existing_ext_ids,
            })

    return {
        "username":     username,
        "user_info":    user_info,
        "posts_scraped": len(raw_items),
        "posts_saved":  sum(1 for p in posts_saved if p["is_new"]),
        "errors":       errors,
        "_posts_data":  posts_saved,
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
