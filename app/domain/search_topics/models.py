import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


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
    # Agent (dari tabel agent_registry/agent_key_pool, mis. "agent_youtube")
    # yg bertugas memproses topik ini -- ditambahkan 2026-07-22 (API v2).
    # Nullable: topik lama/belum ditugaskan tetap valid, tidak WAJIB diisi.
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
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
    # Kapan topik ini TERAKHIR benar-benar dapat panggilan AI-context discovery
    # (app/services/search_topics/ai_discovery_service.py) -- HANYA di-update
    # saat AI beneran dipanggil (bukan saat di-skip krn sudah tercover), dipakai
    # urutkan rotasi ASC NULLS FIRST supaya topik yang belum/paling lama
    # dipanggil menang duluan saat budget harian terbatas.
    last_ai_discovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
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


class TopicNotification(Base, UUIDMixin, TimestampMixin):
    """
    Notifikasi "topik ini lagi viral" -- dibuat task Celery per jam
    (app/services/search_topics/notification_service.py) begitu ada post
    yang cocok keyword topik DAN metriknya (views/likes, tergantung
    platform) lewat ambang batas (disimpan di Redis, BUKAN .env -- supaya
    bisa diubah live tanpa restart, lihat notification_service.py).

    UniqueConstraint(topic_id, post_id) dipakai SEKALIGUS sbg mekanisme
    dedup "post ini sudah pernah dinotifikasi utk topik ini belum" --
    insert baru pakai ON CONFLICT DO NOTHING, bukan query SELECT existence
    check terpisah.
    """
    __tablename__ = "topic_notifications"
    __table_args__ = (
        UniqueConstraint("topic_id", "post_id", name="uq_topic_notification_topic_post"),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posts.id", ondelete="SET NULL"), nullable=True
    )
    keyword_text: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "views" | "likes"
    metric_value: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Snapshot tanggal upload ASLI konten (BUKAN kapan notifikasi dibuat) --
    # ditambahkan 2026-07-20 supaya user bisa lihat sekilas seberapa "baru"
    # konten yang dinotifikasi (ambang batas viral di sini murni angka
    # tetap, TIDAK ada komponen waktu -- post 3 minggu lalu yg baru
    # ke-notif tetap valid selama masih dalam lookback_days & belum pernah
    # dinotif, lihat notification_service.py). Disimpan sbg snapshot
    # (pola sama dgn title/author/url di atas) supaya tetap ada nilainya
    # walau post_id kelak SET NULL (post dihapus).
    post_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false", index=True)
