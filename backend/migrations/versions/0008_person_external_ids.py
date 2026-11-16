"""Person external IDs (IMDb, Instagram, Wikidata)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-16

Adds:
  person.imdb_id      — TMDB-sourced IMDb person ID (e.g. "nm0000093")
  person.instagram_id — TMDB-sourced Instagram username
  person.wikidata_id  — TMDB-sourced Wikidata entity ID (e.g. "Q38111")

No existing rows or columns are modified.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("person", sa.Column("imdb_id", sa.String(20), nullable=True))
    op.add_column("person", sa.Column("instagram_id", sa.String(100), nullable=True))
    op.add_column("person", sa.Column("wikidata_id", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("person", "wikidata_id")
    op.drop_column("person", "instagram_id")
    op.drop_column("person", "imdb_id")
