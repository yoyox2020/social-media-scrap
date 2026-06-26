"""
LexiconAnalysis — hasil analisis sentimen berbasis leksikon per komentar YouTube.

Tabel ini menyimpan detail per komentar:
  - keyword: kata dari leksikon yang cocok (positif / negatif)
  - stopword: kata yang dihapus sebelum analisis
  - score: skor akhir (positif - negatif)
  - label: 'positif', 'negatif', 'netral'
"""
import uuid

from sqlalchemy import Float, ForeignKey, String
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
    keyword_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("keywords.id", ondelete="CASCADE"),
        nullable=False,
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

    comment: Mapped["Comment"] = relationship("Comment", lazy="noload")  # noqa: F821
    keyword: Mapped["Keyword"] = relationship("Keyword", lazy="noload")  # noqa: F821
