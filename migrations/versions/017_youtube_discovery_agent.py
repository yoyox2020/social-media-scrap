"""tambah tabel youtube_discovery_runs utk YouTube Discovery Agent --
pencarian video viral/trending otomatis (topic-guided + free discovery),
divalidasi LLM (OpenRouter) sebelum simpan ke posts. Status monitor +
riwayat detail (kolom `details` JSON) utk dianalisis tim (lihat riwayat
percakapan 2026-07-18).

Revision ID: 017
Revises: 016
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "youtube_discovery_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),  # running | success | failed
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("topics_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_validated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("posts_saved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_used", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # details: list per-kandidat {mode, topic, video_id, title, verdict,
        # reason, saved} -- rincian lengkap utk dianalisis, BUKAN cuma angka
        # ringkasan di atas.
        sa.Column("details", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_youtube_discovery_runs_status", "youtube_discovery_runs", ["status"])
    op.create_index("ix_youtube_discovery_runs_started_at", "youtube_discovery_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_youtube_discovery_runs_started_at", table_name="youtube_discovery_runs")
    op.drop_index("ix_youtube_discovery_runs_status", table_name="youtube_discovery_runs")
    op.drop_table("youtube_discovery_runs")
