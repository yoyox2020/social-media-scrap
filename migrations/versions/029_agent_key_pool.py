"""Pool rotasi API key per agent (2026-07-22, permintaan user) --
1 agent (agent_name, cocok dgn agent_registry.agent_name) bisa punya
BANYAK key kandidat, bukan cuma 1 spt agent_registry.custom_api_key.

Beda dari agent_registry: tabel ini KHUSUS utk rotasi -- status per key
(active/exhausted/disabled), exhausted_until (TTL, auto-pulih sendiri
tanpa reset manual), priority (urutan dicoba). agent_registry TETAP ada
sbg identitas+tampilan ringkas (key "aktif saat ini"), tabel ini yg jadi
sumber kebenaran rotasi sebenarnya.

Revision ID: 029
Revises: 028
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_key_pool",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("api_key", sa.Text, nullable=False),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("account_email", sa.String(255), nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("exhausted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_key_pool_agent_name", "agent_key_pool", ["agent_name", "status", "priority"])


def downgrade() -> None:
    op.drop_index("ix_agent_key_pool_agent_name", table_name="agent_key_pool")
    op.drop_table("agent_key_pool")
