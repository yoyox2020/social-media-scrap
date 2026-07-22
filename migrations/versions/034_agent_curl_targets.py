"""Target curl utk crawling per agent (2026-07-22, permintaan user)
-- 1 agent bisa punya BANYAK target curl (URL + method + header +
body), BEDA dari third_party_apis (itu 1:1 dgn agent). Cocok by NAMA
(agent_name), bukan FK id, sama seperti agent_key_pool/agent_registry.

Revision ID: 034
Revises: 033
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_curl_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("method", sa.String(10), nullable=False, server_default="GET"),
        sa.Column("headers", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_curl_targets_agent_name", "agent_curl_targets", ["agent_name"])


def downgrade() -> None:
    op.drop_index("ix_agent_curl_targets_agent_name", table_name="agent_curl_targets")
    op.drop_table("agent_curl_targets")
