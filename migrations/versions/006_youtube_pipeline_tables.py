"""add trending_topics and lexicon_analyses tables for YouTube pipeline

Revision ID: 006
Revises: 005
Create Date: 2026-06-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── trending_topics ───────────────────────────────────────────────────────
    op.create_table(
        "trending_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("traffic", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("geo", sa.String(10), nullable=False, server_default="ID"),
        sa.Column("period", sa.String(10), nullable=False, server_default="24h"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_trending_topics_title", "trending_topics", ["title"])
    op.create_index("ix_trending_topics_fetched_at", "trending_topics", ["fetched_at"])
    op.create_index("ix_trending_topics_geo_period", "trending_topics", ["geo", "period"])

    # ── lexicon_analyses ──────────────────────────────────────────────────────
    op.create_table(
        "lexicon_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "comment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "keyword_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("keywords.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("matched_positive", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("matched_negative", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("removed_stopwords", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(20), nullable=False, server_default="netral"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_lexicon_analyses_comment_id", "lexicon_analyses", ["comment_id"])
    op.create_index("ix_lexicon_analyses_keyword_id", "lexicon_analyses", ["keyword_id"])
    op.create_index("ix_lexicon_analyses_label", "lexicon_analyses", ["label"])


def downgrade() -> None:
    op.drop_table("lexicon_analyses")
    op.drop_table("trending_topics")
