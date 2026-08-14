"""data_sources (§30 Universal Data Layer)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14

Hand-written: autogenerate must not see the FTS5 shadow tables from 0002.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

import retinue.db.types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", retinue.db.types.UUIDBlob(length=16), nullable=False),
        sa.Column("owner_id", retinue.db.types.UUIDBlob(length=16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("config", _JSON, nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("policy", _JSON, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_test", _JSON, nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_data_sources_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sources")),
    )


def downgrade() -> None:
    op.drop_table("data_sources")
