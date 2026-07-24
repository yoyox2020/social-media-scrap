"""REST API Sentiment Agent (2026-07-24) -- baca metadata yg SUDAH
tersimpan (posts/comments, hasil Metadata Agent tiap platform), TIDAK
melakukan crawling/scraping sendiri. Endpoint baca (list/detail/
statistics) PUBLIK tanpa login (pola SAMA dgn *_metadata router lain --
facebook_metadata.py dkk), endpoint TULIS (trigger analisis LLM,
override manual) admin-only krn manggil LLM = biaya/kuota nyata."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.users.models import User
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.infrastructure.database.connection import get_db
from app.repositories.sentiment_repository import SentimentRepository
from app.services.auth.dependencies import require_admin
from app.services.sentiment import config as sentiment_cfg
from app.services.sentiment.agent import run_sentiment_agent
from app.services.sentiment.backfill import backfill_lexicon_analysis
from app.services.sentiment.lexicon import analyze as lexicon_analyze
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/sentiment", tags=["sentiment"])


def _row_to_dict(la: LexiconAnalysis, c: Comment, p) -> dict:
    return {
        "comment_id": str(la.comment_id),
        "post_id": str(c.post_id),
        "platform": p.platform,
        "post_external_id": p.external_id,
        "comment_author": c.author,
        "comment_content": c.content,
        "sentiment": la.final_label or la.label,
        "lexicon_label": la.label,
        "lexicon_score": la.score,
        "matched_positive": la.matched_positive,
        "matched_negative": la.matched_negative,
        "detected_language": la.detected_language,
        "llm_label": la.llm_label,
        "llm_model": la.llm_model,
        "llm2_label": la.llm2_label,
        "llm2_model": la.llm2_model,
        "final_label": la.final_label,
        "sentiment_agreement": la.sentiment_agreement,
        "analyzed_at": la.created_at.isoformat() if la.created_at else None,
        "llm_checked_at": la.llm_checked_at.isoformat() if la.llm_checked_at else None,
    }


@router.get("", response_model=dict)
async def list_sentiment(
    platform: str | None = Query(default=None, description="Filter platform (youtube/tiktok/facebook/instagram/threads/twitter)"),
    sentiment: str | None = Query(default=None, description="Filter label efektif: positif/negatif/netral"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    post_id: uuid.UUID | None = Query(default=None, description="Filter SATU post spesifik by UUID internal (post_id di response)"),
    post_external_id: str | None = Query(default=None, description="Filter SATU post spesifik by ID asli platform (mis. video ID YouTube, lebih praktis drpd UUID)"),
    db: AsyncSession = Depends(get_db),
):
    """Daftar hasil sentiment, siap dipakai dashboard/analytics langsung.
    Pakai `post_external_id` (mis. ID video YouTube) utk lihat SEMUA
    komentar 1 post spesifik -- bukan cuma sample tercampur lintas post."""
    repo = SentimentRepository(db)
    rows, total = await repo.list_results(
        platform, sentiment, date_from, date_to, page, limit,
        post_id=post_id, post_external_id=post_external_id,
    )
    return build_success_response({
        "items": [_row_to_dict(la, c, p) for la, c, p in rows],
        "page": page, "limit": limit, "total": total,
        "total_pages": (total + limit - 1) // limit if limit else 0,
    })


@router.get("/statistics", response_model=dict)
async def get_statistics(
    platform: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Ringkasan jumlah per label (positif/negatif/netral)."""
    repo = SentimentRepository(db)
    stats = await repo.get_statistics(platform, date_from, date_to)
    unanalyzed = await repo.get_unanalyzed_count()
    return build_success_response({**stats, "belum_direview_llm": unanalyzed})


@router.get("/{comment_id}", response_model=dict)
async def get_sentiment_detail(comment_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Hasil sentiment SATU komentar."""
    repo = SentimentRepository(db)
    row = await repo.get_by_comment_id(comment_id)
    if not row:
        raise NotFoundError(f"Belum ada hasil sentiment utk comment_id {comment_id} (lexicon belum jalan/comment tidak ada)")
    return build_success_response(_row_to_dict(*row))


@router.post("/{comment_id}", response_model=dict)
async def analyze_one(
    comment_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan ULANG lexicon (instan, gratis) utk SATU komentar --
    utk override manual/testing, BUKAN jalur utama (lexicon otomatis
    jalan inline saat komentar pertama kali disimpan Metadata Agent)."""
    comment = await db.get(Comment, comment_id)
    if not comment:
        raise NotFoundError(f"Comment {comment_id} tidak ditemukan")

    result = lexicon_analyze(comment.content or "")
    existing = await db.scalar(select(LexiconAnalysis).where(LexiconAnalysis.comment_id == comment_id))
    if existing:
        existing.matched_positive = result.matched_positive
        existing.matched_negative = result.matched_negative
        existing.removed_stopwords = result.removed_stopwords
        existing.score = result.score
        existing.label = result.label
    else:
        db.add(LexiconAnalysis(
            comment_id=comment_id, keyword_id=None,
            matched_positive=result.matched_positive, matched_negative=result.matched_negative,
            removed_stopwords=result.removed_stopwords, score=result.score, label=result.label,
        ))
    await db.commit()

    return build_success_response({
        "comment_id": str(comment_id), "sentiment": result.label, "score": result.score,
    })


@router.post("/analyze/batch", response_model=dict)
async def analyze_batch(
    limit: int | None = Query(default=None, description="Override batch_size default sekali jalan"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan Sentiment Agent (LLM tiebreaker) SEKARANG thd backlog
    komentar yg lexicon-nya belum direview LLM -- SAMA persis dgn yg
    jalan otomatis via Celery beat, cuma dipicu manual/segera."""
    if limit is not None:
        await sentiment_cfg.set_batch_size(limit if limit in sentiment_cfg.ALLOWED_BATCH_SIZE else sentiment_cfg.DEFAULT_BATCH_SIZE)
    result = await run_sentiment_agent(db)
    return build_success_response(result)


@router.post("/backfill/lexicon", response_model=dict)
async def backfill_lexicon(
    limit: int = Query(default=5000, ge=1, le=100000, description="Jumlah komentar lama diproses sekali panggil (lexicon lokal, gratis, aman batch besar)"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Analisis lexicon utk komentar LAMA yg belum pernah tersentuh
    (sebelum wiring inline dibangun) -- panggil BERULANG (has_more=true)
    sampai backlog habis, TIDAK ada batas biaya krn lexicon lokal."""
    result = await backfill_lexicon_analysis(db, limit=limit)
    return build_success_response(result)


@router.get("/config/current", response_model=dict)
async def get_config(_admin: User = Depends(require_admin)):
    return build_success_response({
        "model": await sentiment_cfg.get_model(),
        "tiebreaker_model": await sentiment_cfg.get_tiebreaker_model(),
        "batch_size": await sentiment_cfg.get_batch_size(),
        "is_running": await sentiment_cfg.is_running(),
    })


@router.patch("/config/current", response_model=dict)
async def update_config(
    model: str | None = Query(default=None),
    tiebreaker_model: str | None = Query(default=None),
    batch_size: int | None = Query(default=None),
    _admin: User = Depends(require_admin),
):
    if model:
        await sentiment_cfg.set_model(model)
    if tiebreaker_model:
        await sentiment_cfg.set_tiebreaker_model(tiebreaker_model)
    if batch_size:
        await sentiment_cfg.set_batch_size(batch_size)
    return build_success_response({
        "model": await sentiment_cfg.get_model(),
        "tiebreaker_model": await sentiment_cfg.get_tiebreaker_model(),
        "batch_size": await sentiment_cfg.get_batch_size(),
    })
