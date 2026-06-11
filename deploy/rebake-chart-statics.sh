#!/usr/bin/env bash
# Re-bake the all-time chart static pages + llms files from the LIVE API, then
# commit + deploy. Run after the monthly chart refresh (the data crons fire on
# the 1st at 09:00/09:10 UTC); this reflects the fresh rankings into the
# crawler-visible static HTML (schema.org ItemList + server-rendered list) and
# into /llms-full.txt. No-ops cleanly when the data hasn't moved.
#
# (deploy.sh also runs this same re-bake automatically on every deploy; this
# script is for refreshing on demand without a code change.)
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PY="$ROOT/backend/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/backend/.venv/bin/python"   # linux venv fallback

# Run from backend/ so pydantic-settings finds backend/.env.
( cd "$ROOT/backend" && "$PY" scripts/bake_chart_ssr.py )
( cd "$ROOT/backend" && "$PY" scripts/bake_llms.py )

git -C "$ROOT" add -- \
    frontend/charts/streamed-all-time/index.html \
    frontend/charts/most-streamed-albums/index.html \
    frontend/charts/best-selling-albums/index.html \
    frontend/llms.txt frontend/llms-full.txt

if git -C "$ROOT" diff --cached --quiet; then
    echo "No changes after re-bake; rankings unchanged, nothing to deploy."
    exit 0
fi

git -C "$ROOT" commit -m "Re-bake chart SSR + llms from monthly refresh"
git -C "$ROOT" push origin master
bash "$ROOT/deploy.sh"
