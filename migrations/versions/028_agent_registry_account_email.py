"""Tambah kolom account_email ke agent_registry (2026-07-22) -- bagian
dari permintaan awal user "manage akun, key dan modelnya" yang sempat
terlewat di migrasi 027. Additive only, nullable.

Revision ID: 028
Revises: 027
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_registry", sa.Column("account_email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_registry", "account_email")
