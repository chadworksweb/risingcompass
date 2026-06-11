#!/usr/bin/env bash
# Re-bake the all-time chart static pages + llms files from the LIVE API, then
# commit + deploy. Run after the monthly chart refresh (the data crons fire on
# the 1st at 09:00/09:10 UTC); this reflects the fresh rankings into the
# crawler-visible static HTML (schema.org ItemList + server-rendered list) and
# into /llms-full.txt. No-ops cleanly when the data hasn't moved.
set -e
cd "$(dirname "$0")/.."   # repo root

PY=backend/.venv/Scripts/python.exe
[ -x "$PY" ] || PY=backend/.venv/bin/python   # linux venv fallback

"$PY" backend/scripts/bake_chart_ssr.py
"$PY" backend/scripts/bake_llms.py

git add frontend/charts/streamed-all-time/index.html \
        frontend/charts/most-streamed-albums/index.html \
        frontend/charts/best-selling-albums/index.html \
        frontend/llms.txt frontend/llms-full.txt

if git diff --cached --quiet; then
    echo "No changes after re-bake; rankings unchanged, nothing to deploy."
    exit 0
fi

git commit -m "Re-bake chart SSR + llms from monthly refresh"
git push origin master
bash deploy.sh
