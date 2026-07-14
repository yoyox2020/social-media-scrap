"""tambah SearchTopic.last_ai_discovery_at utk rotasi budget AI-context
discovery (app/services/search_topics/ai_discovery_service.py)

Revision ID: 014
Revises: 013
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("search_topics", sa.Column("last_ai_discovery_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_search_topics_last_ai_discovery_at", "search_topics", ["last_ai_discovery_at"])


def downgrade() -> None:
    op.drop_index("ix_search_topics_last_ai_discovery_at", table_name="search_topics")
    op.drop_column("search_topics", "last_ai_discovery_at")
