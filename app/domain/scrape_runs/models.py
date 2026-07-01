import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class ScrapeRun(Base, UUIDMixin):
    """
    Riwayat setiap scraping run — dicatat oleh Celery worker.
    Dipakai untuk monitoring: apakah scraping jalan, API mana yang dipakai, hasilnya apa.
    """
    __tablename__ = "scrape_runs"

    keyword_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True, index=True
    )
    keyword_text: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, default="youtube")
    api_source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="ensembledata",
        comment="ensembledata | youtube_data_api | unknown"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", index=True,
        comment="running | success | failed | fallback"
    )
    triggered_by: Mapped[str] = mapped_column(
        String(30), nullable=False, default="celery_beat",
        comment="celery_beat | manual_api | manual_cli"
    )
    videos_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    videos_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    videos_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
