import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class TrendRecommendationPlatformUsage(Base, UUIDMixin):
    """
    Tabel PENDAMPING `trend_recommendations` (§3.1
    docs/threads-redesign-schema.md) -- TIDAK mengubah model/tabel
    `trend_recommendations` (models.py di folder yang sama) sama sekali,
    file TERPISAH sengaja supaya jelas tabel frozen itu tidak disentuh.

    `trend_recommendations.status` DIBAGI BERSAMA semua platform (satu
    kolom utk semua) -- tabel ini menyimpan tracking PER-PLATFORM: "topik
    mana yang SUDAH pernah dicoba platform X", independen dari status
    global. Threads adalah platform PERTAMA yang pakai ini (2026-07-21);
    platform lain TIDAK WAJIB ikut, boleh ditambahkan belakangan.
    """
    __tablename__ = "trend_recommendation_platform_usage"
    __table_args__ = (
        UniqueConstraint("trend_recommendation_id", "platform", name="uq_trend_reco_platform_usage"),
    )

    trend_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trend_recommendations.id", ondelete="CASCADE"), nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
