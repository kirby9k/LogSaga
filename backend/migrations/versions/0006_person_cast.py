"""Add person and item_cast tables for actor/cast data

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12

Adds:
  person     — TMDB person identity, portrait path, lazily-fetched biography
  item_cast  — junction between person and (media_item for movies | kodi_show for TV)
               credit_order preserves TMDB billing order

No changes to existing rows or columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person",
        sa.Column("tmdb_person_id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("profile_path", sa.Text, nullable=True),
        sa.Column("profile_local_path", sa.Text, nullable=True),
        sa.Column("profile_thumb_path", sa.Text, nullable=True),
        sa.Column("biography", sa.Text, nullable=True),
        sa.Column("birthday", sa.String(20), nullable=True),
        sa.Column("birthplace", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "item_cast",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "person_id",
            sa.Integer,
            sa.ForeignKey("person.tmdb_person_id"),
            nullable=False,
        ),
        sa.Column(
            "media_item_id",
            sa.Integer,
            sa.ForeignKey("media_item.id"),
            nullable=True,
        ),
        sa.Column(
            "show_id",
            sa.Integer,
            sa.ForeignKey("kodi_show.id"),
            nullable=True,
        ),
        sa.Column("character", sa.Text, nullable=True),
        sa.Column("credit_order", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("person_id", "media_item_id", name="uq_item_cast_person_movie"),
        sa.UniqueConstraint("person_id", "show_id", name="uq_item_cast_person_show"),
    )
    op.create_index("ix_item_cast_person_id", "item_cast", ["person_id"])
    op.create_index("ix_item_cast_media_item_id", "item_cast", ["media_item_id"])
    op.create_index("ix_item_cast_show_id", "item_cast", ["show_id"])


def downgrade() -> None:
    op.drop_index("ix_item_cast_show_id", table_name="item_cast")
    op.drop_index("ix_item_cast_media_item_id", table_name="item_cast")
    op.drop_index("ix_item_cast_person_id", table_name="item_cast")
    op.drop_table("item_cast")
    op.drop_table("person")
