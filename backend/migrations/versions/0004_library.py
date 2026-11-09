"""Add kodi_show table; add date_added + show_id to media_item

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07

Adds:
  kodi_show  — new table for TV show identity and metadata (plot, studio, status, etc.)
  media_item.date_added  — when Kodi added the file; sentinel '1951-09-25 00:00:00' for NULLs
  media_item.show_id     — FK to kodi_show.id (episodes only; NULL for movies)

Existing rows get NULL for the new columns. Values are backfilled by `logsaga sync-library`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create kodi_show table if not already present
    # (may exist if a previous migration attempt partially succeeded)
    has_kodi_show = conn.execute(
        text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='kodi_show'")
    ).scalar()
    if not has_kodi_show:
        op.create_table(
            "kodi_show",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kodi_idShow", sa.Integer(), nullable=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("plot", sa.Text(), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("premiered", sa.String(20), nullable=True),
            sa.Column("studio", sa.String(200), nullable=True),
            sa.Column("status", sa.String(50), nullable=True),
            sa.Column("genres", sa.Text(), nullable=True),
            sa.Column("tvdb_id", sa.String(20), nullable=True),
            sa.Column("imdb_id", sa.String(20), nullable=True),
            sa.Column("tmdb_id", sa.String(20), nullable=True),
            sa.Column(
                "kodi_state",
                sa.Enum("watched", "unwatched", "in_progress", "removed", name="kodistate"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("kodi_idShow", name="uq_kodi_show_kodi_idShow"),
        )
        op.create_index("ix_kodi_show_title", "kodi_show", ["title"])

    # Add date_added and show_id to media_item.
    # Disable FK enforcement while batch_alter_table recreates the table
    # (other tables reference media_item, which would block the DROP).
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    with op.batch_alter_table("media_item") as batch_op:
        batch_op.add_column(sa.Column("date_added", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("show_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_media_item_show_id", ["show_id"])
        batch_op.create_foreign_key(
            "fk_media_item_show_id",
            "kodi_show",
            ["show_id"],
            ["id"],
        )
    conn.execute(text("PRAGMA foreign_keys=ON"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    with op.batch_alter_table("media_item") as batch_op:
        batch_op.drop_constraint("fk_media_item_show_id", type_="foreignkey")
        batch_op.drop_index("ix_media_item_show_id")
        batch_op.drop_column("show_id")
        batch_op.drop_column("date_added")
    conn.execute(text("PRAGMA foreign_keys=ON"))

    op.drop_index("ix_kodi_show_title", "kodi_show")
    op.drop_table("kodi_show")
