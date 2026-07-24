"""Helper BERSAMA dipanggil oleh struktur_data.py SEMUA platform (2026-07-24)
-- analisis lexicon langsung SAAT komentar disimpan (bukan batch job
terpisah), pola SAMA dgn kode lama (`main` branch, tiap pipeline_service.py
platform manggil LexiconAnalysis inline). SATU fungsi di sini dipakai
lintas 6 platform (bukan duplikat kode analisis di tiap struktur_data.py)
krn analisis lexicon genuinely generik -- tidak ada logika khusus platform.

`keyword_id` SENGAJA selalu None -- tabel `keywords` (skema lama) sudah
digantikan konsep `source_topic`/`trend_recommendations` di arsitektur v2
saat ini, TIDAK ada pemetaan 1:1 yg valid ke situ. Kolom itu tetap ada
di skema (nullable) utk kompatibilitas mundur, bukan dipaksa diisi."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.youtube_analysis.models import LexiconAnalysis
from app.services.sentiment import lexicon


async def analyze_and_queue_lexicon(db: AsyncSession, comment_id: uuid.UUID, content: str) -> LexiconAnalysis:
    """HANYA `db.add()` -- TIDAK commit sendiri, pemanggil (struktur_data.py)
    yg commit bareng post+comment dlm satu transaksi, konsisten dgn pola
    yg sudah ada di semua platform saat ini."""
    result = lexicon.analyze(content)
    row = LexiconAnalysis(
        comment_id=comment_id,
        keyword_id=None,
        matched_positive=result.matched_positive,
        matched_negative=result.matched_negative,
        removed_stopwords=result.removed_stopwords,
        score=result.score,
        label=result.label,
    )
    db.add(row)
    return row
