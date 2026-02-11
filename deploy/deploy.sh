#!/bin/bash
# Deploy The Rising Compass to a DigitalOcean droplet
# Run from the project root on the server

set -e

echo "=== The Rising Compass — Deploy ==="

# Pull latest code
git pull origin main

# Build and start containers
docker compose up -d --build

# Run extraction and seed (first deploy only — safe to re-run)
echo "Running data extraction and seed..."
docker compose exec backend python scripts/extract_songs.py
docker compose exec backend python scripts/extract_dtmf.py
docker compose exec backend python scripts/seed_db.py

echo ""
echo "=== Deploy complete ==="
echo "API: http://api.risingcompass.com"
echo "Admin: http://api.risingcompass.com/api/admin/dashboard"
echo ""
echo "Reminder: Set RC_ADMIN_KEY in .env before going live!"
