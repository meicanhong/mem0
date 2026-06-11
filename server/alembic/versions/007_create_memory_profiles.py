"""Create memory_profiles table

Revision ID: 007
Revises: 006
Create Date: 2026-06-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("profile_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("profile_json", sa.JSON(), nullable=True),
        sa.Column("source_memory_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_memory_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_cursor_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_memory_profiles_user_id", "memory_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_profiles_user_id", table_name="memory_profiles")
    op.drop_table("memory_profiles")
