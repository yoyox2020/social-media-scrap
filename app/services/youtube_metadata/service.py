"""Data lengkap post YouTube utk dipakai FRONTEND (2026-07-22) --
reuse tabel `posts`/`comments` yg sudah diisi pipeline multi-agent
(lihat app/agents/pipeline.py), TIDAK bikin tabel baru. Publik (tanpa
login) sama spt /youtube/trending-public -- data ini memang utk
ditampilkan, bukan dikelola."""
from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import Float, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post

SortKey = Literal["trend_score", "engagement_score", "freshness_score", "authority_score", "views", "published_at"]


def _sort_column(sort_by: str):
    if sort_by == "views":
        return cast(Post.metrics["views"].astext, Float)
    if sort_by == "published_at":
        return Post.published_at
    if sort_by in ("trend_score", "engagement_score", "freshness_score", "authority_score"):
        return cast(Post.metadata_[sort_by].astext, Float)
    return cast(Post.metadata_["trend_score"].astext, Float)


def _post_to_dict(p: Post, comment_count: int) -> dict:
    meta = p.metadata_ or {}
    metrics = p.metrics or {}
    thumb = None
    if p.media:
        for m in p.media:
            if m.get("url"):
                thumb = m["url"]
                break
    return {
        "id": p.external_id,
        "title": p.title,
        "content": p.content,
        "author": p.author,
        "channel_id": meta.get("channel_id"),
        "audience_size": meta.get("audience_size") or meta.get("channel_subscriber_count"),
        "url": p.url,
        "thumbnail": thumb,
        "metrics": {
            "views": metrics.get("views", 0),
            "likes": metrics.get("likes", 0),
            "comments": metrics.get("comments", 0),
            "shares": metrics.get("shares", 0),
        },
        "scores": {
            "trend_score": meta.get("trend_score"),
            "engagement_score": meta.get("engagement_score"),
            "freshness_score": meta.get("freshness_score"),
            "authority_score": meta.get("authority_score"),
        },
        "ai_summary": meta.get("ai_summary"),
        "ai_tags": meta.get("ai_tags") or [],
        "source_topic": meta.get("source_topic"),
        "source_topics": meta.get("source_topics") or ([meta["source_topic"]] if meta.get("source_topic") else []),
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "collected_at": p.collected_at.isoformat() if p.collected_at else None,
        "saved_comment_count": comment_count,
    }


async def list_posts(
    db: AsyncSession, topic: str | None = None, search: str | None = None,
    sort_by: str = "trend_score", order: str = "desc", page: int = 1, page_size: int = 20,
) -> dict:
    base_filters = [Post.platform == "youtube"]
    if topic:
        base_filters.append(
            or_(
                Post.metadata_["source_topic"].astext == topic,
                cast(Post.metadata_["source_topics"], String).ilike(f'%"{topic}"%'),
            )
        )
    if search:
        like = f"%{search}%"
        base_filters.append(or_(Post.title.ilike(like), Post.author.ilike(like)))

    count_stmt = select(func.count()).select_from(Post).where(*base_filters)
    total = await db.scalar(count_stmt)

    sort_col = _sort_column(sort_by)
    sort_col = sort_col.desc() if order != "asc" else sort_col.asc()

    offset = (max(page, 1) - 1) * page_size
    stmt = (
        select(Post)
        .where(*base_filters)
        .order_by(sort_col.nullslast() if hasattr(sort_col, "nullslast") else sort_col)
        .offset(offset)
        .limit(page_size)
    )
    posts = (await db.scalars(stmt)).all()

    comment_counts: dict[uuid.UUID, int] = {}
    if posts:
        post_ids = [p.id for p in posts]
        rows = (await db.execute(
            select(Comment.post_id, func.count()).where(Comment.post_id.in_(post_ids)).group_by(Comment.post_id)
        )).all()
        comment_counts = {row[0]: row[1] for row in rows}

    items = [_post_to_dict(p, comment_counts.get(p.id, 0)) for p in posts]

    stats_row = (await db.execute(
        select(
            func.count().label("total"),
            func.count(func.distinct(func.coalesce(Post.metadata_["source_topic"].astext, ""))).label("topics"),
            func.sum(cast(Post.metrics["views"].astext, Float)).label("total_views"),
            func.avg(cast(Post.metadata_["trend_score"].astext, Float)).label("avg_trend"),
        ).where(*base_filters)
    )).one()

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total or 0,
        "total_pages": ((total or 0) + page_size - 1) // page_size if page_size else 0,
        "stats": {
            "total_videos": stats_row.total or 0,
            "distinct_topics": stats_row.topics or 0,
            "total_views": int(stats_row.total_views or 0),
            "avg_trend_score": round(float(stats_row.avg_trend), 2) if stats_row.avg_trend is not None else None,
        },
    }


async def get_post_detail(db: AsyncSession, video_id: str) -> dict | None:
    post = await db.scalar(select(Post).where(Post.platform == "youtube", Post.external_id == video_id))
    if not post:
        return None
    comments = (await db.scalars(
        select(Comment).where(Comment.post_id == post.id).order_by(Comment.published_at.desc().nullslast())
    )).all()
    data = _post_to_dict(post, len(comments))
    data["comments"] = [
        {
            "author": c.author,
            "content": c.content,
            "likes": (c.metadata_ or {}).get("like_count", 0),
            "published_at": c.published_at.isoformat() if c.published_at else None,
        }
        for c in comments
    ]
    return data


async def list_topics(db: AsyncSession) -> list[dict]:
    """Daftar topik yg pernah dicari + jumlah videonya -- utk dropdown
    filter di frontend. Diagregasi di Python (bukan GROUP BY SQL atas
    ekspresi JSON) -- Postgres menolak GROUP BY dgn 2 bind-parameter
    terpisah yg kebetulan sama nilainya (SQLAlchemy tidak menyatukan
    parameter SELECT vs GROUP BY scr otomatis). Jumlah post masih
    kecil (ribuan), jadi agregasi Python aman performa."""
    from collections import Counter

    rows = (await db.scalars(select(Post.metadata_).where(Post.platform == "youtube"))).all()
    counter: Counter = Counter()
    for meta in rows:
        topic = (meta or {}).get("source_topic")
        if topic:
            counter[topic] += 1
    return [{"topic": t, "count": c} for t, c in counter.most_common()]
