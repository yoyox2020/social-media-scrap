"""Backfill lexicon utk komentar LAMA (2026-07-24) -- gap nyata ditemukan
saat user tanya "apakah sudah bisa mengikuti analisis dari data kita
semua": wiring inline (save.py) cuma jalan utk komentar BARU sejak hari
ini, komentar yg SUDAH ADA di DB sebelumnya (terutama YouTube 452rb+ dari
825rb total, TikTok 3.9rb+ dari 4.1rb) TIDAK PERNAH tersentuh lexicon
sama sekali -- tidak ada mekanisme retroaktif sebelum file ini dibuat.

Lexicon (rule-based, LOKAL, TANPA panggil API luar) -- BEDA dari
backfill komentar platform lain (Instagram dkk) yg genuinely perlu
panggil Apify/dst, backfill INI murni CPU, gratis, instan. Batch besar
aman dipakai (tidak ada kuota/biaya yg perlu dijaga), commit tiap
BATCH_COMMIT_SIZE baris spy 1 transaksi tidak membengkak kalau backlog
besar (ratusan ribu baris)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.services.sentiment.lexicon import analyze as lexicon_analyze

BATCH_COMMIT_SIZE = 500


def _not_analyzed_exists_clause():
    """NOT EXISTS terkorelasi (BUKAN `NOT IN (subquery)` -- ditemukan
    NYATA 2026-07-24 lewat live-test: `NOT IN` thd subquery gede (385rb+
    baris lexicon_analyses vs 825rb+ comments) bikin query MACET >1 jam
    (query plan Postgres jelek utk NOT IN skala besar, beda dgn NOT
    EXISTS yg bisa dioptimasi jadi anti-join pakai index)."""
    return ~select(LexiconAnalysis.id).where(LexiconAnalysis.comment_id == Comment.id).exists()


async def backfill_lexicon_analysis(db: AsyncSession, limit: int = 5000) -> dict:
    """Cari komentar yg BELUM py baris `lexicon_analyses`, analisis
    lexicon lokal, simpan. Aman dipanggil berulang -- otomatis skip yg
    sudah pernah diproses run sebelumnya (idempotent)."""
    stmt = (
        select(Comment)
        .where(_not_analyzed_exists_clause())
        .order_by(Comment.created_at.asc())
        .limit(limit)
    )
    comments = (await db.scalars(stmt)).all()
    if not comments:
        return {"checked": 0, "analyzed": 0, "has_more": False}

    analyzed = 0
    for i, c in enumerate(comments, start=1):
        result = lexicon_analyze(c.content or "")
        db.add(LexiconAnalysis(
            comment_id=c.id, keyword_id=None,
            matched_positive=result.matched_positive, matched_negative=result.matched_negative,
            removed_stopwords=result.removed_stopwords, score=result.score, label=result.label,
        ))
        analyzed += 1
        if i % BATCH_COMMIT_SIZE == 0:
            await db.commit()
    await db.commit()

    remaining = await db.scalar(select(Comment.id).where(_not_analyzed_exists_clause()).limit(1))
    return {
        "checked": len(comments), "analyzed": analyzed,
        "has_more": remaining is not None,
    }
