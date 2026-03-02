#!/usr/bin/env bash
# Deploy Rising Compass to production
# Usage: bash deploy.sh
set -euo pipefail

SERVER="root@138.197.111.66"
REMOTE_DIR="/root/rising-compass"

echo "=== Pulling latest code ==="
ssh "$SERVER" "cd $REMOTE_DIR && git pull origin master"

# Detect what changed in the pull
CHANGED=$(ssh "$SERVER" "cd $REMOTE_DIR && git diff --name-only HEAD~1 HEAD")

BACKEND_CHANGED=false
FRONTEND_CHANGED=false

if echo "$CHANGED" | grep -q "^backend/"; then
    BACKEND_CHANGED=true
fi
if echo "$CHANGED" | grep -q "^frontend/\|^deploy/nginx.conf"; then
    FRONTEND_CHANGED=true
fi

echo ""
echo "Backend changed: $BACKEND_CHANGED"
echo "Frontend changed: $FRONTEND_CHANGED"

if [ "$BACKEND_CHANGED" = true ]; then
    echo ""
    echo "=== Rebuilding backend ==="
    ssh "$SERVER" "cd $REMOTE_DIR && docker compose up -d --build --no-deps backend"
    # Restart nginx to pick up new backend container IP
    echo "=== Restarting nginx (new backend IP) ==="
    ssh "$SERVER" "cd /root/proxy && docker compose restart nginx"
fi

if [ "$FRONTEND_CHANGED" = true ]; then
    # Frontend is volume-mounted — git pull already updated it.
    # Nginx config now lives in /root/proxy/nginx/conf.d/ (not in this repo).
    echo ""
    echo "=== Frontend updated (volume-mounted, no restart needed) ==="
fi

if [ "$BACKEND_CHANGED" = false ] && [ "$FRONTEND_CHANGED" = false ]; then
    echo ""
    echo "=== No backend or frontend changes detected ==="
fi

# Quick smoke test
echo ""
echo "=== Smoke test ==="
STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://api.risingcompass.net/api/compass/current)
if [ "$STATUS" = "200" ]; then
    echo "API OK (200)"
else
    echo "WARNING: API returned $STATUS"
fi

echo ""
echo "=== Deploy complete ==="
