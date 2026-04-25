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
# Daily reading cron — REMOVED 2026-04-25.
#
# The previous block installed a curl that hit /api/admin/agent/calibrate-live
# with X-Admin-Key. After the multi-user admin auth migration, that header
# is no longer accepted, and the endpoint requires a session cookie that a
# headless cron can't carry. The cron had also been broken for some time
# (hardcoded stale key value baked in at install, hit localhost:8000 which
# doesn't reach the dockerized backend). Removed entirely; if automated
# daily readings come back they should authenticate via a dedicated service
# token like RC_BACKUP_KEY does for the backup cron.
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
