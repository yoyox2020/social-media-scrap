from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class InstagramTrendingAccount(Base, UUIDMixin, TimestampMixin):
    """
    Akun Instagram yang terdeteksi trending via discovery hashtag.

    Discovery berjalan otomatis setiap hari (Celery Beat 09:00 WIB):
      1. Search hashtag #indonesia, #viral, #fyp → extract username
      2. Hitung trending_score dari data post di DB
      3. Top 5 → auto-scrape 2 post + 5 komentar terpopuler

    source = nama provider discovery ('ensembledata', 'rapidapi', dll) → pluggable.
    """
    __tablename__ = "instagram_trending_accounts"

    username: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="ensembledata", server_default="ensembledata", index=True)
    discovered_via: Mapped[str | None] = mapped_column(String(255), nullable=True)  # hashtag asal

    # Ranking & score
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trending_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    engagement_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    virality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")

    # Snapshot metrics saat discovery
    followers: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    posts_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Status & tracking
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active", index=True)
    last_scraped_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    scrape_logs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
