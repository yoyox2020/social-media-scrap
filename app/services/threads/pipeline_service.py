"""
Threads Pipeline Service — Fase 1 (scrape dasar via EnsembleData).

Mirroring app/services/tiktok/pipeline_service.py, TAPI Threads search-nya
berbasis KEYWORD/TOPIK TEKS langsung (bukan per-akun seperti TikTok/
Facebook) -- lebih dekat pola News (app/services/news/trend_scrape_service.py)
drpd TikTok. Tidak butuh `related_accounts` di trend_recommendations.

PENTING (lihat catatan lengkap di app/integrations/threads/connector.py):
- Endpoint search TIDAK terbukti mendukung pagination (cursor selalu
  kosong) -- 1x panggilan = 1 batch tetap dari EnsembleData.
- Endpoint balasan TERBUKTI cuma balikin SEBAGIAN balasan pada post yang
  repliesnya banyak (keterbatasan API pihak ketiga, bukan bug kita).
- EnsembleData BERBAYAR & kuota harian kecil (dites live 2026-07-19, abis
  cuma dari ~10 panggilan uji) -- komentar/replies HANYA diambil utk N post
  teratas per topik (comments_top_n), BUKAN semua post, demi kendali biaya
  (pola sama dgn smart_search_youtube: comment cuma utk video terbaru).
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
from app.shared.ensembledata_errors import tag_if_quota_error as _tag_if_ensembledata_quota_error
from app.services.processing.normalizer import ThreadsNormalizer

MAX_POSTS = 20
MAX_COMMENTS_TOP_N = 10  # maks post per topik yg diambil balasannya (kendali biaya)


async def _analyze_lexicon(db: AsyncSession, comments: list[Comment], keyword_id: uuid.UUID | None) -> None:
    """Sentimen lexicon utk komentar -- pola SAMA dgn semua platform lain
    (Facebook/Instagram/TikTok/Twitter/YouTube), leksikon dipakai LINTAS
    PLATFORM (app/ai/lexicon/service.py)."""
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


def _reply_to_comment(raw_reply: dict[str, Any], post_id: uuid.UUID) -> Comment | None:
    """Konversi 1 balasan mentah (dari ThreadsConnector.extract_replies())
    jadi Comment -- struktur post reply SAMA dgn post biasa (lihat catatan
    di ThreadsNormalizer), tapi field yg dipakai lebih sedikit."""
    pk = raw_reply.get("pk")
    if not pk:
        return None

    caption = raw_reply.get("caption") or {}
    content = caption.get("text") if isinstance(caption, dict) else None
    if not content:
        app_info = raw_reply.get("text_post_app_info") or {}
        fragments = (app_info.get("text_fragments") or {}).get("fragments") or []
        if isinstance(fragments, list):
            content = "".join(f.get("plaintext") or "" for f in fragments if isinstance(f, dict))
    content = content or ""

    user = raw_reply.get("user") or {}
    app_info = raw_reply.get("text_post_app_info") or {}
    reply_to = (app_info.get("reply_to_author") or {}).get("username")

    from app.services.processing.normalizer import _utc_from_timestamp

    return Comment(
        post_id=post_id,
        external_id=str(pk),
        content=content,
        author=user.get("username") or "",
        published_at=_utc_from_timestamp(raw_reply.get("taken_at")),
        metadata_={
            "like_count": raw_reply.get("like_count", 0),
            "reply_to": reply_to,
        },
    )


async def collect_replies_for_post(
    db: AsyncSession,
    post: Post,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Ambil balasan utk 1 post Threads, simpan sbg Comment + analisis
    lexicon. Return ringkasan (termasuk `total_replies_hint` dari post
    aslinya utk transparansi soal cakupan -- lihat catatan keterbatasan
    di modul connector)."""
    from app.integrations.ensemble_data.client import EnsembleDataClient
    from app.integrations.threads.connector import ThreadsConnector

    existing_ids = set((await db.scalars(
        select(Comment.external_id).where(Comment.post_id == post.id)
    )).all())

    fetched = 0
    new_comments: list[Comment] = []
    errors: list[str] = []
    try:
        async with EnsembleDataClient() as client:
            connector = ThreadsConnector(client)
            raw = await connector.get_post_replies(post.external_id)
            raw_replies = connector.extract_replies(raw, root_post_pk=post.external_id)
    except Exception as exc:
        errors.append(_tag_if_ensembledata_quota_error(str(exc)[:300]))
        raw_replies = []

    for raw_reply in raw_replies:
        fetched += 1
        comment = _reply_to_comment(raw_reply, post.id)
        if not comment or comment.external_id in existing_ids:
            continue
        db.add(comment)
        new_comments.append(comment)
        existing_ids.add(comment.external_id)

    if new_comments:
        await db.flush()
        await _analyze_lexicon(db, new_comments, keyword_id)
        await db.commit()

    return {
        "post_id": str(post.id),
        "replies_fetched": fetched,
        "replies_new": len(new_comments),
        "total_replies_hint": (post.metadata_ or {}).get("replies", 0),
        "errors": errors,
    }


async def search_threads_posts(
    db: AsyncSession,
    keyword: str,
    max_posts: int = 10,
    comments_top_n: int = 3,
    keyword_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Cari post Threads berdasarkan keyword via EnsembleData, simpan post
    baru, lalu ambil balasan utk `comments_top_n` post PALING BANYAK
    interaksi (like+reply) -- bukan semua post, demi kendali biaya (lihat
    catatan modul).
    """
    from app.integrations.ensemble_data.client import EnsembleDataClient
    from app.integrations.threads.connector import ThreadsConnector

    max_posts = min(max_posts, MAX_POSTS)
    comments_top_n = min(comments_top_n, MAX_COMMENTS_TOP_N)

    errors: list[str] = []
    raw_posts: list[dict[str, Any]] = []
    try:
        async with EnsembleDataClient() as client:
            connector = ThreadsConnector(client)
            raw = await connector.search_by_keyword(keyword)
            raw_posts = connector.extract_posts(raw)[:max_posts]
    except Exception as exc:
        errors.append(_tag_if_ensembledata_quota_error(str(exc)[:300]))

    normalizer = ThreadsNormalizer()
    posts_found = len(raw_posts)
    posts_saved = 0
    saved_posts: list[Post] = []

    for raw_item in raw_posts:
        pk = raw_item.get("pk")
        if not pk:
            continue
        existing = await db.scalar(
            select(Post).where(Post.platform == "threads", Post.external_id == str(pk))
        )
        if existing is not None:
            saved_posts.append(existing)  # tetap ikut kandidat comments_top_n (post lama boleh direfresh replies-nya)
            continue

        post_obj = normalizer.normalize([raw_item], keyword_id)[0]
        db.add(post_obj)
        await db.flush()
        posts_saved += 1
        saved_posts.append(post_obj)

    await db.commit()

    # Comments_top_n post dgn interaksi (likes+replies) tertinggi -- pola
    # sama spt smart_search_youtube (comment cuma utk yg paling relevan).
    ranked = sorted(
        saved_posts,
        key=lambda p: (p.metadata_ or {}).get("likes", 0) + (p.metadata_ or {}).get("replies", 0),
        reverse=True,
    )
    reply_results = []
    for post in ranked[:comments_top_n]:
        try:
            r = await collect_replies_for_post(db, post, keyword_id)
            reply_results.append(r)
        except Exception as exc:
            errors.append(_tag_if_ensembledata_quota_error(f"replies post={post.external_id}: {exc}"))

    return {
        "keyword": keyword,
        "posts_found": posts_found,
        "posts_saved": posts_saved,
        "replies_collected": reply_results,
        "errors": errors,
    }
