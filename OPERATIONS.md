# Rising Compass — Operations Runbook

## Architecture

```
Local (your machine)                    Production (DigitalOcean)
├── backend/  (FastAPI)                 ├── Docker: backend (FastAPI + Playwright)
├── frontend/ (static HTML/JS)          ├── Docker: nginx (serves frontend + proxies API)
└── data/rising_compass.db              ├── Docker: certbot (SSL renewal)
                                        └── Volume: db-data (rising_compass.db)

risingcompass.net        → nginx → static frontend files
api.risingcompass.net    → nginx → backend:8000
```

---

## Day-to-Day: What Happens Automatically

- **8:00 UTC daily** — cron triggers `calibrate-live` → agent calibrates today's top songs → creates draft reading → emails you for review
- **You review** — click approve link in email (or reject/edit via admin dashboard)
- **3:00 UTC daily** — certbot checks if SSL certs need renewal

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

# Frontend (separate terminal)
cd "C:\Users\chad\Local Sites\rising-compass\frontend"
python -m http.server 3000
```
- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- API docs: http://localhost:8000/docs

### Pulling fresh data from production
When you want the latest songs, readings, and calibration data locally:
```bash
cd "C:\Users\chad\Local Sites\rising-compass"
bash db-pull.sh
```
This downloads the production DB and saves it as your local DB. Your previous local DB is backed up automatically.

### Working on features (blog, UI, etc.)
Just work locally as normal. The local DB has all the data you need. When you're done:
```bash
git add -A && git commit -m "description"
git push origin main
```
Then deploy (see below).

---

## Backfill & Calibration

**Do this against production** — the training data should live in the canonical DB.

> **Admin auth note (2026-04-25):** the `X-Admin-Key` header is no longer
> accepted. Admin endpoints require an `rc_admin_session` cookie minted by
> the obscured login URL (see CLAUDE.md). Day-to-day work should go through
> the admin dashboard. For ad-hoc curl, log in once to write a cookie jar,
> then reuse it on every subsequent call.

### Step 0 — log in once, save cookie jar
```bash
curl -c rc-cookies.txt -X POST \
  -H "Content-Type: application/json" \
  -d '{"username":"YOUR_USER","password":"YOUR_PASSWORD"}' \
  "https://api.risingcompass.net/rc-admin-YOUR_TOKEN"
```
Replace `YOUR_TOKEN` with the value of `ADMIN_LOGIN_URL_TOKEN` from `.env`.
The cookie jar is good for 8 hours of idle time / 24 hours absolute. All
the recipes below assume `rc-cookies.txt` is in the working directory.

### Backfill a year (e.g., 1970)
```bash
curl -b rc-cookies.txt -X POST \
  "https://api.risingcompass.net/api/admin/agent/backfill/1970?limit=10"
```
This calibrates the top 10 songs of that year. Does NOT auto-approve.

### Calibrate songs after reviewing
```bash
curl -b rc-cookies.txt -X POST \
  -H "Content-Type: application/json" \
  -d '[{"id": 123, "tier": "Elevated", "charge": 50, "summary": "...", "meaning": "...", "emotion": "...", "intent": "..."}]' \
  "https://api.risingcompass.net/api/admin/agent/calibrate"
```

### Delete a song (instrumental, duplicate, etc.)
```bash
curl -b rc-cookies.txt -X DELETE \
  "https://api.risingcompass.net/api/admin/agent/songs/123"
```

### After backfill/calibration, pull DB to local
```bash
bash db-pull.sh
```

---

## Deploying Code Changes

### From your local machine
```bash
cd "C:\Users\chad\Local Sites\rising-compass"
git push origin main
```

### On the server
```bash
ssh root@YOUR_DROPLET_IP
cd /root/rising-compass
bash deploy/deploy.sh
```
This pulls latest code, rebuilds containers, and restarts everything. Your DB (in the Docker volume) is preserved across deploys.

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

### 5. Push local DB to server
From your local machine:
```bash
# First, update db-push.sh with your droplet IP (replace YOUR_DROPLET_IP)
bash db-push.sh
```

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

### Download DB to your machine
```bash
# Option A: use the script
bash db-pull.sh

# Option B: direct curl (requires the cookie jar from "Step 0" above)
curl -b rc-cookies.txt \
  https://api.risingcompass.net/api/admin/db-export \
  -o backend/data/rising_compass.db
```

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/Dockerfile` | Python + Playwright + Chromium |
| `docker-compose.yml` | All services: backend, nginx, certbot |
| `deploy/nginx.conf` | SSL, frontend serving, API proxy |
| `deploy/deploy.sh` | Full deploy script (run on server) |
| `.env` | Production secrets (not in git) |
| `db-pull.sh` | Pull production DB to local |
| `db-push.sh` | Push local DB to production |
