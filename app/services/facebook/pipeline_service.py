"""
Facebook Pipeline Service.

Dua jalur scrape yang SENGAJA terpisah:
  - scrape_facebook_posts()              — Meta Graph API resmi (token
    sendiri), CUMA bisa untuk Page yang dikelola sendiri (dipakai
    GET /facebook/posts, ad-hoc).
  - scrape_facebook_posts_via_provider()  — provider abstraction (Apify,
    siap auto-switch), untuk akun MANAPUN termasuk yang ditemukan AI
    discovery (dipakai pipeline trend_recommendations). Lihat
    docs/flow scrape/flow-scrap-facebook.md untuk alasan token Meta resmi
    tidak dipakai di jalur ini (terverifikasi live: diblokir untuk page
    di luar milik sendiri).
"""
from __future__ import annotations

import hashlib
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
from app.integrations.facebook.connector import FacebookConnector
from app.services.processing.normalizer import _detect_lang, _extract_hashtags, _media_list
from app.shared.apify_errors import tag_if_quota_error
from app.shared.config import settings

MAX_POSTS = 12
MAX_COMMENTS = 50

_HASHTAG_RE = re.compile(r"#(\w+)")


def _extract_hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(_HASHTAG_RE.findall(text or "")))


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

        content = raw_p.get("message") or raw_p.get("story") or ""
        thumbnail = raw_p.get("full_picture", "")

        post = Post(
            platform="facebook",
            external_id=post_id,
            content=content,
            author=page_info.get("username") or identifier,
            url=raw_p.get("permalink_url") or f"https://www.facebook.com/{post_id}",
            published_at=published_at,
            collected_at=datetime.now(timezone.utc),
            keyword_id=keyword_id,
            tags=_extract_hashtags(content),
            media=_media_list(thumbnail),
            metrics={"views": 0, "likes": likes, "comments": comments_count, "shares": shares},
            language=_detect_lang(content),
            metadata_={
                "likes":     likes,
                "comments":  comments_count,
                "shares":    shares,
                "thumbnail": thumbnail,
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


async def scrape_facebook_posts_via_provider(
    db: AsyncSession,
    identifier: str,
    max_posts: int = 3,
    max_comments: int = 10,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Scrape Facebook via provider abstraction (Apify, siap auto-switch —
    lihat app/services/facebook/providers/). Dipakai pipeline
    trend_recommendations (run_daily_trend_scrape_facebook) — BUKAN
    scrape_facebook_posts() di atas yang pakai token Meta resmi.

    Dedup: akun yang sudah discrape hari ini di-skip (tidak panggil provider
    lagi), pola sama dengan Instagram (app/services/instagram/pipeline_service.py).
    """
    from app.services.facebook.providers.registry import search_profile_with_fallback

    max_posts = min(max_posts, MAX_POSTS)
    max_comments = min(max_comments, MAX_COMMENTS)

    # ── Skip kalau akun ini sudah discrape hari ini ────────────────────────────
    today_count = await db.scalar(
        select(func.count()).select_from(Post).where(
            Post.platform == "facebook",
            Post.author == identifier,
            func.date(Post.collected_at) == datetime.now(timezone.utc).date(),
        )
    )
    if today_count:
        return {
            "identifier": identifier,
            "posts_scraped": today_count,
            "posts_saved": 0,
            "errors": [],
            "provider_used": "cached_today",
            "already_scraped_today": True,
        }

    errors: list[str] = []
    provider_used: str | None = None
    try:
        rows, provider_used = await search_profile_with_fallback(identifier, max_posts, max_comments)
    except Exception as exc:
        errors.append(tag_if_quota_error(f"provider: {exc}", exc=exc))
        rows = []

    if not rows:
        return {
            "identifier": identifier, "posts_scraped": 0, "posts_saved": 0,
            "errors": errors, "provider_used": provider_used,
        }

    # ── Group baris per post (by postUrl) ─────────────────────────────────────
    posts_by_url: dict[str, dict[str, Any]] = {}
    profile_followers = 0
    for row in rows:
        post_url = row.get("postUrl", "")
        if not post_url:
            continue
        bucket = posts_by_url.setdefault(post_url, {"meta": row, "comments": []})
        if row.get("commentText"):
            bucket["comments"].append(row)
        if row.get("profileFollowers"):
            profile_followers = row.get("profileFollowers")

    posts_saved_count = 0
    for post_url, bucket in posts_by_url.items():
        meta_row = bucket["meta"]
        # Facebook tidak punya "shortcode" seperti Instagram — URL post
        # formatnya variatif (reel/permalink/video/story), jadi external_id
        # diturunkan dari hash URL-nya sendiri (stabil per post, cukup unik).
        ext_id = hashlib.sha1(post_url.encode("utf-8")).hexdigest()[:24]

        post_obj = await db.scalar(
            select(Post).where(Post.platform == "facebook", Post.external_id == ext_id)
        )
        is_new = post_obj is None

        if is_new:
            caption = meta_row.get("postDescription", "") or ""
            published_at = None
            if meta_row.get("postTimestamp"):
                try:
                    published_at = datetime.fromisoformat(meta_row["postTimestamp"].replace("Z", "+00:00"))
                except ValueError:
                    published_at = None

            likes = meta_row.get("postLikesCount", 0)
            comments = meta_row.get("postCommentsCount", 0)

            post_obj = Post(
                id=uuid.uuid4(),
                keyword_id=keyword_id,
                external_id=ext_id,
                platform="facebook",
                content=caption,
                author=identifier,
                url=post_url,
                published_at=published_at,
                collected_at=datetime.now(timezone.utc),
                tags=_extract_hashtags(caption),
                media=[],  # Facebook (jalur ini): thumbnail belum diekstrak dari raw response
                metrics={"views": 0, "likes": likes, "comments": comments, "shares": 0},
                language=_detect_lang(caption),
                metadata_={
                    "likes":     likes,
                    "comments":  comments,
                    "followers": profile_followers,
                    "source":    provider_used,
                },
            )
            db.add(post_obj)
            await db.flush()
            posts_saved_count += 1

            for tag in _extract_hashtags(caption):
                db.add(Entity(post_id=post_obj.id, text=tag, entity_type="HASHTAG"))

            from app.workers.ai_worker import analyze_post_task
            analyze_post_task.delay(str(post_obj.id), run_sentiment=True, run_ner=False, run_embedding=False)

        # ── Komentar baru ──────────────────────────────────────────────────────
        if bucket["comments"] and post_obj.id:
            existing_cmt_ids: set[str] = set(
                (await db.scalars(select(Comment.external_id).where(Comment.post_id == post_obj.id))).all()
            )
            new_comments: list[Comment] = []
            for cmt in bucket["comments"][:max_comments]:
                text_ = cmt.get("commentText", "")
                author = cmt.get("commentAuthor", "")
                timestamp = cmt.get("commentTimestamp", "")
                ext_cmt_id = hashlib.sha1(f"{ext_id}|{author}|{text_}|{timestamp}".encode("utf-8")).hexdigest()
                if ext_cmt_id in existing_cmt_ids:
                    continue

                comment = Comment(
                    post_id=post_obj.id,
                    external_id=ext_cmt_id,
                    content=text_,
                    author=author,
                    published_at=None,
                    metadata_={"like_count": cmt.get("commentLikesCount", 0)},
                )
                db.add(comment)
                new_comments.append(comment)
                existing_cmt_ids.add(ext_cmt_id)

            if new_comments:
                await db.flush()
                await _analyze_lexicon(db, new_comments, keyword_id)

    await db.commit()

    return {
        "identifier":     identifier,
        "posts_scraped":  len(posts_by_url),
        "posts_saved":    posts_saved_count,
        "errors":         errors,
        "provider_used":  provider_used,
    }
