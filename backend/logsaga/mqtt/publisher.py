"""
MQTT Home Assistant discovery publisher for LogSAGA.

Publishes MQTT discovery configs and state updates so Home Assistant
auto-creates sensors without any YAML configuration.

Entities published:
  sensor.logsaga_last_watched    — most recent completed session
  sensor.logsaga_last_started    — most recent session of any status
  sensor.logsaga_recent          — count + last 10 sessions as attrs
  sensor.logsaga_in_progress     — count of in_progress items
  sensor.logsaga_watched_today   — completed sessions today (count + minutes)
  sensor.logsaga_watched_this_week — completed sessions this week
  binary_sensor.logsaga_sync_ok  — True when last sync was recent

All is a no-op if cfg.mqtt_enabled is False.

Phase 3: discovery configs + state publishing are fully implemented.
Phase 6: dashboard YAML and HA integration testing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import Engine, create_engine, func
from sqlalchemy.orm import sessionmaker

from logsaga.config import Settings
from logsaga.models import (
    KodiState,
    MediaItem,
    SessionStatus,
    SyncState,
    WatchSession,
)

logger = logging.getLogger(__name__)

_DEVICE = {
    "identifiers": ["logsaga"],
    "name": "LogSAGA",
    "model": "LogSAGA",
    "manufacturer": "LogSAGA",
}

_AVAIL_TOPIC = "logsaga/status"
_STATE_BASE = "logsaga/sensor"
_BINARY_BASE = "logsaga/binary_sensor"


def _disc_prefix(cfg: Settings) -> str:
    return cfg.mqtt_discovery_prefix


def _sensor_topics(cfg: Settings, name: str) -> dict[str, str]:
    return {
        "config": f"{_disc_prefix(cfg)}/sensor/{name}/config",
        "state": f"{_STATE_BASE}/{name}/state",
        "avail": _AVAIL_TOPIC,
    }


def _binary_topics(cfg: Settings, name: str) -> dict[str, str]:
    return {
        "config": f"{_disc_prefix(cfg)}/binary_sensor/{name}/config",
        "state": f"{_BINARY_BASE}/{name}/state",
        "avail": _AVAIL_TOPIC,
    }


def _sensor_config(
    cfg: Settings,
    name: str,
    friendly_name: str,
    unit: Optional[str] = None,
    icon: str = "mdi:television-play",
    value_template: str = "{{ value_json.state }}",
    json_attrs: bool = False,
) -> dict[str, Any]:
    t = _sensor_topics(cfg, name)
    cfg_dict: dict[str, Any] = {
        "name": friendly_name,
        "unique_id": name,
        "state_topic": t["state"],
        "availability_topic": t["avail"],
        "payload_available": "online",
        "payload_not_available": "offline",
        "value_template": value_template,
        "icon": icon,
        "device": _DEVICE,
    }
    if unit:
        cfg_dict["unit_of_measurement"] = unit
    if json_attrs:
        cfg_dict["json_attributes_topic"] = t["state"]
    return cfg_dict


def _binary_config(
    cfg: Settings,
    name: str,
    friendly_name: str,
    icon: str = "mdi:check-circle",
    value_template: str = "{{ value_json.state }}",
) -> dict[str, Any]:
    t = _binary_topics(cfg, name)
    return {
        "name": friendly_name,
        "unique_id": name,
        "state_topic": t["state"],
        "availability_topic": t["avail"],
        "payload_available": "online",
        "payload_not_available": "offline",
        "payload_on": "ON",
        "payload_off": "OFF",
        "value_template": value_template,
        "icon": icon,
        "device": _DEVICE,
    }


async def publish_discovery(client: Any, cfg: Settings) -> None:
    """
    Send HA MQTT discovery configs for all LogSAGA entities.

    Call once on startup (or on reconnect). Messages are retained.
    """
    entities = [
        (
            _sensor_topics(cfg, "logsaga_last_watched")["config"],
            _sensor_config(
                cfg, "logsaga_last_watched", "LogSAGA Last Watched",
                icon="mdi:movie-check",
                json_attrs=True,
                value_template="{{ value_json.state }}",
            ),
        ),
        (
            _sensor_topics(cfg, "logsaga_last_started")["config"],
            _sensor_config(
                cfg, "logsaga_last_started", "LogSAGA Last Started",
                icon="mdi:play-circle",
                json_attrs=True,
            ),
        ),
        (
            _sensor_topics(cfg, "logsaga_recent")["config"],
            _sensor_config(
                cfg, "logsaga_recent", "LogSAGA Recent",
                unit="items",
                icon="mdi:history",
                json_attrs=True,
            ),
        ),
        (
            _sensor_topics(cfg, "logsaga_in_progress")["config"],
            _sensor_config(
                cfg, "logsaga_in_progress", "LogSAGA In Progress",
                unit="items",
                icon="mdi:play-pause",
                json_attrs=True,
            ),
        ),
        (
            _sensor_topics(cfg, "logsaga_watched_today")["config"],
            _sensor_config(
                cfg, "logsaga_watched_today", "LogSAGA Watched Today",
                unit="min",
                icon="mdi:calendar-today",
            ),
        ),
        (
            _sensor_topics(cfg, "logsaga_watched_this_week")["config"],
            _sensor_config(
                cfg, "logsaga_watched_this_week", "LogSAGA Watched This Week",
                unit="min",
                icon="mdi:calendar-week",
            ),
        ),
        (
            _binary_topics(cfg, "logsaga_sync_ok")["config"],
            _binary_config(
                cfg, "logsaga_sync_ok", "LogSAGA Sync OK",
                icon="mdi:sync",
            ),
        ),
    ]

    for topic, payload in entities:
        await client.publish(topic, json.dumps(payload), retain=True)

    # Availability: online
    await client.publish(_AVAIL_TOPIC, "online", retain=True)
    logger.info("MQTT discovery published for %d entities", len(entities))


def _build_state(engine: Engine) -> dict[str, Any]:
    """
    Read current state from DB and return a dict of entity states.

    Called from the background task; runs synchronously in a thread.
    """
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    with SessionLocal() as db:
        # Last watched (most recent completed session)
        last_watched_q = (
            db.query(WatchSession, MediaItem)
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(WatchSession.status == SessionStatus.completed)
            .order_by(WatchSession.completed_at.desc())
            .first()
        )
        last_watched: dict[str, Any] = {"state": ""}
        if last_watched_q:
            ws, mi = last_watched_q
            label = mi.title
            if mi.show_title:
                ep = f"S{mi.season:02d}E{mi.episode:02d}" if mi.season and mi.episode else ""
                label = f"{mi.show_title} {ep} \"{mi.title}\"".strip()
            last_watched = {
                "state": label,
                "type": mi.media_type,
                "show": mi.show_title,
                "season": mi.season,
                "episode": mi.episode,
                "completed_at": ws.completed_at.isoformat() if ws.completed_at else None,
            }

        # Last started (most recent session any status)
        last_started_q = (
            db.query(WatchSession, MediaItem)
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(WatchSession.status != SessionStatus.ignored)
            .order_by(WatchSession.last_activity_at.desc())
            .first()
        )
        last_started: dict[str, Any] = {"state": ""}
        if last_started_q:
            ws, mi = last_started_q
            label = mi.title
            if mi.show_title:
                ep = f"S{mi.season:02d}E{mi.episode:02d}" if mi.season and mi.episode else ""
                label = f"{mi.show_title} {ep} \"{mi.title}\"".strip()
            last_started = {
                "state": label,
                "status": ws.status,
                "last_activity_at": ws.last_activity_at.isoformat(),
            }

        # Recent (last 10 non-ignored sessions)
        recent_sessions = (
            db.query(WatchSession, MediaItem)
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(WatchSession.status != SessionStatus.ignored)
            .order_by(WatchSession.last_activity_at.desc())
            .limit(10)
            .all()
        )
        recent_count = (
            db.query(WatchSession)
            .filter(WatchSession.status != SessionStatus.ignored)
            .count()
        )
        recent_items = []
        for ws, mi in recent_sessions:
            recent_items.append({
                "title": mi.title,
                "show": mi.show_title,
                "status": ws.status,
                "last_activity_at": ws.last_activity_at.isoformat(),
            })

        # In-progress
        in_progress_count = (
            db.query(MediaItem)
            .filter(MediaItem.kodi_state == KodiState.in_progress)
            .count()
        )
        in_progress_items = (
            db.query(MediaItem)
            .filter(MediaItem.kodi_state == KodiState.in_progress)
            .order_by(MediaItem.updated_at.desc())
            .limit(10)
            .all()
        )

        # Watch time today
        today_minutes = (
            db.query(func.sum(WatchSession.runtime_s))
            .filter(
                WatchSession.status == SessionStatus.completed,
                WatchSession.completed_at >= today_start,
                WatchSession.runtime_s.isnot(None),
            )
            .scalar()
        ) or 0
        today_minutes = int(today_minutes / 60)

        # Watch time this week
        week_minutes = (
            db.query(func.sum(WatchSession.runtime_s))
            .filter(
                WatchSession.status == SessionStatus.completed,
                WatchSession.completed_at >= week_start,
                WatchSession.runtime_s.isnot(None),
            )
            .scalar()
        ) or 0
        week_minutes = int(week_minutes / 60)

        # Sync OK
        sync = db.get(SyncState, 1)
        sync_ok = False
        if sync and sync.last_success_at:
            age_s = (now - sync.last_success_at).total_seconds()
            sync_ok = age_s < 600  # ok if polled within 10 minutes

    return {
        "last_watched": last_watched,
        "last_started": last_started,
        "recent": {"state": str(recent_count), "sessions": recent_items},
        "in_progress": {
            "state": str(in_progress_count),
            "items": [
                {"title": mi.title, "show": mi.show_title}
                for mi in in_progress_items
            ],
        },
        "watched_today": {"state": str(today_minutes)},
        "watched_this_week": {"state": str(week_minutes)},
        "sync_ok": {"state": "ON" if sync_ok else "OFF"},
    }


async def publish_state(client: Any, cfg: Settings, engine: Engine) -> None:
    """
    Publish current state to all MQTT state topics.

    Runs DB reads in the event loop directly (SQLite is fast enough for this).
    """
    import asyncio

    state = await asyncio.to_thread(_build_state, engine)
    t_base = _STATE_BASE
    b_base = _BINARY_BASE
    name_map = {
        "last_watched": f"{t_base}/logsaga_last_watched/state",
        "last_started": f"{t_base}/logsaga_last_started/state",
        "recent": f"{t_base}/logsaga_recent/state",
        "in_progress": f"{t_base}/logsaga_in_progress/state",
        "watched_today": f"{t_base}/logsaga_watched_today/state",
        "watched_this_week": f"{t_base}/logsaga_watched_this_week/state",
        "sync_ok": f"{b_base}/logsaga_sync_ok/state",
    }
    for key, topic in name_map.items():
        payload = state.get(key, {})
        await client.publish(topic, json.dumps(payload), retain=True)

    logger.debug("MQTT state published")


async def run_mqtt_publisher(cfg: Settings, engine: Engine) -> None:
    """
    Lifespan background task: connect to MQTT, publish discovery, then
    publish state every 60 seconds (or immediately after a poll produces changes).

    Reconnects automatically on drop. No-op if cfg.mqtt_enabled is False.
    """
    import asyncio

    if not cfg.mqtt_enabled:
        return

    try:
        import aiomqtt
    except ImportError:
        logger.warning("aiomqtt not installed — MQTT publishing disabled")
        return

    backoff = 5
    while True:
        try:
            async with aiomqtt.Client(
                hostname=cfg.mqtt_host,
                port=cfg.mqtt_port,
                username=cfg.mqtt_user or None,
                password=cfg.mqtt_password or None,
            ) as client:
                backoff = 5  # reset on success
                await publish_discovery(client, cfg)
                while True:
                    await publish_state(client, cfg, engine)
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("MQTT error (%s) — reconnecting in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)
