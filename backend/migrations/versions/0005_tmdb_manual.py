"""Add tmdb_cache and recently_viewed tables; index kodi_show.tmdb_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12

Adds:
  tmdb_cache       — 30-day TTL JSON cache for TMDB API responses
  recently_viewed  — server-side log of TMDB pages browsed (capped at 50)
  ix_kodi_show_tmdb_id — index on kodi_show.tmdb_id for dedup lookups

No changes to existing rows or columns. SessionSource and CompletionReason
enums gain 'manual' values — SQLite stores them as VARCHAR so no schema change
is required; Alembic Enum columns are unchecked in SQLite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # --- tmdb_cache ---
    has_tmdb_cache = conn.execute(
        text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tmdb_cache'")
    ).scalar()
    if not has_tmdb_cache:
        op.create_table(
            "tmdb_cache",
            sa.Column("cache_key", sa.String(200), nullable=False),
            sa.Column("data_json", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("cache_key"),
        )

    # --- recently_viewed ---
    has_recently_viewed = conn.execute(
        text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='recently_viewed'")
    ).scalar()
    if not has_recently_viewed:
        op.create_table(
            "recently_viewed",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("media_type", sa.String(10), nullable=False),
            sa.Column("tmdb_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("poster_url", sa.Text(), nullable=True),
            sa.Column("viewed_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("media_type", "tmdb_id", name="uq_recently_viewed_tmdb"),
        )
        op.create_index("ix_recently_viewed_viewed_at", "recently_viewed", ["viewed_at"])

    # --- index on kodi_show.tmdb_id (for TMDB-only show dedup lookups) ---
    has_index = conn.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='index' AND name='ix_kodi_show_tmdb_id'"
        )
    ).scalar()
    if not has_index:
        op.create_index("ix_kodi_show_tmdb_id", "kodi_show", ["tmdb_id"])


def downgrade() -> None:
    op.drop_index("ix_kodi_show_tmdb_id", "kodi_show")
    op.drop_index("ix_recently_viewed_viewed_at", "recently_viewed")
    op.drop_table("recently_viewed")
    op.drop_table("tmdb_cache")
