import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin, TimestampMixin


class YouTubeVideoMetadata(Base, UUIDMixin, TimestampMixin):
    """
    Info lengkap 1 video YouTube (video + channel), diambil MURNI dari
    YouTube API (videos.list + channels.list) -- BUKAN analisis/AI, cuma
    pengambilan data. Dibuat oleh Metadata Agent
    (app/services/youtube_metadata/agent.py) SETELAH Discovery Agent (atau
    pipeline lain) simpan post baru ke `posts`. 1 baris per post
    (UniqueConstraint post_id).
    """
    __tablename__ = "youtube_video_metadata"
    __table_args__ = (UniqueConstraint("post_id", name="uq_youtube_video_metadata_post_id"),)

    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # 1. Informasi dasar
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_iso: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # 2. Informasi channel
    channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_subscriber_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    channel_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 3. Statistik
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    likes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comments: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    favorite_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # favorite_available: YouTube TIDAK PERNAH benar2 hapus field ini dari
    # response API, tapi nilainya bisa absen/None kalau statistics disabled
    # utk video itu -- flag ini beda dari favorite_count=0 (post PUNYA stat
    # tapi nilainya nol) vs favorite_available=False (stat-nya sendiri
    # tidak tersedia dari API).
    favorite_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # 4. SEO
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # keyword_matched: kata kunci topic-search yg menemukan video ini (mode
    # topic-guided Discovery Agent) -- None kalau dari mode free discovery
    # atau sumber lain yg tidak terikat keyword.
    keyword_matched: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # topic_categories: kategori topik menurut YOUTUBE sendiri
    # (topicDetails.topicCategories, list URL Wikipedia) -- BEDA dari
    # keyword_matched (topik KITA), ini klasifikasi YouTube.
    topic_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # viral_context: penjelasan LLM (OpenRouter, model gratis, TANPA
    # pencarian web real-time -- berdasar pengetahuan model + title/
    # description/tags video) soal KENAPA/konteks video ini viral -- BUKAN
    # latar belakang topik umum atau reputasi channel. None kalau LLM
    # belum/gagal dipanggil (data video+channel tetap tersimpan).
    viral_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    viral_context_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Verifikasi judul vs id video (2026-07-18, permintaan user) -- OBJEKTIF,
    # bandingkan `title` tersimpan vs title ASLI YouTube pada refresh
    # berikutnya. Kalau beda: title_mismatch=True + title_live diisi title
    # yg baru dilihat, TAPI `title` tersimpan TIDAK ditimpa otomatis (cuma
    # ditandai utk ditinjau manual -- video bisa memang ganti judul/clickbait
    # edit, atau indikasi data awal salah).
    title_mismatch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    title_live: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
