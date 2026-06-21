# Rising Compass — Operations Runbook

> **STALE DB/LOCAL-DEV SECTIONS — the repo `CLAUDE.md` is the authoritative current ops doc.** This runbook predates the Postgres migration (2026-05-24). The DB is no longer a local/volume SQLite file (`data/rising_compass.db`, `db-data`) -- it is **DigitalOcean Managed Postgres**, shared by local + prod; local dev reaches it by SSH tunnel (`ssh -L 25061:<db-host>:25061 deploy@...`, point `DATABASE_URL` at `127.0.0.1:25061`), and there is NO `db-pull.sh` / `db-push.sh` of a local DB anymore. So the Architecture diagram, Local Dev, "Pulling fresh data", "Push local DB", First-Time Setup DB steps, and the Key Files DB rows below are SUPERSEDED -- read `CLAUDE.md` (Database + Local Dev) for the current workflow. Also: terminal calibration is ZERO-Anthropic (Claude Code is the model; `calibrate_song.py` + LEC golden rubric), per the "Terminal calibration" section of `CLAUDE.md`.

## Architecture

```
Local (your machine)                    Production (le-projects-01, DigitalOcean)
├── backend/  (FastAPI)                 ├── Docker: backend (FastAPI + Playwright)
├── frontend/ (static HTML/JS)          ├── Docker: nginx (serves frontend + proxies API)
└── (no local DB file)                  └── Docker: certbot (SSL renewal)

DATABASE: DigitalOcean Managed Postgres (NYC3), shared by local + prod through
          DO's PgBouncer pool (port 25061). No SQLite file, no db-data volume.

risingcompass.net        → nginx → static frontend + first-party /api/* + Site Admin
api.risingcompass.net    → nginx → backend:8000 (machine API + API Monitor)
```

---

## Day-to-Day: What Happens Automatically

- **8:00 UTC daily** — cron at `/root/risingcompass-readings/reading.sh` hits `POST /api/admin/agent/cron/calibrate-live` with `X-Reading-Cron-Key` (`RC_READING_CRON_KEY`) → agent calibrates today's top songs → creates draft reading → emails you for review. Service-token authed, not admin-session — distinct from the human admin login.
- **You review** — click approve link in email (or reject/edit via admin dashboard)
- **9:00 UTC daily** — cron at `/root/risingcompass-readings/itunes.sh` (reference copy `deploy/itunes.sh`) hits `POST /api/admin/agent/cron/refresh-chart-snapshot/itunes` with `X-Reading-Cron-Key` (same `RC_READING_CRON_KEY`) → fetches the iTunes Download Chart - USA (Apple RSS JSON, no Playwright), writes an UNPUBLISHED snapshot + draft, auto-calibrates cache hits, emails you the songs awaiting lyrics. Runs after the daily reading (08:00) so the Top 50 overlap is already calibrated. **You supply lyrics manually (`calibrate_song.py`) then click Approve — approval is what publishes the chart to the homepage panel.** Nothing public until then. Crontab line: `0 9 * * * /root/risingcompass-readings/itunes.sh >> /root/risingcompass-readings/itunes.log 2>&1`. (History: this slot was the weekly Spotify Viral 50 until Spotify retired Viral 50 in May 2026; reskinned to the iTunes chart and moved weekly->daily. The homepage secondary panel itself now shows Spotify New Music Friday as of 2026-06-19; the iTunes chart still feeds its own page + the Calendar.)
- **3:00 UTC daily** — certbot checks if SSL certs need renewal
- **4:17 UTC Monday** — cron refreshes the MaxMind GeoLite2-Country DB used by
  the cookie consent bar's geo-aware default (`/api/geo-country`). Reads
  `MAXMIND_LICENSE_KEY` from `.env`, writes `/root/geoip/GeoLite2-Country.mmdb`,
  then needs a backend restart to load the new DB.

  Crontab line (deploy user; sources `.env` so the key stays only there):
  ```
  17 4 * * 1 bash -c 'set -a; . /root/rising-compass/.env; set +a; bash /root/rising-compass/deploy/refresh_geoip.sh && cd /root/rising-compass && docker compose restart backend' >> /var/log/geoip-refresh.log 2>&1
  ```
  Requires `MAXMIND_LICENSE_KEY=...` in `/root/rising-compass/.env` (free MaxMind
  account). Until that key + the first `mmdb` exist, `/api/geo-country` returns
  null and the consent bar safely behaves as opt-in everywhere.

You don't need to do anything unless you get an email.

### Email delivery
Emails (draft notifications, misread receipts) are sent via **Resend API** over HTTPS — not SMTP. This avoids DigitalOcean's outbound SMTP port blocking.
- Free tier: 3,000 emails/month (we send ~1/day)
- Domain verified: `risingcompass.net` in Resend dashboard
- From address: `compass@risingcompass.net`
- Config: `RESEND_API_KEY` and `APPROVAL_EMAIL` in `.env`

---

## Local Development

### Starting locally
```bash
# Backend
cd "C:\Users\chad\Local Sites\rising-compass\backend"
.venv\Scripts\uvicorn app.main:app --port 8000

# Frontend (separate terminal) -- use dev_server.py, NOT python -m http.server
cd "C:\Users\chad\Local Sites\rising-compass\frontend"
python scripts/dev_server.py --port 3005
```
- Frontend: http://localhost:3005 (dev_server.py reverse-proxies `/api/*` + `/rc-admin-*` to :8000, mirroring prod's single origin; plain `http.server` 404s every API call)
- Backend: http://localhost:8000  /  API docs: http://localhost:8000/docs

### Database (no local DB file)
The DB is **DigitalOcean Managed Postgres**, shared by local + prod -- there is NO local SQLite file and NO `db-pull.sh` / `db-push.sh`. Local dev reaches the DB by tunneling through the droplet (the DB firewall trusts only the droplet):
```bash
ssh -L 25061:<db-host>:25061 deploy@138.197.111.66
# then point backend/.env DATABASE_URL at 127.0.0.1:25061 (the PgBouncer pool)
```
Because the DB is shared, there is nothing to "pull" -- local already reads the canonical data. DSN forms are in CLAUDE.md (Database).

### Working on features (blog, UI, etc.)
Work locally; the shared DB has all the data. When done:
```bash
git add -A && git commit -m "description"
git push origin master
```
Then deploy (see below). Iterate locally; deploy on request.

---

## Backfill & Calibration

**Terminal = Claude Code IS the model: ZERO Anthropic calls.** Claude Code supplies the calibration and it is written to the shared Postgres DB; the server makes no model call. The full procedures live in the SOPs -- do NOT hand-roll calibration here:
- Historical Year-End backfill: `Dropbox/Libra Engine/Rising Compass/plans and docs/agent/risingcompass-backfill-process.md`
- Daily reading (Daily Listens): `.../RISING-COMPASS-DAILY-LISTENS-SOP.md`
- Other chart readings (YouTube, iTunes, etc.): their `RISING-COMPASS-*-SOP.md`

Canonical rubric = the LEC golden (`Local Sites/libra-engine-compass/backend/app/rubric/lec-golden-<latest>/`), verified against LEC prod `GET /api/rubric`. RC no longer carries a `tenets/` rubric copy -- the in-process rubric apparatus was removed 2026-06-21, so the LEC golden is the only rubric source. Write tools: `backend/scripts/calibrate_song.py` (draft readings; HARD-REQUIRES `--listener-effects-prose-file`) and a supplied-result `_store_calibration(..., year=YEAR, allow_prose_generation=False)` for backfill. **NEVER `calibrate_song()` / `supply_lyrics` (server Anthropic calibrator).** The old curl `/api/admin/agent/backfill/{year}` + `/calibrate` (tier/charge/meaning/emotion/intent) recipes are DEAD -- that endpoint was removed and the schema is v3 components now.

### Tag an instrumental (do NOT delete)
Instrumentals (no real lyrics) stay on the chart as historical record. Setting `instrumental = true`
is the whole procedure: the frontend greys the dot (rubric_color is never rendered) and excludes the
song from the compass reading. Leave `rubric_color`/`charge_value` as-is; never calibrate it.
```bash
ssh deploy@138.197.111.66 "cd /root/rising-compass && sudo docker compose exec -T backend python3 -c \"
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
db.execute(text('UPDATE songs SET instrumental = true WHERE id = :i'), {'i': 123})
db.commit(); db.close()
\""
```

### Delete a song (true duplicate only)
Only true duplicates get deleted. Instrumentals are tagged (above), not removed. Use the admin dashboard DB explorer, or the admin API with an `rc_admin_session` cookie (the `X-Admin-Key` header is no longer accepted). No DB to "pull" afterward -- the DB is shared.

---

## Deploying Code Changes

From your local machine -- push to GitHub first, then run the deploy script (it SSHes to the droplet, git-pulls, rebuilds the backend, restarts nginx):
```bash
cd "C:\Users\chad\Local Sites\rising-compass"
git push origin master
bash deploy.sh
```
`deploy.sh` auto-detects backend vs frontend changes (backend: rebuild the `backend` container + restart nginx; frontend: git pull only, volume-mounted, no restart) and smoke-tests `https://api.risingcompass.net/api/compass/current`. The Managed Postgres DB is external, so it is untouched by deploys. (SSH user is `deploy@138.197.111.66`; project files are root-owned, so in-container calls use `sudo docker compose`.)

---

## First-Time Server Setup

### 1. Create DigitalOcean droplet
- Ubuntu 24.04, Docker pre-installed (or install Docker)
- Recommended: 2GB RAM / 1 vCPU ($12/mo) — Playwright needs memory

### 2. Point DNS (in your domain registrar)
```
A record: risingcompass.net      → DROPLET_IP
A record: api.risingcompass.net  → DROPLET_IP
```
Wait for DNS propagation (check: `dig risingcompass.net`)

### 3. Clone repo on server
```bash
ssh root@DROPLET_IP
git clone https://github.com/YOUR_REPO/rising-compass.git /root/rising-compass
cd /root/rising-compass
```

### 4. Create .env
```bash
cp backend/.env.example .env
nano .env   # fill in all real values
```

### 5. Database
No DB push -- the app uses **DigitalOcean Managed Postgres** (external). Set `DATABASE_URL` (PgBouncer pool, port 25061) and `BACKUP_DATABASE_URL` (direct, port 25060) in `.env`; `Base.metadata.create_all()` builds a fresh schema on first boot. See CLAUDE.md (Database).

### 6. Deploy
```bash
ssh root@DROPLET_IP
cd /root/rising-compass
bash deploy/deploy.sh
```
First deploy will: obtain SSL certs → start all containers → set up crons.

### 7. Verify
```bash
curl https://api.risingcompass.net/api/health
# → {"status":"ok"}

# Open in browser
# https://risingcompass.net → frontend loads
```

---

## Troubleshooting

### Check container logs
```bash
ssh root@DROPLET_IP
cd /root/rising-compass
docker compose logs backend --tail 50
docker compose logs nginx --tail 50
```

### Restart everything
```bash
docker compose down && docker compose up -d
```

### SSL certificate issues
```bash
# Check cert status
docker compose run --rm certbot certificates

# Force renewal
docker compose run --rm certbot renew --force-renewal
docker compose exec nginx nginx -s reload
```

### Database backup (manual)
The backup endpoint uses a service token (`X-Backup-Key`), not the admin
session — distinct keys for distinct callers. The nightly cron at 04:45 UTC
already runs this.

```bash
curl -X POST "https://api.risingcompass.net/api/admin/backup" \
  -H "X-Backup-Key: $(grep '^RC_BACKUP_KEY=' backend/.env | cut -d= -f2-)"
```
Backups land in `s3://crystopa-forge-backup1/le-projects-01/risingcompass/`
with 30-day retention.

### Inspect / query the DB
The DB is Managed Postgres, not a downloadable file. Query it via the admin dashboard DB explorer, or open an SSH tunnel (`ssh -L 25061:<db-host>:25061 deploy@138.197.111.66`) and connect a local `psql` / SQLAlchemy at `127.0.0.1:25061`. For a dump, use `BACKUP_DATABASE_URL` (direct DSN, port 25060) with `pg_dump` -- the nightly S3 backup cron already does this (above).

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/Dockerfile` | Python + Playwright + Chromium |
| `docker-compose.yml` | All services: backend, nginx, certbot |
| `deploy/nginx.conf` | SSL, frontend serving, API proxy |
| `deploy.sh` | Deploy script (run LOCALLY; SSHes to the droplet, git-pulls, rebuilds backend, restarts nginx) |
| `.env` | Secrets incl. `DATABASE_URL` / `BACKUP_DATABASE_URL` (not in git) |
| `backend/scripts/calibrate_song.py` | Terminal calibration write (zero Anthropic) |
