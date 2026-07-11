import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base


class SearchTopic(Base):
    __tablename__ = "search_topics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    platforms: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=["youtube"])
    scheduled_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_crawl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Smart Search — jadwal pencarian berkala (Celery task workers.search_topics.daily_rescan,
    # lihat app/services/search_topics/rescan_service.py). BEDA dari scheduled_hour di atas
    # (field lama, tidak pernah dibaca worker manapun -- dibiarkan apa adanya, tidak dipakai
    # di sini). schedule_expires_at DIHITUNG SEKALI saat recurring diaktifkan/diubah
    # (schedule_started_at + schedule_duration_days), bukan dihitung ulang tiap query --
    # supaya filter task harian tinggal `WHERE schedule_expires_at > now()`, tidak perlu
    # date-math per baris.
    schedule_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    topic_keywords: Mapped[list["SearchTopicKeyword"]] = relationship(
        "SearchTopicKeyword", back_populates="topic", cascade="all, delete-orphan", lazy="noload"
    )


class SearchTopicKeyword(Base):
    __tablename__ = "search_topic_keywords"

    topic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("search_topics.id", ondelete="CASCADE"), primary_key=True)
    keyword_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True)
    keyword_text: Mapped[str] = mapped_column(String(255), nullable=False)
    # Penanda cooldown per keyword utk workers.search_topics.daily_rescan -- punya sendiri
    # (bukan reuse scrape_runs.keyword_text) krn formatnya tidak konsisten lintas platform
    # yang sudah ada (kadang persis keyword, kadang "search:{identifier}", dst).
    last_rescanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    topic: Mapped["SearchTopic"] = relationship("SearchTopic", back_populates="topic_keywords")
