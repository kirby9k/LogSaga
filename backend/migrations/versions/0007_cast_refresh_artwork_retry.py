"""Cast refresh tracking and artwork retry backoff

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15

Adds:
  artwork.consecutive_failures  — reset to 0 on success; tracks retry budget
  artwork.retry_after           — computed backoff window; NULL when stopped or not failed
  kodi_show.cast_fetched_at     — when cast was last successfully populated
  kodi_show.cast_refresh_attempted_at — last attempt (success or failure); 12-hour cooldown
  media_item.cast_fetched_at    — same, for movies (episodes use kodi_show)
  media_item.cast_refresh_attempted_at
  person.bio_refresh_attempted_at — 12-hour cooldown for background bio refreshes

No existing rows or columns are modified.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # artwork — retry tracking
    op.add_column("artwork", sa.Column(
        "consecutive_failures", sa.Integer, nullable=False, server_default="0"
    ))
    op.add_column("artwork", sa.Column("retry_after", sa.DateTime, nullable=True))

    # kodi_show — cast refresh tracking
    op.add_column("kodi_show", sa.Column("cast_fetched_at", sa.DateTime, nullable=True))
    op.add_column("kodi_show", sa.Column("cast_refresh_attempted_at", sa.DateTime, nullable=True))

    # media_item — cast refresh tracking (movies only; episodes use kodi_show)
    op.add_column("media_item", sa.Column("cast_fetched_at", sa.DateTime, nullable=True))
    op.add_column("media_item", sa.Column("cast_refresh_attempted_at", sa.DateTime, nullable=True))

    # person — bio refresh cooldown tracking
    op.add_column("person", sa.Column("bio_refresh_attempted_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("person", "bio_refresh_attempted_at")
    op.drop_column("media_item", "cast_refresh_attempted_at")
    op.drop_column("media_item", "cast_fetched_at")
    op.drop_column("kodi_show", "cast_refresh_attempted_at")
    op.drop_column("kodi_show", "cast_fetched_at")
    op.drop_column("artwork", "retry_after")
    op.drop_column("artwork", "consecutive_failures")
