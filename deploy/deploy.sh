#!/bin/bash
# Deploy The Rising Compass to a DigitalOcean droplet (le-projects-01)
# Run from /root/rising-compass on the server
#
# Architecture: RC backend runs as its own Docker Compose stack,
# nginx lives in /root/proxy/ (shared reverse proxy for all projects).

set -e

DOMAIN="risingcompass.net"
API_DOMAIN="api.risingcompass.net"

echo "=== The Rising Compass — Deploy ==="

# Pull latest code
git pull origin master

# ------------------------------------------------------------------
# Build and start backend
# ------------------------------------------------------------------
echo ""
echo "Building and starting backend..."
docker compose up -d --build

# ------------------------------------------------------------------
# Daily reading cron — restored 2026-04-26.
#
# Lives at /root/risingcompass-readings/reading.sh and runs at 08:00 UTC.
# Mirrors the backup cron pattern: a one-shot curl container on the
# le-proxy network hits /api/admin/agent/cron/calibrate-live with
# X-Reading-Cron-Key (RC_READING_CRON_KEY in /root/rising-compass/.env).
#
# Not installed by this script — the cron entry is managed alongside the
# other le-projects-01 backup/reading crons on the host crontab. Verify
# with `sudo crontab -l | grep risingcompass-readings`.
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Set up certbot renewal cron (idempotent) — runs from proxy stack
# ------------------------------------------------------------------
RENEW_CMD="0 3 * * * cd /root/proxy && docker compose run --rm certbot renew --quiet && docker compose exec nginx nginx -s reload > /dev/null 2>&1"

if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    echo "Setting up certbot renewal cron..."
    (crontab -l 2>/dev/null; echo "$RENEW_CMD") | crontab -
    echo "Certbot renewal cron installed."
else
    echo "Certbot renewal cron already exists."
fi

echo ""
echo "=== Deploy complete ==="
echo "Frontend: https://$DOMAIN"
echo "API:      https://$API_DOMAIN"
echo "Admin:    https://$API_DOMAIN/api/admin/dashboard"
echo ""
echo "--- Post-deploy checklist ---"
echo "1. curl https://$API_DOMAIN/api/health"
echo "2. Test calibrate-live → email → approve flow"
echo "3. Verify cron: crontab -l"
