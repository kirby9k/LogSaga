"""Add technical metadata columns to media_item

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05

Adds 8 nullable columns:
  release_date  — YYYY-MM-DD air/release date from Kodi
  director      — director(s) string (movies only)
  video_codec   — strVideoCodec from streamdetails
  video_width   — iVideoWidth from streamdetails
  video_height  — iVideoHeight from streamdetails
  hdr_type      — strHdrType from streamdetails (Kodi v21+)
  audio_codec   — strAudioCodec from streamdetails
  audio_channels — iAudioChannels from streamdetails

Existing rows get NULL; use `logsaga enrich-metadata` to backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("media_item") as batch_op:
        batch_op.add_column(sa.Column("release_date", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("director", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("video_codec", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("video_width", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("video_height", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("hdr_type", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("audio_codec", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("audio_channels", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_item") as batch_op:
        batch_op.drop_column("audio_channels")
        batch_op.drop_column("audio_codec")
        batch_op.drop_column("hdr_type")
        batch_op.drop_column("video_height")
        batch_op.drop_column("video_width")
        batch_op.drop_column("video_codec")
        batch_op.drop_column("director")
        batch_op.drop_column("release_date")
