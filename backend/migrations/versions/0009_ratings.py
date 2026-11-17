"""Ratings from Kodi — IMDB, TMDB, Trakt, userrating

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-17

Adds 7 nullable columns to both media_item and kodi_show:
  rating_imdb, votes_imdb       — IMDB community rating + vote count
  rating_tmdb, votes_tmdb       — TMDB community rating (themoviedb + tmdb combined)
  rating_trakt, votes_trakt     — Trakt community rating
  userrating                    — Personal 0–10 rating set in Kodi UI

All columns are nullable and have no default.  Existing rows get NULL values.
Ratings are populated by library sync and the poll loop on next run.

No existing columns are modified.  Downgrade drops all 14 added columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # media_item
    op.add_column("media_item", sa.Column("rating_imdb",  sa.Float(),   nullable=True))
    op.add_column("media_item", sa.Column("votes_imdb",   sa.Integer(), nullable=True))
    op.add_column("media_item", sa.Column("rating_tmdb",  sa.Float(),   nullable=True))
    op.add_column("media_item", sa.Column("votes_tmdb",   sa.Integer(), nullable=True))
    op.add_column("media_item", sa.Column("rating_trakt", sa.Float(),   nullable=True))
    op.add_column("media_item", sa.Column("votes_trakt",  sa.Integer(), nullable=True))
    op.add_column("media_item", sa.Column("userrating",   sa.Integer(), nullable=True))

    # kodi_show
    op.add_column("kodi_show", sa.Column("rating_imdb",  sa.Float(),   nullable=True))
    op.add_column("kodi_show", sa.Column("votes_imdb",   sa.Integer(), nullable=True))
    op.add_column("kodi_show", sa.Column("rating_tmdb",  sa.Float(),   nullable=True))
    op.add_column("kodi_show", sa.Column("votes_tmdb",   sa.Integer(), nullable=True))
    op.add_column("kodi_show", sa.Column("rating_trakt", sa.Float(),   nullable=True))
    op.add_column("kodi_show", sa.Column("votes_trakt",  sa.Integer(), nullable=True))
    op.add_column("kodi_show", sa.Column("userrating",   sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("kodi_show",  "userrating")
    op.drop_column("kodi_show",  "votes_trakt")
    op.drop_column("kodi_show",  "rating_trakt")
    op.drop_column("kodi_show",  "votes_tmdb")
    op.drop_column("kodi_show",  "rating_tmdb")
    op.drop_column("kodi_show",  "votes_imdb")
    op.drop_column("kodi_show",  "rating_imdb")

    op.drop_column("media_item", "userrating")
    op.drop_column("media_item", "votes_trakt")
    op.drop_column("media_item", "rating_trakt")
    op.drop_column("media_item", "votes_tmdb")
    op.drop_column("media_item", "rating_tmdb")
    op.drop_column("media_item", "votes_imdb")
    op.drop_column("media_item", "rating_imdb")
