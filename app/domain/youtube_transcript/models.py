"""Model transcript YouTube (2026-07-25) -- lihat migrasi 041 utk
alasan desain (reuse posts.id, segment per-baris bukan text panjang)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class YoutubeTranscript(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "youtube_transcripts"

    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
    )
    video_external_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_translated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # manual | generated | unavailable | error
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="unavailable", index=True)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    segments: Mapped[list["YoutubeTranscriptSegment"]] = relationship(
        "YoutubeTranscriptSegment", back_populates="transcript", lazy="noload", cascade="all, delete-orphan",
    )


class YoutubeTranscriptSegment(Base, UUIDMixin):
    __tablename__ = "youtube_transcript_segments"
    __table_args__ = (UniqueConstraint("transcript_id", "segment_index", name="uq_youtube_transcript_segments_transcript_index"),)

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_transcripts.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_second: Mapped[float] = mapped_column(Float, nullable=False)
    end_second: Mapped[float] = mapped_column(Float, nullable=False)
    duration: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    transcript: Mapped["YoutubeTranscript"] = relationship("YoutubeTranscript", back_populates="segments", lazy="noload")
