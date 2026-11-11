# LogSAGA — Homepage Dashboard

[Homepage](https://gethomepage.dev) can display LogSAGA as a service card with live
stats pulled from the `/api/health` and `/api/recent` endpoints.

Replace `YOUR_SERVER_IP` with your Unraid server's LAN IP throughout.

---

## Basic service card

No widget — just a link with description. Add to your `services.yaml`:

```yaml
- Media:
    - LogSAGA:
        icon: mdi-television-play
        href: http://YOUR_SERVER_IP:8790
        description: Kodi watch history
```

---

## Service card with live stats (custom API widget)

Shows sync status and total session count. Requires Homepage ≥ 0.9.0.

```yaml
- Media:
    - LogSAGA:
        icon: mdi-television-play
        href: http://YOUR_SERVER_IP:8790
        description: Kodi watch history
        widget:
          type: customapi
          url: http://YOUR_SERVER_IP:8790/api/health
          refreshInterval: 60000        # ms — matches LogSAGA's 60 s MQTT publish interval
          mappings:
            - field: status
              label: Sync
              format: text
            - field: observation_count
              label: Observations
              format: number
            - field: poll_count
              label: Polls
              format: number
```

---

## Service card showing last watched title

Pulls from `/api/recent?limit=1` to display the most recently played item.

```yaml
- Media:
    - LogSAGA:
        icon: mdi-television-play
        href: http://YOUR_SERVER_IP:8790
        description: Kodi watch history
        widget:
          type: customapi
          url: http://YOUR_SERVER_IP:8790/api/recent?limit=1
          refreshInterval: 30000
          mappings:
            - field: total
              label: Sessions
              format: number
            - field: items.0.item.title
              label: Last Watched
              format: text
```

> **Note:** `items.0.item.title` uses Homepage's dot-notation for nested JSON fields
> (array index 0, then `item`, then `title`). Supported in Homepage ≥ 0.9.

---

## Docker socket card (alternative)

If you prefer the simpler Docker-socket style card (shows container status only):

```yaml
- Media:
    - LogSAGA:
        icon: mdi-television-play
        href: http://YOUR_SERVER_IP:8790
        description: Kodi watch history
        server: my-unraid        # your Docker socket server name in Homepage config
        container: logsaga
```
