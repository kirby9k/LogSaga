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
| `sensor.logsaga_now_playing` | sensor | Currently playing title, or `""` | `show`, `type`, `percent`, `poster_url`, `fanart_url` |
| `sensor.logsaga_last_watched` | sensor | Last completed title | `show`, `season`, `episode`, `type`, `completed_at`, `plot`, `poster_url`, `fanart_url` |
| `sensor.logsaga_last_started` | sensor | Most recent title (any status) | `status`, `last_activity_at`, `poster_url`, `fanart_url` |
| `sensor.logsaga_recent` | sensor | Total session count | `sessions` (list of last 10: title, show, status, last_activity_at, **poster_url**) |
| `sensor.logsaga_in_progress` | sensor | Count of items with resume point | `items` (list: title, show, **poster_url**, **percent**, **resume_s**, **runtime_s**) |
| `sensor.logsaga_watched_today` | sensor | Minutes watched today | **`count`**, **`items`** (list: title, show, type, completed_at, runtime_min, poster_url) |
| `sensor.logsaga_watched_this_week` | sensor | Minutes watched this week | **`count`**, **`items`** (list: title, show, type, completed_at, runtime_min, poster_url, capped at 50) |
| `binary_sensor.logsaga_sync_ok` | binary sensor | ON / OFF | — |

All entities are grouped under a single **LogSAGA** device. State updates every 60 seconds.

> **`logsaga_now_playing`** depends on the crash/resume addon (Device 1) which writes
> a bookmark every 20 s during playback. Device 2 (no addon) will not appear as
> "now playing" mid-episode — this is a known limitation of Kodi's DB write behaviour.

---

## 3. Dashboard YAML

Paste this into a new dashboard view via the raw YAML editor
(`Edit Dashboard → ⋮ → Raw configuration editor`).

Replace `YOUR_SERVER_IP` with your Unraid server's LAN IP. If `PUBLIC_BASE_URL` is set
in the container, poster images resolve automatically.

```yaml
title: LogSAGA
views:
  - title: Watch History
    path: logsaga
    icon: mdi:television-play
    cards:

      # ── Now Playing — only visible when something is actively being watched ──
      - type: conditional
        conditions:
          - condition: state
            entity: sensor.logsaga_now_playing
            state_not: ""
        card:
          type: markdown
          content: >
            {% set title  = states('sensor.logsaga_now_playing') %}
            {% set show   = state_attr('sensor.logsaga_now_playing', 'show') %}
            {% set pct    = state_attr('sensor.logsaga_now_playing', 'percent') %}
            {% set poster = state_attr('sensor.logsaga_now_playing', 'poster_url') %}
            {% set fanart = state_attr('sensor.logsaga_now_playing', 'fanart_url') %}
            {% if fanart %}
            <div style="background-image:url('{{ fanart }}');background-size:cover;background-position:center top;border-radius:8px;overflow:hidden">
            <div style="background:linear-gradient(to right,rgba(0,0,0,0.88) 55%,rgba(0,0,0,0.25));padding:14px">
            {% endif %}
            <table style="border:0;width:100%"><tr>
            {% if poster %}<td style="width:70px;vertical-align:top;padding-right:12px"><img src="{{ poster }}" style="height:105px;border-radius:5px;display:block;box-shadow:0 3px 12px rgba(0,0,0,0.5)"></td>{% endif %}
            <td style="vertical-align:top">
            <div style="font-size:0.68em;text-transform:uppercase;letter-spacing:0.1em;color:{% if fanart %}rgba(255,255,255,0.6){% else %}var(--secondary-text-color){% endif %};margin-bottom:4px">▶ Now Playing</div>
            <div style="font-size:1.05em;font-weight:700;{% if fanart %}color:#fff;{% endif %}">{{ title }}</div>
            {% if show %}<div style="{% if fanart %}color:rgba(255,255,255,0.75);{% else %}color:var(--secondary-text-color);{% endif %}margin-top:3px">{{ show }}</div>{% endif %}
            {% if pct %}<div style="margin-top:8px">
            <div style="background:rgba(255,255,255,0.2);border-radius:4px;height:4px;width:100%">
            <div style="background:#4ade80;border-radius:4px;height:4px;width:{{ pct }}%"></div>
            </div>
            <div style="font-size:0.75em;{% if fanart %}color:rgba(255,255,255,0.55);{% else %}color:var(--secondary-text-color);{% endif %}margin-top:3px">{{ pct }}%</div>
            </div>{% endif %}
            </td>
            </tr></table>
            {% if fanart %}</div></div>{% endif %}

      # ── Last Watched — fanart backdrop + poster ───────────────────────────────
      - type: markdown
        content: >
          {% set title   = states('sensor.logsaga_last_watched') %}
          {% set show    = state_attr('sensor.logsaga_last_watched', 'show') %}
          {% set type    = state_attr('sensor.logsaga_last_watched', 'type') %}
          {% set season  = state_attr('sensor.logsaga_last_watched', 'season') %}
          {% set episode = state_attr('sensor.logsaga_last_watched', 'episode') %}
          {% set at      = state_attr('sensor.logsaga_last_watched', 'completed_at') %}
          {% set plot    = state_attr('sensor.logsaga_last_watched', 'plot') %}
          {% set poster  = state_attr('sensor.logsaga_last_watched', 'poster_url') %}
          {% set fanart  = state_attr('sensor.logsaga_last_watched', 'fanart_url') %}
          {% if season and episode %}{% set ep = 'S' ~ season|string|zfill(2) ~ 'E' ~ episode|string|zfill(2) %}{% else %}{% set ep = '' %}{% endif %}
          <div style="font-size:0.7em;text-transform:uppercase;letter-spacing:0.1em;color:var(--secondary-text-color);margin-bottom:8px">Last Watched</div>
          {% if fanart %}<div style="background-image:url('{{ fanart }}');background-size:cover;background-position:center top;border-radius:8px;overflow:hidden"><div style="background:linear-gradient(to right,rgba(0,0,0,0.88) 55%,rgba(0,0,0,0.25));padding:14px">{% endif %}
          <table style="border:0;width:100%"><tr>
          {% if poster %}<td style="width:90px;vertical-align:top;padding-right:12px"><img src="{{ poster }}" style="height:135px;border-radius:5px;display:block;box-shadow:0 3px 12px rgba(0,0,0,0.5)"></td>{% endif %}
          <td style="vertical-align:top">
          <div style="font-size:1.05em;font-weight:700;{% if fanart %}color:#fff;{% endif %}">{{ title }}</div>
          {% if show and type == 'episode' %}<div style="margin-top:3px;{% if fanart %}color:rgba(255,255,255,0.75);{% else %}color:var(--secondary-text-color);{% endif %}">{{ show }}{% if ep %} · <span style="font-family:monospace">{{ ep }}</span>{% endif %}</div>{% endif %}
          {% if at %}<div style="font-size:0.8em;margin-top:5px;{% if fanart %}color:rgba(255,255,255,0.55);{% else %}color:var(--secondary-text-color);{% endif %}">{{ as_datetime(at) | as_local | as_timestamp | timestamp_custom('%-d %b %Y · %-I:%M %p') }}</div>{% endif %}
          {% if plot %}<div style="font-size:0.8em;margin-top:8px;line-height:1.45;{% if fanart %}color:rgba(255,255,255,0.65);{% else %}color:var(--secondary-text-color);{% endif %}">{{ plot | truncate(220) }}</div>{% endif %}
          </td>
          </tr></table>
          {% if fanart %}</div></div>{% endif %}

      # ── Stats row ─────────────────────────────────────────────────────────────
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

      # ── Watched Today ─────────────────────────────────────────────────────────
      - type: conditional
        conditions:
          - condition: numeric_state
            entity: sensor.logsaga_watched_today
            above: 0
        card:
          type: markdown
          title: Watched Today
          content: >
            {% set items = state_attr('sensor.logsaga_watched_today', 'items') %}
            {% set total = states('sensor.logsaga_watched_today') %}
            {% if items %}
            {% for i in items %}
            <div style="display:flex;align-items:center;gap:10px;padding:5px 0;{% if not loop.last %}border-bottom:1px solid var(--divider-color);{% endif %}">
            {% if i.poster_url %}<img src="{{ i.poster_url }}" style="height:54px;border-radius:4px;flex-shrink:0">{% endif %}
            <div style="flex:1;min-width:0">
            <div style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ i.title }}</div>
            {% if i.show %}<div style="font-size:0.78em;color:var(--secondary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ i.show }}</div>{% endif %}
            <div style="font-size:0.75em;color:var(--secondary-text-color)">{{ i.runtime_min }} min{% if i.completed_at %} · {{ as_datetime(i.completed_at) | as_local | as_timestamp | timestamp_custom('%-I:%M %p') }}{% endif %}</div>
            </div>
            </div>
            {% endfor %}
            <div style="margin-top:8px;font-size:0.8em;color:var(--secondary-text-color);text-align:right">{{ total }} min total</div>
            {% else %}
            <span style="color:var(--secondary-text-color)">Nothing watched today yet.</span>
            {% endif %}

      # ── Recently Watched + Continue Watching ──────────────────────────────────
      - type: horizontal-stack
        cards:

          - type: markdown
            title: Recently Watched
            content: >
              {% set sessions = state_attr('sensor.logsaga_recent', 'sessions') %}
              {% if sessions %}
              {% for s in sessions %}
              <div style="display:flex;align-items:center;gap:8px;padding:5px 0;{% if not loop.last %}border-bottom:1px solid var(--divider-color);{% endif %}">
              {% if s.poster_url %}<img src="{{ s.poster_url }}" style="height:48px;border-radius:3px;flex-shrink:0">{% endif %}
              <div style="flex:1;min-width:0">
              <div style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.title }}</div>
              {% if s.show %}<div style="font-size:0.75em;color:var(--secondary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.show }}</div>{% endif %}
              </div>
              <div style="text-align:right;flex-shrink:0">
              {% if s.status == 'completed' %}<div style="color:#4ade80;font-size:0.72em;font-weight:700">✓</div>
              {% elif s.status == 'partial' %}<div style="color:#fb923c;font-size:0.72em">···</div>{% endif %}
              {% if s.last_activity_at %}<div style="color:var(--secondary-text-color);font-size:0.72em">{{ as_datetime(s.last_activity_at) | as_local | as_timestamp | timestamp_custom('%-d %b') }}</div>{% endif %}
              </div>
              </div>
              {% endfor %}
              {% else %}
              <span style="color:var(--secondary-text-color)">No sessions yet.</span>
              {% endif %}

          - type: markdown
            title: Continue Watching
            content: >
              {% set items = state_attr('sensor.logsaga_in_progress', 'items') %}
              {% if items %}
              {% for i in items %}
              <div style="display:flex;align-items:center;gap:8px;padding:5px 0;{% if not loop.last %}border-bottom:1px solid var(--divider-color);{% endif %}">
              {% if i.poster_url %}<img src="{{ i.poster_url }}" style="height:54px;border-radius:3px;flex-shrink:0">{% endif %}
              <div style="flex:1;min-width:0">
              <div style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ i.title }}</div>
              {% if i.show %}<div style="font-size:0.75em;color:var(--secondary-text-color)">{{ i.show }}</div>{% endif %}
              {% if i.percent %}
              <div style="margin-top:4px">
              <div style="background:var(--divider-color);border-radius:4px;height:3px">
              <div style="background:#4ade80;border-radius:4px;height:3px;width:{{ i.percent }}%"></div>
              </div>
              <div style="font-size:0.72em;color:var(--secondary-text-color);margin-top:2px">{{ i.percent }}%</div>
              </div>
              {% endif %}
              </div>
              </div>
              {% endfor %}
              {% else %}
              <span style="color:var(--secondary-text-color)">Nothing in progress.</span>
              {% endif %}

      # ── Full LogSAGA web UI embedded ──────────────────────────────────────────
      - type: iframe
        url: http://YOUR_SERVER_IP:8790
        aspect_ratio: 75%
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
