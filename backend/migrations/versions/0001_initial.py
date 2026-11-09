"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # media_item
    op.create_table(
        "media_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_type", sa.Enum("movie", "episode", name="mediatype"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("show_title", sa.Text(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("plot", sa.Text(), nullable=True),
        sa.Column("runtime_s", sa.Integer(), nullable=True),
        sa.Column("genres", sa.Text(), nullable=True),
        sa.Column("imdb_id", sa.String(20), nullable=True),
        sa.Column("tmdb_id", sa.String(20), nullable=True),
        sa.Column("tvdb_id", sa.String(20), nullable=True),
        sa.Column("kodi_file_id", sa.Integer(), nullable=True),
        sa.Column("kodi_media_id", sa.Integer(), nullable=True),
        sa.Column(
            "kodi_state",
            sa.Enum("watched", "unwatched", "in_progress", "removed", name="kodistate"),
            nullable=False,
        ),
        sa.Column("metadata_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_item_kodi_file_id", "media_item", ["kodi_file_id"])
    op.create_index("ix_media_item_imdb_id", "media_item", ["imdb_id"])
    op.create_index("ix_media_item_tmdb_id", "media_item", ["tmdb_id"])
    op.create_index("ix_media_item_tvdb_id", "media_item", ["tvdb_id"])

    # kodi_file_state
    op.create_table(
        "kodi_file_state",
        sa.Column("kodi_file_id", sa.Integer(), nullable=False),
        sa.Column("play_count", sa.Integer(), nullable=True),
        sa.Column("last_played", sa.String(32), nullable=True),
        sa.Column("resume_pos_s", sa.Float(), nullable=True),
        sa.Column("resume_total_s", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("kodi_file_id"),
    )

    # kodi_observation
    op.create_table(
        "kodi_observation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("kodi_file_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "resume_set",
                "resume_moved",
                "resume_cleared",
                "lastplayed_changed",
                "playcount_up",
                "playcount_cleared",
                "file_removed",
                name="observationkind",
            ),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("kodi_last_played", sa.String(32), nullable=True),
        sa.Column("kodi_play_count", sa.Integer(), nullable=True),
        sa.Column("resume_pos_s", sa.Float(), nullable=True),
        sa.Column("resume_total_s", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_item.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_kodi_observation_media_item_id", "kodi_observation", ["media_item_id"]
    )
    op.create_index(
        "ix_kodi_observation_observed_at", "kodi_observation", ["observed_at"]
    )
    op.create_index(
        "ix_kodi_observation_kodi_file_id", "kodi_observation", ["kodi_file_id"]
    )

    # watch_session
    op.create_table(
        "watch_session",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("max_position_s", sa.Float(), nullable=True),
        sa.Column("runtime_s", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "completed", "partial", "ignored", name="sessionstatus"),
            nullable=False,
        ),
        sa.Column(
            "completion_reason",
            sa.Enum(
                "position", "kodi_playcount", "kodi_marked", name="completionreason"
            ),
            nullable=True,
        ),
        sa.Column(
            "source",
            sa.Enum("poll", "backfill", name="sessionsource"),
            nullable=False,
        ),
        sa.Column("device", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_item.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_watch_session_media_item_id", "watch_session", ["media_item_id"]
    )
    op.create_index("ix_watch_session_started_at", "watch_session", ["started_at"])
    op.create_index("ix_watch_session_status", "watch_session", ["status"])

    # sync_state
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kodi_db_name", sa.String(64), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("poll_count", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # settings
    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("sync_state")
    op.drop_index("ix_watch_session_status", table_name="watch_session")
    op.drop_index("ix_watch_session_started_at", table_name="watch_session")
    op.drop_index("ix_watch_session_media_item_id", table_name="watch_session")
    op.drop_table("watch_session")
    op.drop_index("ix_kodi_observation_kodi_file_id", table_name="kodi_observation")
    op.drop_index("ix_kodi_observation_observed_at", table_name="kodi_observation")
    op.drop_index("ix_kodi_observation_media_item_id", table_name="kodi_observation")
    op.drop_table("kodi_observation")
    op.drop_table("kodi_file_state")
    op.drop_index("ix_media_item_tvdb_id", table_name="media_item")
    op.drop_index("ix_media_item_tmdb_id", table_name="media_item")
    op.drop_index("ix_media_item_imdb_id", table_name="media_item")
    op.drop_index("ix_media_item_kodi_file_id", table_name="media_item")
    op.drop_table("media_item")
