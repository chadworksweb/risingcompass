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
# Set up daily reading cron (idempotent)
# ------------------------------------------------------------------
CRON_CMD="0 8 * * * curl -s -X POST \"http://localhost:8000/api/admin/agent/classify-live\" -H \"X-Admin-Key: \$(grep RC_ADMIN_KEY $(pwd)/.env | cut -d= -f2)\" > /dev/null 2>&1"

if ! crontab -l 2>/dev/null | grep -q "classify-live"; then
    echo ""
    echo "Setting up daily reading cron (08:00 UTC)..."
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo "Cron installed."
else
    echo "Daily reading cron already exists."
fi

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
echo "2. Test classify-live → email → approve flow"
echo "3. Verify cron: crontab -l"
