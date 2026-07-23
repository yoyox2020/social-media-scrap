import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class TrendRecommendationKeyword(Base, UUIDMixin):
    """Keyword KUSTOM per topik (2026-07-24, permintaan user "1 topik
    bisa create beberapa keyword") -- tabel PENDAMPING
    trend_recommendations (pola SAMA dgn platform_usage_models.py),
    TIDAK mengubah tabel frozen itu. Kalau topik py >=1 baris di sini,
    agent_search.build_keywords() pakai keyword2 ini alih-alih 3-varian
    auto default."""
    __tablename__ = "trend_recommendation_keywords"
    __table_args__ = (
        UniqueConstraint("trend_recommendation_id", "keyword_text", name="uq_trend_reco_keyword"),
    )

    trend_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trend_recommendations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    keyword_text: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
