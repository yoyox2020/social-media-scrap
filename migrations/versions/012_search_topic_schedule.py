"""add scheduled recurring re-scan columns to search_topics + search_topic_keywords

Revision ID: 012
Revises: 011
Create Date: 2026-07-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("search_topics", sa.Column("schedule_recurring", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("search_topics", sa.Column("schedule_duration_days", sa.Integer, nullable=True))
    op.add_column("search_topics", sa.Column("schedule_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_topics", sa.Column("schedule_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_search_topics_schedule_expires_at", "search_topics", ["schedule_expires_at"])

    op.add_column("search_topic_keywords", sa.Column("last_rescanned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("search_topic_keywords", "last_rescanned_at")

    op.drop_index("ix_search_topics_schedule_expires_at", table_name="search_topics")
    op.drop_column("search_topics", "schedule_expires_at")
    op.drop_column("search_topics", "schedule_started_at")
    op.drop_column("search_topics", "schedule_duration_days")
    op.drop_column("search_topics", "schedule_recurring")
