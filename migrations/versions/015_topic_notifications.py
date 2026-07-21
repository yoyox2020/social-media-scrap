"""tambah tabel topic_notifications utk fitur notifikasi viral per jam
(app/services/search_topics/notification_service.py)

Revision ID: 015
Revises: 014
Create Date: 2026-07-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "topic_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("search_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("keyword_text", sa.String(255), nullable=False),
        sa.Column("metric_type", sa.String(20), nullable=False),
        sa.Column("metric_value", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("topic_id", "post_id", name="uq_topic_notification_topic_post"),
    )
    op.create_index("ix_topic_notifications_topic_id", "topic_notifications", ["topic_id"])
    op.create_index("ix_topic_notifications_platform", "topic_notifications", ["platform"])
    op.create_index("ix_topic_notifications_is_read", "topic_notifications", ["is_read"])


def downgrade() -> None:
    op.drop_index("ix_topic_notifications_is_read", table_name="topic_notifications")
    op.drop_index("ix_topic_notifications_platform", table_name="topic_notifications")
    op.drop_index("ix_topic_notifications_topic_id", table_name="topic_notifications")
    op.drop_table("topic_notifications")
