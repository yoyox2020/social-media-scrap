import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class ViralChannelTracker(Base, UUIDMixin, TimestampMixin):
    """
    Melacak channel YouTube yang sedang dipantau karena salah satu postingannya viral.
    Scraping berjalan otomatis 5 post/hari selama 7 hari (ends_at).

    tracker_type:
      'viral'             — dipicu oleh post viral (view >= threshold)
      'flagged_commenter' — dipicu oleh akun yang berkomentar >10x di post viral
    """
    __tablename__ = "viral_channel_trackers"

    channel_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    channel_name: Mapped[str] = mapped_column(String(500), nullable=False)
    trigger_post_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    keyword_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tracker_type: Mapped[str] = mapped_column(String(50), nullable=False, default="viral", server_default="viral")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active", index=True)
    posts_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_scraped_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    scrape_logs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    flagged_accounts: Mapped[list["FlaggedAccount"]] = relationship(
        "FlaggedAccount",
        foreign_keys="FlaggedAccount.tracker_id",
        back_populates="tracker",
        lazy="noload",
    )


class FlaggedAccount(Base, UUIDMixin, TimestampMixin):
    """
    Akun yang berkomentar >10x pada post yang sedang dilacak.
    Setelah diflag, sistem otomatis membuat ViralChannelTracker baru
    untuk menganalisis konten channel akun tersebut selama 7 hari.
    """
    __tablename__ = "flagged_accounts"

    channel_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    channel_name: Mapped[str] = mapped_column(String(500), nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tracker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("viral_channel_trackers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger_post_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posts.id", ondelete="SET NULL"), nullable=True
    )
    analysis_tracker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("viral_channel_trackers.id", ondelete="SET NULL"), nullable=True
    )
    flagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tracker: Mapped["ViralChannelTracker"] = relationship(
        "ViralChannelTracker",
        foreign_keys=[tracker_id],
        back_populates="flagged_accounts",
    )
