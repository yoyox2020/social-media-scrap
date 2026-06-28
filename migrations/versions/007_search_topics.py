"""007_search_topics — tabel topik pencarian + relasi ke keywords

Revision ID: 007_search_topics
Revises: 006_youtube_pipeline_tables
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY, TEXT

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tabel topik pencarian
    op.create_table(
        "search_topics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("platforms", ARRAY(TEXT), nullable=False, server_default="{youtube}"),
        sa.Column("scheduled_hour", sa.Integer, nullable=True),
        sa.Column("auto_crawl", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_search_topics_name", "search_topics", [sa.text("lower(name)")])

    # Tabel relasi topik ↔ keyword (many-to-many)
    op.create_table(
        "search_topic_keywords",
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("search_topics.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("keyword_id", UUID(as_uuid=True), sa.ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("keyword_text", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("search_topic_keywords")
    op.drop_index("ix_search_topics_name", table_name="search_topics")
    op.drop_table("search_topics")
