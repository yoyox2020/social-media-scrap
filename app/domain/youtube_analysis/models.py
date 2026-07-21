"""
LexiconAnalysis — hasil analisis sentimen berbasis leksikon per komentar YouTube.

Tabel ini menyimpan detail per komentar:
  - keyword: kata dari leksikon yang cocok (positif / negatif)
  - stopword: kata yang dihapus sebelum analisis
  - score: skor akhir (positif - negatif)
  - label: 'positif', 'negatif', 'netral'
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class LexiconAnalysis(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "lexicon_analyses"

    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("keywords.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Kata kunci leksikon yang ditemukan
    matched_positive: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    matched_negative: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Stopword yang dihapus saat preprocessing
    removed_stopwords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Skor akhir dan label
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    label: Mapped[str] = mapped_column(String(20), nullable=False, default="netral")

    # Sentiment Agent (2026-07-18) -- opini KEDUA dari LLM, TERPISAH dari
    # label/score lexicon di atas (TIDAK menimpa) -- lihat
    # app/services/sentiment_agent/agent.py utk kriteria kapan dipakai
    # (komentar BUKAN bahasa Indonesia, atau lexicon bilang "netral").
    detected_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # None = belum direview LLM. True/False = direview, sepakat/tidak sepakat dgn label lexicon.
    sentiment_agreement: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Tie-breaker (2026-07-18) -- dipanggil HANYA saat lexicon vs llm_label
    # TIDAK sepakat. LLM KEDUA (model/provider beda dari llm_label) jadi
    # suara ketiga, final_label = mayoritas 2-dari-3 (None kalau 3 beda
    # semua -- genuinely ambigu, tidak ada mayoritas).
    llm2_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm2_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    final_label: Mapped[str | None] = mapped_column(String(20), nullable=True)

    comment: Mapped["Comment"] = relationship("Comment", lazy="noload")  # noqa: F821
    keyword: Mapped["Keyword"] = relationship("Keyword", lazy="noload")  # noqa: F821


class LexiconWordSuggestion(Base, UUIDMixin, TimestampMixin):
    """
    Usulan kata BARU utk kamus lexicon (app/ai/lexicon/data/*.txt), digali
    dari kasus dimana mayoritas (lexicon vs LLM1 vs LLM2 tie-breaker)
    MENGALAHKAN lexicon -- kata dari komentar itu yg BELUM ada di kamus
    dicatat di sini. BUKAN auto-ditambahkan ke kamus (lexicon dipakai
    LINTAS PLATFORM FB/IG/TikTok/Twitter/YouTube, bukan cuma YouTube) --
    cuma usulan berbasis bukti (evidence_count), tinjau manual dulu.
    """
    __tablename__ = "lexicon_word_suggestions"
    __table_args__ = (UniqueConstraint("word", "suggested_polarity", name="uq_lexicon_word_suggestions_word_polarity"),)

    word: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    suggested_polarity: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    example_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
