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
