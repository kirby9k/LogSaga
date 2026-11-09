# Security Policy

## Kodi Database Safety

LogSAGA is designed with a strict read-only policy toward Kodi's database.
The app **never writes to Kodi's MariaDB database** — not a row, not a setting.
This is enforced at four independent layers:

1. **MariaDB grants** — the app connects with a `SELECT`-only account. MariaDB
   refuses any write attempt at the database level regardless of what the code does.
2. **Read-only session** — every connection immediately issues
   `SET SESSION TRANSACTION READ ONLY`, so MariaDB rejects writes on that connection
   even if grants were misconfigured.
3. **Query allowlist** — all Kodi SQL is defined as fixed constants. A guard wraps
   every `execute()` call and raises an error if the statement does not start with
   `SELECT`, `SHOW`, or `SET SESSION`.
4. **Startup check** — on startup the app runs `SHOW GRANTS FOR CURRENT_USER()` and
   exits with a clear error if the account has anything beyond `SELECT` / `USAGE`.

See `docs/setup.md` for instructions on creating the read-only MariaDB account.

## Reporting a Vulnerability

If you discover a security vulnerability in LogSAGA, please open a
[GitHub issue](https://github.com/kirby9k/logsaga/issues) and mark it
with the **security** label. For sensitive issues, use GitHub's private
vulnerability reporting feature if available.

Please include:
- A description of the issue
- Steps to reproduce
- Potential impact

This is a personal project maintained on a best-effort basis. There is no SLA
for security fixes, but reports will be taken seriously.

## Scope

- The web UI is intended for LAN use only. Do not expose port 8790 to the internet.
- No authentication is implemented. Access controls are your network's responsibility.
- All credentials (database passwords, MQTT passwords) must be supplied via environment
  variables and are never stored in the application code or committed to this repository.
