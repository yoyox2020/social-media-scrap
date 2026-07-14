"""sementara: default role user jadi admin + semua user existing ikut di-admin-kan
(supaya gampang testing di server tanpa kehalang RBAC, akan disesuaikan lagi nanti)

Revision ID: 013
Revises: 012
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "role", server_default="admin")
    op.execute("UPDATE users SET role = 'admin' WHERE role != 'admin'")


def downgrade() -> None:
    op.alter_column("users", "role", server_default="user")
