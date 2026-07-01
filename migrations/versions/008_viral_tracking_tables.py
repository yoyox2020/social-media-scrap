"""add viral_channel_trackers and flagged_accounts tables

Revision ID: 008
Revises: 007
Create Date: 2026-07-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── viral_channel_trackers ────────────────────────────────────────────────
    op.create_table(
        "viral_channel_trackers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("channel_name", sa.String(500), nullable=False),
        sa.Column("trigger_post_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("posts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("keyword_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tracker_type", sa.String(50), nullable=False, server_default="viral"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("posts_collected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_scraped_date", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_vct_channel_id", "viral_channel_trackers", ["channel_id"])
    op.create_index("ix_vct_status", "viral_channel_trackers", ["status"])
    op.create_index("ix_vct_trigger_post_id", "viral_channel_trackers", ["trigger_post_id"])

    # ── flagged_accounts ──────────────────────────────────────────────────────
    op.create_table(
        "flagged_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("channel_name", sa.String(500), nullable=False),
        sa.Column("comment_count", sa.Integer, nullable=False),
        sa.Column("tracker_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("viral_channel_trackers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_post_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("posts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("analysis_tracker_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("viral_channel_trackers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_fa_channel_id", "flagged_accounts", ["channel_id"])
    op.create_index("ix_fa_tracker_id", "flagged_accounts", ["tracker_id"])


def downgrade() -> None:
    op.drop_table("flagged_accounts")
    op.drop_table("viral_channel_trackers")
