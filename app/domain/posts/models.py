import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin
from app.shared.constants import EMBEDDING_DIMENSION


class Post(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "posts"

    keyword_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=True)
    # tags: list[str] hasil ekstrak hashtag dari content/caption (lihat
    # _extract_hashtags() di normalizer.py) -- BUKAN dari API platform
    # (kebanyakan platform tidak expose tags terstruktur di endpoint yg
    # kita pakai), jadi cakupannya terbatas ke hashtag literal di teks.
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # media: list[{"type": "image"|"video", "url": str}] -- MVP cuma
    # thumbnail (1 gambar), belum tangkap multi-gambar/carousel.
    media: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # metrics: {"views","likes","comments","shares"} terstruktur, nilai
    # yg platform-nya tidak punya konsep itu (mis. FB/IG views) tetap 0,
    # BUKAN None -- konsisten dgn metadata_ lama supaya dashboard yg
    # sudah baca metadata_ tidak perlu berubah interpretasi.
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Processing columns — diisi oleh ProcessingService (Phase 3)
    cleaned_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_processed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    is_near_duplicate: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    keyword: Mapped["Keyword"] = relationship("Keyword", back_populates="posts")  # noqa: F821
    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="post", lazy="noload")  # noqa: F821
    sentiments: Mapped[list["Sentiment"]] = relationship("Sentiment", back_populates="post", lazy="noload")  # noqa: F821
    entities: Mapped[list["Entity"]] = relationship("Entity", back_populates="post", lazy="noload")  # noqa: F821
