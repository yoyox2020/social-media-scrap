"""add unique constraint on posts(external_id, platform) for deduplication

Revision ID: 002
Revises: 001
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_posts_external_id_platform",
        "posts",
        ["external_id", "platform"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_posts_external_id_platform", table_name="posts")
