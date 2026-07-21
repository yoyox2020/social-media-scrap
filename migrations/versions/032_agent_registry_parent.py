"""Tambah kolom parent_agent_name ke agent_registry (2026-07-22) --
hierarki parent-child antar agent: agent fungsional (agent_viral, dst)
punya child per-platform (agent_viral_youtube, dst), agent platform
(agent_youtube, dst) punya child per-fungsi pipeline (agent_youtube_
discovery, dst). Relasi lewat NAMA (bukan FK id) krn 1 "agent" secara
konsep = kumpulan baris yg berbagi agent_name (lihat
app/services/agent_registry/service.py::list_agents() yg group by
agent_name) -- parent_agent_name cukup cocokkan ke agent_name manapun
yg sudah ada, tanpa perlu FK ketat. Additive only, nullable.

Revision ID: 032
Revises: 031
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_registry", sa.Column("parent_agent_name", sa.String(255), nullable=True))
    op.create_index("ix_agent_registry_parent_agent_name", "agent_registry", ["parent_agent_name"])


def downgrade() -> None:
    op.drop_index("ix_agent_registry_parent_agent_name", table_name="agent_registry")
    op.drop_column("agent_registry", "parent_agent_name")
