#!/usr/bin/env bash
# Deploy Rising Compass to production
# Usage: bash deploy.sh
set -euo pipefail

SERVER="deploy@138.197.111.66"
REMOTE_DIR="/root/rising-compass"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Building partials (header/footer) ==="
# Replaces content between <!-- INCLUDE:name --> ... <!-- /INCLUDE:name -->
# markers with the partial in frontend/partials/. Idempotent. Auto-commits
# any drift so the server's git pull renders the latest header/footer.
python3 "$REPO_ROOT/frontend/scripts/build_partials.py"
if ! git -C "$REPO_ROOT" diff --quiet -- frontend; then
    echo "partial content updated — committing + pushing"
    git -C "$REPO_ROOT" add -u frontend
    git -C "$REPO_ROOT" commit -m "Rebuild header/footer partials (auto)"
    git -C "$REPO_ROOT" push origin master
fi

echo "=== Regenerating sitemap ==="
# Top-level pages only; script scans frontend/ and rewrites sitemap.xml
# if anything changed. Auto-commit + push so the server's `git pull`
# picks up the fresh sitemap without any manual step.
python3 "$REPO_ROOT/frontend/scripts/generate-sitemap.py"
if ! git -C "$REPO_ROOT" diff --quiet -- frontend/sitemap.xml; then
    echo "sitemap.xml changed — committing + pushing"
    git -C "$REPO_ROOT" add frontend/sitemap.xml
    git -C "$REPO_ROOT" commit -m "Regenerate sitemap.xml (auto)"
    git -C "$REPO_ROOT" push origin master
fi

echo "=== Pulling latest code ==="
ssh "$SERVER" "sudo bash -c 'cd $REMOTE_DIR && git pull origin master'"

# Detect what changed in the pull
CHANGED=$(ssh "$SERVER" "sudo bash -c 'cd $REMOTE_DIR && git diff --name-only HEAD~1 HEAD'")

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
    ssh "$SERVER" "sudo bash -c 'cd $REMOTE_DIR && docker compose up -d --build --no-deps backend'"
    # Restart nginx to pick up new backend container IP
    echo "=== Restarting nginx (new backend IP) ==="
    ssh "$SERVER" "sudo bash -c 'cd /root/proxy && docker compose restart nginx'"
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
STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://api.risingcompass.net/api/health)
if [ "$STATUS" = "200" ]; then
    echo "API OK (200)"
else
    echo "WARNING: API returned $STATUS"
fi

echo ""
echo "=== Deploy complete ==="
