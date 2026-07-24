"""Data lengkap post Facebook utk dipakai FRONTEND (2026-07-24) -- pola
SAMA PERSIS dgn app/services/youtube_metadata/service.py &
tiktok_metadata/service.py, reuse tabel `posts`/`comments` yg sama
(platform="facebook"), TIDAK bikin tabel baru. Publik (tanpa login).

Beda dari YouTube/TikTok (dicek LANGSUNG ke 50 post Facebook yg SUDAH
ADA di DB sebelum file ini ditulis, BUKAN asumsi):
- `title` SELALU kosong (Facebook tidak py konsep judul terpisah dari
  isi post) -- `content` dipakai sbg teks utama, TIDAK ada fallback ke
  title spt platform lain.
- `views`/`shares` SELALU 0 (Facebook/Apify tidak expose data ini) --
  tetap ditampilkan sbg 0 (bukan dihilangkan) spy struktur response
  konsisten dgn platform lain di frontend.
- `author_followers` dari `metadata_.followers` (BUKAN metrics -- beda
  lokasi dari TikTok yg pakai `author_fans`, sudah diverifikasi lewat
  query ke 50 baris asli: 50/50 py `metadata.followers`, 0 py itu di
  `metrics`).
- `scores` (trend_score dkk) SELALU null -- belum ada agent
  struktur-data Facebook di branch ini yg menghitungnya (0/50 post py
  field itu). BUKAN bug, field tetap ada di response (null) spy
  frontend tidak perlu cek platform utk tau struktur field.
- `source_topic`/topics SELALU kosong utk 50 post yg ada sekarang (data
  lama, dikumpulkan sebelum pipeline topic->search->coordinator
  dibangun) -- endpoint /topics tetap dibangun (siap dipakai begitu ada
  post baru lewat pipeline topic asli), bukan dihapus."""
from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import Float, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post

SortKey = Literal["trend_score", "engagement_score", "freshness_score", "authority_score", "likes", "published_at"]


def _sort_column(sort_by: str):
    if sort_by == "likes":
        return cast(Post.metrics["likes"].astext, Float)
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
        "author_followers": meta.get("followers"),
        "audience_size": meta.get("audience_size") or meta.get("followers"),
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
    sort_by: str = "published_at", order: str = "desc", page: int = 1, page_size: int = 20,
) -> dict:
    base_filters = [Post.platform == "facebook"]
    if topic:
        base_filters.append(
            or_(
                Post.metadata_["source_topic"].astext == topic,
                cast(Post.metadata_["source_topics"], String).ilike(f'%"{topic}"%'),
            )
        )
    if search:
        like = f"%{search}%"
        base_filters.append(or_(Post.content.ilike(like), Post.author.ilike(like)))

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
            func.sum(cast(Post.metrics["likes"].astext, Float)).label("total_likes"),
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
            "total_posts": stats_row.total or 0,
            "distinct_topics": stats_row.topics or 0,
            "total_likes": int(stats_row.total_likes or 0),
            "avg_trend_score": round(float(stats_row.avg_trend), 2) if stats_row.avg_trend is not None else None,
        },
    }


async def get_post_detail(db: AsyncSession, post_id: str) -> dict | None:
    post = await db.scalar(select(Post).where(Post.platform == "facebook", Post.external_id == post_id))
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
    """Sama pola dgn youtube_metadata/tiktok_metadata: agregasi di
    Python (Postgres menolak GROUP BY atas ekspresi JSON yg SQLAlchemy
    bind terpisah). Kosong utk sekarang (0/50 post py source_topic),
    tetap dibangun spy siap dipakai begitu ada post baru dari pipeline
    topic asli."""
    from collections import Counter

    rows = (await db.scalars(select(Post.metadata_).where(Post.platform == "facebook"))).all()
    counter: Counter = Counter()
    for meta in rows:
        topic = (meta or {}).get("source_topic")
        if topic:
            counter[topic] += 1
    return [{"topic": t, "count": c} for t, c in counter.most_common()]
