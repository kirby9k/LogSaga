"""Add artwork table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artwork",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("art_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_item_id", "art_type", name="uq_artwork_item_type"),
    )
    op.create_index("ix_artwork_media_item_id", "artwork", ["media_item_id"])


def downgrade() -> None:
    op.drop_index("ix_artwork_media_item_id", table_name="artwork")
    op.drop_table("artwork")
