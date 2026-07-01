"""add scrape_runs table for monitoring

Revision ID: 010
Revises: 009
Create Date: 2026-07-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scrape_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("keyword_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True),
        sa.Column("keyword_text", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False, server_default="youtube"),
        sa.Column("api_source", sa.String(50), nullable=False, server_default="ensembledata"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("triggered_by", sa.String(30), nullable=False, server_default="celery_beat"),
        sa.Column("videos_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("videos_new", sa.Integer, nullable=False, server_default="0"),
        sa.Column("videos_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comments_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comments_new", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_scrape_runs_status", "scrape_runs", ["status"])
    op.create_index("ix_scrape_runs_started_at", "scrape_runs", ["started_at"])
    op.create_index("ix_scrape_runs_keyword_id", "scrape_runs", ["keyword_id"])


def downgrade() -> None:
    op.drop_table("scrape_runs")
