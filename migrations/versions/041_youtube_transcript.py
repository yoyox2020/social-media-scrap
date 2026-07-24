"""Tabel transcript YouTube (2026-07-25, permintaan user "YouTube
Transcript AI Agent") -- 2 tabel baru: `youtube_transcripts` (1 baris/
video, metadata transcript) + `youtube_transcript_segments` (banyak
baris/video, per potongan waktu -- BUKAN 1 text panjang, sesuai
permintaan eksplisit user "transcript harus disimpan per segment").

REUSE `posts.id` yg SUDAH ADA (platform='youtube') sbg foreign key,
BUKAN tabel `youtube_metadata` terpisah spt diasumsikan README generik
user -- project ini pakai 1 tabel `posts` bersama lintas platform.

Revision ID: 041
Revises: 040
Create Date: 2026-07-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "youtube_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("video_external_id", sa.String(50), nullable=False),
        sa.Column("language", sa.String(20), nullable=True),
        sa.Column("is_generated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_translated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False, server_default="unavailable"),  # manual|generated|unavailable|error
        sa.Column("segment_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_youtube_transcripts_post_id", "youtube_transcripts", ["post_id"], unique=True)
    op.create_index("ix_youtube_transcripts_video_external_id", "youtube_transcripts", ["video_external_id"])
    op.create_index("ix_youtube_transcripts_source", "youtube_transcripts", ["source"])

    op.create_table(
        "youtube_transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("youtube_transcripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_index", sa.Integer, nullable=False),
        sa.Column("start_second", sa.Float, nullable=False),
        sa.Column("end_second", sa.Float, nullable=False),
        sa.Column("duration", sa.Float, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_youtube_transcript_segments_transcript_id", "youtube_transcript_segments", ["transcript_id"])
    op.create_unique_constraint(
        "uq_youtube_transcript_segments_transcript_index",
        "youtube_transcript_segments", ["transcript_id", "segment_index"],
    )


def downgrade() -> None:
    op.drop_table("youtube_transcript_segments")
    op.drop_table("youtube_transcripts")
