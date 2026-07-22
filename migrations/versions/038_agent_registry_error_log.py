"""Log error/habis per-kartu di Kelola Agent (2026-07-22, permintaan
user) -- SAMA pola dgn migrasi 037 (third_party_apis), tapi di
agent_registry -- INI cakupan LEBIH LUAS krn kebanyakan agent py key
LANGSUNG di sini (bukan lewat katalog API Pihak Ketiga).

Revision ID: 038
Revises: 037
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_registry", sa.Column("last_error", sa.Text, nullable=True))
    op.add_column("agent_registry", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_registry", "last_error_at")
    op.drop_column("agent_registry", "last_error")
