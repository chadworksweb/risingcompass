# Rising Compass

## Structure

- `backend/` — Python FastAPI app (api.risingcompass.net)
- `frontend/` — static HTML/JS (risingcompass.net)

## Deploy

**Server:** le-projects-01 (138.197.111.66), SSH as `deploy`

```bash
cd "C:/Users/chad/Local Sites/rising-compass"
bash deploy.sh
```

Script auto-detects what changed (backend vs frontend):
- **Backend changes:** `git pull` on server → `docker compose up -d --build --no-deps backend` → restart nginx
- **Frontend changes:** `git pull` on server (volume-mounted, no restart needed)
- Runs smoke test against `https://api.risingcompass.net/api/compass/current`

**Requires:** code must be pushed to GitHub first (`git push origin master`), then `deploy.sh` does `git pull` on the server.

## Local Dev

- Backend: `cd backend && .venv\Scripts\uvicorn app.main:app --port 8000`
- Frontend: `cd frontend && python -m http.server 3005`
- Backend does NOT auto-reload — kill and restart uvicorn after code changes

## Database

Single hosted Turso (libSQL) DB. Local + prod connect to the same instance — no
sync, no `db-pull`/`db-push`. Set `DATABASE_URL=libsql://...` and
`TURSO_AUTH_TOKEN=...` in both local `backend/.env` and prod `/root/rising-compass/.env`.

- DB URL: `libsql://rising-compass-crystopaforge.aws-us-east-1.turso.io`
- Backups: Turso point-in-time restore (managed); local `backend/data/*.local-backup-*.db`
  files are pre-Turso archives, kept for safety.
- Migration tool (one-shot): `backend/scripts/sqlite_to_turso.py <sqlite_path> <url> <token>`
- Smoke test: `backend/scripts/test_turso_write.py` (writes + drops a test table)

## Writes to Turso primary

Long-running transactions die on the embedded-replica session (Hrana stream
timeout). Any write path that runs several statements in one transaction
must open a direct libsql connection to the primary and own the whole
transaction itself. See `_write_api_call_log` in `app/main.py`,
`_open_primary_conn` in `app/routers/artists_admin.py`, and the flusher in
`app/services/api_clients.py` for the pattern.

## Admin auth

Multi-user admin login (added 2026-04-25). The single shared `RC_ADMIN_KEY`
header is gone — every admin endpoint authenticates via the
`rc_admin_session` HttpOnly cookie minted by the obscured login URL.

**Login URL:** `https://api.risingcompass.net/rc-admin-{ADMIN_LOGIN_URL_TOKEN}`
(GET serves the form, POST authenticates; any other token returns 404).
Successful login redirects to `/api/admin/dashboard`. Unauthed
`/api/admin/*` GETs return 404; mutating endpoints return 401. Bookmark
the login URL — it isn't linked from anywhere.

**Required env (both local and prod):**
- `ADMIN_LOGIN_URL_TOKEN=<6+ char string>` — the prefix in the login URL
- `RC_BACKUP_KEY=<service token>` — used by the cron at `POST /api/admin/backup`
  with the `X-Backup-Key` header. Falls back to `RC_ADMIN_KEY` during the
  transition; remove the fallback once cron is migrated.
- `RC_READING_CRON_KEY=<service token>` — used by the daily reading cron at
  08:00 UTC against `POST /api/admin/agent/cron/calibrate-live` with the
  `X-Reading-Cron-Key` header. Distinct from `RC_BACKUP_KEY` so cron lanes
  can be rotated independently.
- `RC_LYRICS_SUPPLY_KEY=<service token>` — accepted by
  `POST /api/admin/agent/drafts/{ref}/songs/{id}/lyrics` (lyrics endpoint) and
  `POST /api/admin/agent/drafts/{ref}/songs/{id}/correct` (override endpoint)
  via the `X-Lyrics-Supply-Key` header, in addition to the browser session cookie.
  Terminal scripts that use this:
  - `backend/scripts/calibrate_song.py` — fresh calibration, sends lyrics +
    Claude-Code-supplied calibration object. Server skips every Anthropic
    call. Use this for any song with no prior `compass_songs` row.
  - `backend/scripts/correct_song.py` — override of an already-calibrated
    song. Only mirrors to `compass_songs` if `agent_draft_songs.compass_song_id`
    is already set; do not use for fresh songs.
  - `backend/scripts/supply_lyrics.py` — legacy server-side Anthropic
    calibrator path; do not run from terminal (API boundary lockdown).
  If env-listed in `docker-compose.yml`, must also be added there for the
  container to see it.
- `RC_ADMIN_KEY` is now used **only** to sign one-time HMAC tokens in
  approval emails. Keep it set and stable across deploys.

**Session policy:** 8h sliding window, 24h absolute cap, 5 failed
attempts / 15min per IP+username = 429, 10 consecutive failures = 1h
lockout. Argon2id password hashes.

**Seeding admins:**
```
cd backend
.venv\Scripts\python.exe scripts\seed_admin.py
```
Prompts for username + password (12-char minimum). Re-running with the
same username resets the password and clears the lockout. Pass
`--revoke-sessions` to kill any active sessions for the user.

## Artist admin endpoints

Authed via the admin session cookie (above). Writes go through a direct
libsql connection to the Turso primary (replica streams time out on
multi-statement transactions).

- `POST /api/admin/artists/{slug}/merge-into` — body `{target_slug, notes?}`.
  Atomically rewrites FKs + denormalised `artist` strings, handles
  `UNIQUE(title, artist)` and `UNIQUE(artist_id, title)` collisions, deletes
  source Artist, writes an `artist_admin_events` audit row.
- `POST /api/admin/artists/{slug}/rename` — body `{new_name, new_slug?, notes?}`.
- `POST /api/admin/artists/{slug}/refresh-release-aggregates` — recomputes
  `track_count / calibrated_count / charge_value / rubric_color` on every real
  Release for the artist. Idempotent.
- `GET /api/admin/artists/events` — paginated audit log for merge/rename.
