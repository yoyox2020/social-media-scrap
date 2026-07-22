"""Bank key BERSAMA utk rotasi otomatis (2026-07-22, permintaan user)
-- BEDA dari agent_key_pool (itu pool KANDIDAT milik 1 agent tertentu).
Di sini key belum tentu milik siapa2 (status=available), diambil
otomatis oleh sistem saat 1 agent lapor key-nya gagal (401/402/429/dst),
lalu di-assign ke agent itu (status=assigned, assigned_to_agent diisi).

Revision ID: 036
Revises: 035
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rotation_key_bank",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("api_key", sa.Text, nullable=False),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("account_email", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="available"),
        sa.Column("assigned_to_agent", sa.String(255), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_rotation_key_bank_status", "rotation_key_bank", ["status"])
    op.create_index("ix_rotation_key_bank_assigned_to_agent", "rotation_key_bank", ["assigned_to_agent"])


def downgrade() -> None:
    op.drop_index("ix_rotation_key_bank_assigned_to_agent", table_name="rotation_key_bank")
    op.drop_index("ix_rotation_key_bank_status", table_name="rotation_key_bank")
    op.drop_table("rotation_key_bank")
