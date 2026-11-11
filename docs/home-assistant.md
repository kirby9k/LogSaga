# LogSAGA — Home Assistant Integration

LogSAGA uses **MQTT discovery** to auto-create sensors in Home Assistant without any
YAML configuration on the HA side. No HACS required.

---

## Prerequisites

- Mosquitto (or any MQTT broker) installed in Home Assistant
- The MQTT integration enabled in HA (`Settings → Devices & Services → MQTT`)
- LogSAGA running and able to reach the broker

---

## 1. Configure LogSAGA

Add these variables to your `.env` or Unraid template:

```env
MQTT_HOST=192.168.1.x          # IP of your HA/Mosquitto host
MQTT_PORT=1883                  # default
MQTT_USER=                      # leave blank if no auth
MQTT_PASSWORD=                  # leave blank if no auth
MQTT_DISCOVERY_PREFIX=homeassistant   # default; must match HA's discovery prefix
PUBLIC_BASE_URL=http://192.168.1.x:8790  # your Unraid server IP — used for poster image URLs
```

Restart the container. Within 60 seconds you should see a **LogSAGA** device appear in
`Settings → Devices & Services → MQTT → Devices`.

---

## 2. Entities created

| Entity | Type | State | Key attributes |
|--------|------|-------|----------------|
| `sensor.logsaga_last_watched` | sensor | Last completed title | `show`, `season`, `episode`, `type`, `completed_at`, `plot`, `poster_url`, `fanart_url` |
| `sensor.logsaga_last_started` | sensor | Most recent title (any status) | `status`, `last_activity_at`, `poster_url`, `fanart_url` |
| `sensor.logsaga_recent` | sensor | Total session count | `sessions` (list of last 10: title, show, status, last_activity_at) |
| `sensor.logsaga_in_progress` | sensor | Count of items with resume point | `items` (list: title, show) |
| `sensor.logsaga_watched_today` | sensor | Minutes watched today | — |
| `sensor.logsaga_watched_this_week` | sensor | Minutes watched this week | — |
| `binary_sensor.logsaga_sync_ok` | binary sensor | ON / OFF | — |

All entities are grouped under a single **LogSAGA** device. State updates every 60 seconds.

---

## 3. Dashboard YAML

Paste this into a new dashboard view via the raw YAML editor
(`Edit Dashboard → ⋮ → Raw configuration editor`).

Replace `http://YOUR_SERVER:8790` with your actual LogSAGA URL if you haven't set
`PUBLIC_BASE_URL` — otherwise poster images are served automatically.

```yaml
title: LogSAGA
views:
  - title: Watch History
    path: logsaga
    icon: mdi:television-play
    cards:

      # ── Last Watched ──────────────────────────────────────────────────────
      - type: markdown
        title: Last Watched
        content: >
          {% set title = states('sensor.logsaga_last_watched') %}
          {% set show  = state_attr('sensor.logsaga_last_watched', 'show') %}
          {% set type  = state_attr('sensor.logsaga_last_watched', 'type') %}
          {% set at    = state_attr('sensor.logsaga_last_watched', 'completed_at') %}
          {% set plot  = state_attr('sensor.logsaga_last_watched', 'plot') %}
          {% set poster = state_attr('sensor.logsaga_last_watched', 'poster_url') %}
          <table style="border:0"><tr>
          {% if poster %}
          <td style="vertical-align:top;padding-right:14px">
          <img src="{{ poster }}" style="height:160px;border-radius:6px;display:block">
          </td>
          {% endif %}
          <td style="vertical-align:top">
          <b>{{ title }}</b>
          {% if show and type == 'episode' %}<br>{{ show }}{% endif %}
          {% if at %}<br><small>{{ as_datetime(at) | as_local | as_timestamp | timestamp_custom('%-d %b %Y, %-I:%M %p') }}</small>{% endif %}
          {% if plot %}<br><br><small style="color:var(--secondary-text-color)">{{ plot | truncate(250) }}</small>{% endif %}
          </td>
          </tr></table>

      # ── Stats row ─────────────────────────────────────────────────────────
      - type: horizontal-stack
        cards:
          - type: entity
            entity: sensor.logsaga_watched_today
            name: Today
            icon: mdi:calendar-today
          - type: entity
            entity: sensor.logsaga_watched_this_week
            name: This Week
            icon: mdi:calendar-week
          - type: entity
            entity: sensor.logsaga_in_progress
            name: In Progress
            icon: mdi:play-pause
          - type: entity
            entity: binary_sensor.logsaga_sync_ok
            name: Sync OK
            icon: mdi:sync

      # ── Recently Watched ──────────────────────────────────────────────────
      - type: markdown
        title: Recently Watched
        content: >
          {% set sessions = state_attr('sensor.logsaga_recent', 'sessions') %}
          {% if sessions %}
          {% for s in sessions %}
          **{{ s.title }}**{% if s.show %} · {{ s.show }}{% endif %}
          <small>· {{ s.status }}</small>
          {% endfor %}
          {% else %}
          No recent sessions.
          {% endif %}

      # ── Continue Watching ─────────────────────────────────────────────────
      - type: markdown
        title: Continue Watching
        content: >
          {% set items = state_attr('sensor.logsaga_in_progress', 'items') %}
          {% if items %}
          {% for i in items %}
          - **{{ i.title }}**{% if i.show %} · {{ i.show }}{% endif %}
          {% endfor %}
          {% else %}
          Nothing in progress.
          {% endif %}
```

---

## 4. Automation examples

### Notify when a new watch session completes

```yaml
alias: LogSAGA — notify on watched
trigger:
  - platform: state
    entity_id: sensor.logsaga_last_watched
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "Just watched"
      message: "{{ states('sensor.logsaga_last_watched') }}"
```

### Send a weekly watch-time summary

```yaml
alias: LogSAGA — weekly summary
trigger:
  - platform: time
    at: "20:00:00"
  - platform: template
    value_template: "{{ now().weekday() == 6 }}"   # Sunday
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "This week's watching"
      message: >
        {{ states('sensor.logsaga_watched_this_week') }} minutes watched.
        Last: {{ states('sensor.logsaga_last_watched') }}.
```

---

## 5. Troubleshooting

| Symptom | Check |
|---------|-------|
| No LogSAGA device in HA | Is `MQTT_HOST` set? Check container logs for `MQTT discovery published` |
| Entities unavailable (grey) | MQTT broker unreachable; LogSAGA shows `MQTT error` in logs |
| Poster images broken | Set `PUBLIC_BASE_URL` to your server's LAN IP/port so HA can reach them |
| State stuck / not updating | Updates every 60 s; check `binary_sensor.logsaga_sync_ok` is ON |
| Wrong discovery prefix | `MQTT_DISCOVERY_PREFIX` must match HA's `discovery_prefix` (default: `homeassistant`) |
