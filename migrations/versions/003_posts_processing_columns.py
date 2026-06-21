"""add processing columns to posts table

Revision ID: 003
Revises: 002
Create Date: 2026-06-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("cleaned_content", sa.Text(), nullable=True))
    op.add_column("posts", sa.Column("language", sa.String(10), nullable=True))
    op.add_column("posts", sa.Column("is_processed", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("posts", sa.Column("is_near_duplicate", sa.Boolean(), nullable=False, server_default="false"))

    op.create_index("ix_posts_is_processed", "posts", ["is_processed"])
    op.create_index("ix_posts_language", "posts", ["language"])


def downgrade() -> None:
    op.drop_index("ix_posts_language", table_name="posts")
    op.drop_index("ix_posts_is_processed", table_name="posts")
    op.drop_column("posts", "is_near_duplicate")
    op.drop_column("posts", "is_processed")
    op.drop_column("posts", "language")
    op.drop_column("posts", "cleaned_content")
