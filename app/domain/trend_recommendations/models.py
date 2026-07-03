from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, Float, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class TrendRecommendation(Base, UUIDMixin, TimestampMixin):
    """
    Topik viral yang direkomendasikan oleh AI eksternal (lintas platform,
    lintas isu — bukan spesifik satu social media).

    Dikirim via POST /trend-recommendations (endpoint publik, tanpa auth,
    supaya sistem AI eksternal bisa langsung submit). Maksimal 20 topik
    tersimpan per hari (recommendation_date) — kalau sudah penuh, topik baru
    menggantikan topik dengan score terendah hanya jika score-nya lebih tinggi.

    related_accounts: list[{"platform": "twitter", "username": "..."}] — akun
    yang teridentifikasi ikut viral untuk topik ini, dipakai sebagai patokan
    untuk tahap pencarian/scraping berikutnya (per keyword & per username).

    status: 'pending' (belum dipakai pipeline pencarian) -> 'used' setelah
    dikonsumsi oleh proses scraping berikutnya.
    """
    __tablename__ = "trend_recommendations"
    __table_args__ = (
        UniqueConstraint("topic", "recommendation_date", name="uq_trend_topic_date"),
        Index("ix_trend_reco_date_score", "recommendation_date", "score"),
    )

    topic: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    related_accounts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="external_ai", server_default="external_ai")
    recommendation_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
