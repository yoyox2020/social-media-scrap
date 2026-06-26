"""
TrendingTopic — snapshot Google Trends per fetch.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class TrendingTopic(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "trending_topics"

    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    traffic: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    geo: Mapped[str] = mapped_column(String(10), nullable=False, default="ID")
    period: Mapped[str] = mapped_column(String(10), nullable=False, default="24h")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
