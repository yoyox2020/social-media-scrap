"""add trend_recommendations table for AI-submitted viral topic recommendations

Revision ID: 011
Revises: 010
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trend_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("related_accounts", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("source", sa.String(50), nullable=False, server_default="external_ai"),
        sa.Column("recommendation_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("topic", "recommendation_date", name="uq_trend_topic_date"),
    )
    op.create_index("ix_trend_recommendations_topic", "trend_recommendations", ["topic"])
    op.create_index("ix_trend_recommendations_recommendation_date", "trend_recommendations", ["recommendation_date"])
    op.create_index("ix_trend_recommendations_status", "trend_recommendations", ["status"])
    op.create_index("ix_trend_reco_date_score", "trend_recommendations", ["recommendation_date", "score"])


def downgrade() -> None:
    op.drop_table("trend_recommendations")
