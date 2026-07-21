"""tambah tabel youtube_video_metadata utk Metadata Agent -- ambil info
lengkap video+channel dari YouTube API (videos.list + channels.list) SETELAH
Discovery Agent menemukan+simpan post baru, MURNI pengambilan data (bukan
analisis/AI). 1 baris per post, terhubung via post_id (lihat riwayat
percakapan 2026-07-18).

Revision ID: 018
Revises: 017
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "youtube_video_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", sa.String(255), nullable=False),
        # 1. Informasi dasar
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("duration_iso", sa.String(50), nullable=True),
        sa.Column("category_id", sa.String(20), nullable=True),
        sa.Column("language", sa.String(20), nullable=True),
        # 2. Informasi channel
        sa.Column("channel_id", sa.String(255), nullable=True),
        sa.Column("channel_name", sa.String(255), nullable=True),
        sa.Column("channel_subscriber_count", sa.BigInteger(), nullable=True),
        sa.Column("channel_country", sa.String(10), nullable=True),
        sa.Column("channel_created_at", sa.DateTime(timezone=True), nullable=True),
        # 3. Statistik
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("favorite_count", sa.Integer(), nullable=True),
        sa.Column("favorite_available", sa.Boolean(), nullable=False, server_default="false"),
        # 4. SEO
        sa.Column("tags", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("keyword_matched", sa.String(255), nullable=True),
        sa.Column("topic_categories", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        # viral_context: penjelasan LLM (OpenRouter, model gratis, TANPA
        # pencarian web real-time -- berdasar pengetahuan model + title/
        # description/tags video) soal KENAPA/konteks video ini viral --
        # BUKAN latar belakang topik umum atau reputasi channel.
        sa.Column("viral_context", sa.Text(), nullable=True),
        sa.Column("viral_context_model", sa.String(255), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("post_id", name="uq_youtube_video_metadata_post_id"),
    )
    op.create_index("ix_youtube_video_metadata_post_id", "youtube_video_metadata", ["post_id"])
    op.create_index("ix_youtube_video_metadata_video_id", "youtube_video_metadata", ["video_id"])
    op.create_index("ix_youtube_video_metadata_channel_id", "youtube_video_metadata", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_youtube_video_metadata_channel_id", table_name="youtube_video_metadata")
    op.drop_index("ix_youtube_video_metadata_video_id", table_name="youtube_video_metadata")
    op.drop_index("ix_youtube_video_metadata_post_id", table_name="youtube_video_metadata")
    op.drop_table("youtube_video_metadata")
