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

## Artist admin endpoints

All require `X-Admin-Key` header. Writes go through a direct libsql connection
to the Turso primary (replica streams time out on multi-statement transactions).

- `POST /api/admin/artists/{slug}/merge-into` — body `{target_slug, notes?}`.
  Atomically rewrites FKs + denormalised `artist` strings, handles
  `UNIQUE(title, artist)` and `UNIQUE(artist_id, title)` collisions, deletes
  source Artist, writes an `artist_admin_events` audit row.
- `POST /api/admin/artists/{slug}/rename` — body `{new_name, new_slug?, notes?}`.
- `POST /api/admin/artists/{slug}/refresh-release-aggregates` — recomputes
  `track_count / calibrated_count / charge_value / rubric_color` on every real
  Release for the artist. Idempotent.
- `GET /api/admin/artists/events` — paginated audit log for merge/rename.
