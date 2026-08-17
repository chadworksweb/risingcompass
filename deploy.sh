#!/usr/bin/env bash
# Deploy Rising Compass to production
# Usage: bash deploy.sh [--allow-dirty]
#
#   --allow-dirty   Proceed even when unrelated frontend files have uncommitted
#                   changes. Those files are LEFT UNTOUCHED -- never committed,
#                   never pushed. Without this flag an unrelated dirty frontend
#                   tree ABORTS the deploy (see the 2026-05-28 incident below).
#
# Optional LEIT integration (all fail-safe -- a deploy works fine without it):
#   LEIT_BASE_URL    dashboard base (default https://leit.libraengine.com)
#   LEIT_DEPLOY_KEY  shared key. If set, deploy.sh asks the LEIT dashboard
#                    whether deploy-time auto-commit is enabled, and reports
#                    each run (dirty-tree check + what it swept) up to it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="deploy@138.197.111.66"
REMOTE_DIR="/root/rising-compass"

# Local, untracked deploy config (LEIT_DEPLOY_KEY, optional LEIT_BASE_URL).
# Gitignored -- it carries the shared secret matched on the LEIT dashboard.
if [ -f "$SCRIPT_DIR/.deploy.env" ]; then set -a; . "$SCRIPT_DIR/.deploy.env"; set +a; fi

LEIT_BASE_URL="${LEIT_BASE_URL:-https://leit.libraengine.com}"
LEIT_DEPLOY_KEY="${LEIT_DEPLOY_KEY:-}"

# RC_* globals -- set by the functions below, read by report_deploy_event so a
# single deploy run can be summarised for the LEIT dashboard panel.
RC_TOGGLE_ENABLED="enabled"
RC_ALLOW_DIRTY="false"
RC_PRE_DIRTY=""
RC_SWEPT_PARTIALS=""
RC_SWEPT_SITEMAP="false"
RC_DEPLOY_STATUS="deployed"

# ---------------------------------------------------------------------------
# Auto-commit safety (the 2026-05-28 incident)
# ---------------------------------------------------------------------------
# deploy.sh regenerates the header/footer partials (build_partials.py) before
# every deploy and auto-commits them so the server's `git pull` renders the
# latest output. The old code staged them with `git add -u frontend`, which
# blind-sweeps EVERY modified tracked frontend file. On 2026-05-28 that
# silently committed + pushed + deployed 8 uncommitted monetization frontend
# files to prod.
#
# (It used to regenerate frontend/sitemap.xml here too. That file and its
# generator were DELETED 2026-08-16: prod serves a sitemap index from
# backend/app/routers/sitemap.py, so the static one was never read, and
# regenerating it on every deploy is what made the dead file look like the
# source of truth.)
#
# The fix: stage ONLY the files the script actually regenerates on this
# run (the post-run dirty set MINUS the pre-run dirty set), and ABORT if any
# UNRELATED frontend file is dirty -- so unrelated uncommitted work can never
# be swept again. Auto-commits are made LOCALLY; the single push near the end
# of the script (the same push that carries any hand-made local commits) sends
# them to origin.

# List tracked frontend files with staged or unstaged modifications (sorted,
# de-duped). Untracked files are intentionally excluded: `git add -u` never
# staged them, so they were never at risk of being swept.
frontend_dirty_files() {  # $1 = repo root
    {
        git -C "$1" diff --name-only -- frontend
        git -C "$1" diff --name-only --cached -- frontend
    } | sort -u
}

# Print lines present in the second list but not the first (set difference).
# Empty lines are ignored so an empty pre/post list is handled cleanly.
lines_added() {  # $1 = pre list, $2 = post list (both newline-separated)
    awk 'NR==FNR { if ($0 != "") seen[$0]=1; next } ($0 != "" && !($0 in seen))' \
        <(printf '%s\n' "$1") <(printf '%s\n' "$2")
}

# Regeneration step, wrapped as a function so the staging logic can be
# exercised in isolation (tests stub this); the real deploy runs the script.
run_build_partials()   { python3 "$1/frontend/scripts/build_partials.py"; }

# Regenerate the partials and auto-commit ONLY what changed, refusing to
# touch unrelated dirty frontend files. Commits locally (no push, no ssh) so it
# is safe to dry-run against a throwaway repo. Returns 1 (does NOT exit) on an
# unrelated-dirty abort so the caller can report the aborted run.
#   $1 = repo root
#   $2 = allow_dirty (true|false)
autocommit_regenerated() {
    local repo="$1" allow_dirty="$2"
    local pre post regenerated partials f

    RC_SWEPT_PARTIALS=""
    # Kept (always "false") so the LEIT deploy-event payload shape does not
    # change under the dashboard. The sitemap is no longer a build artifact.
    RC_SWEPT_SITEMAP="false"

    # Snapshot the dirty frontend tree BEFORE regenerating anything. Whatever is
    # dirty now is unrelated uncommitted work -- it must never be auto-committed.
    pre="$(frontend_dirty_files "$repo")"
    RC_PRE_DIRTY="$pre"

    if [ -n "$pre" ]; then
        if [ "$allow_dirty" = true ]; then
            echo "WARNING: --allow-dirty set. These frontend files have uncommitted"
            echo "changes and will be LEFT UNTOUCHED (not committed, not deployed):"
            printf '  %s\n' $pre
            echo ""
        else
            {
                echo "ERROR: refusing to deploy -- unrelated uncommitted frontend changes."
                echo "These files are dirty but are NOT partial output:"
                printf '  %s\n' $pre
                echo ""
                echo "deploy.sh auto-commits the regenerated partials and pushes"
                echo "to prod; sweeping these in alongside them is the 2026-05-28 incident."
                echo "Commit, stash, or revert them -- or re-run with --allow-dirty to"
                echo "deploy and leave them uncommitted."
            } >&2
            return 1
        fi
    fi

    echo "=== Building partials (header/footer) ==="
    # A NONZERO EXIT HERE ABORTS THE DEPLOY. build_partials.py runs the frontend
    # gates (every public page loads /css/main.css; every same-origin .js/.css
    # carries a ?v= cache-bust and is loaded once) and returns 1 on a violation.
    # That return value used to be discarded, so both gates printed to stderr and
    # the deploy shipped anyway -- a gate that does not gate. It still writes the
    # partials it CAN before failing, so a rerun after the fix is clean.
    if ! run_build_partials "$repo"; then
        {
            echo ""
            echo "ERROR: refusing to deploy -- frontend gate violation (above)."
            echo "Fix the listed files, then re-run. Missing ?v= on a script or"
            echo "stylesheet means Cloudflare keeps serving the old copy for up to"
            echo "4h against new markup, which is the 2026-08-12 song.html incident."
        } >&2
        return 1
    fi

    # Whatever became dirty that wasn't dirty before = exactly what the script
    # regenerated on this run. This is the ONLY thing we auto-commit.
    post="$(frontend_dirty_files "$repo")"
    regenerated="$(lines_added "$pre" "$post")"

    if [ -z "$regenerated" ]; then
        echo "partials: up to date, nothing to auto-commit"
        return 0
    fi

    partials="$(printf '%s\n' "$regenerated" | grep -v '^$' || true)"

    if [ -n "$partials" ]; then
        echo "partial content regenerated -- committing:"
        printf '  %s\n' $partials
        while IFS= read -r f; do
            [ -n "$f" ] && git -C "$repo" add -- "$f"
        done <<< "$partials"
        git -C "$repo" commit -m "Rebuild header/footer partials (auto)"
        RC_SWEPT_PARTIALS="$partials"
    fi
}

# ---------------------------------------------------------------------------
# Chart static re-bake (GEO freshness)
# ---------------------------------------------------------------------------
# The all-time chart pages + llms-full.txt carry a baked snapshot of the live
# rankings (schema.org ItemList + server-rendered list, for crawlers). After the
# monthly data cron refreshes the DB, this re-bakes that snapshot from the live
# API so the crawler-visible HTML stays current. FAIL-SOFT: a missing venv or a
# down API is logged and skipped -- a deploy never depends on it. Stages ONLY
# the five known baked files (never a sweep), so it cannot repeat the 2026-05-28
# incident. Produces no commit when the rankings haven't moved (the common case).
BAKED_FILES=(
    frontend/charts/streamed-all-time/index.html
    frontend/charts/most-streamed-albums/index.html
    frontend/charts/best-selling-albums/index.html
    frontend/llms.txt
    frontend/llms-full.txt
)

bake_chart_statics() {  # $1 = repo root
    local repo="$1" py
    py="$repo/backend/.venv/Scripts/python.exe"
    [ -x "$py" ] || py="$repo/backend/.venv/bin/python"
    [ -x "$py" ] || { echo "(no backend venv -- skipping chart re-bake)"; return 0; }
    # Run from backend/ so pydantic-settings finds backend/.env (the bakers
    # import app.config). Output paths resolve independent of CWD.
    ( cd "$repo/backend" && "$py" scripts/bake_chart_ssr.py ) || { echo "(chart bake failed -- skipping)"; return 0; }
    ( cd "$repo/backend" && "$py" scripts/bake_llms.py )      || { echo "(llms bake failed -- skipping)"; return 0; }
    git -C "$repo" add -- "${BAKED_FILES[@]}" 2>/dev/null || true
    if git -C "$repo" diff --cached --quiet -- "${BAKED_FILES[@]}"; then
        echo "chart statics + llms: rankings unchanged, nothing to commit"
    else
        git -C "$repo" commit -m "Re-bake chart SSR + llms (auto)" -- "${BAKED_FILES[@]}"
        echo "chart statics + llms: re-baked from live data + committed"
    fi
}

# ---------------------------------------------------------------------------
# LEIT dashboard integration (optional, fail-safe)
# ---------------------------------------------------------------------------
# Ask the dashboard whether deploy-time auto-commit is currently enabled.
# Prints "enabled" or "disabled". Any failure (no key, dashboard down, bad
# response) defaults to "enabled" -- the local dirty-tree guard is the real
# safety net, so a network hiccup must never change committing behaviour
# silently. Uses python3 (already required for the build scripts) for the HTTP.
fetch_autocommit_toggle() {
    if [ -z "$LEIT_DEPLOY_KEY" ]; then echo "enabled"; return 0; fi
    # Pass config inline so the python child sees it whether or not the caller
    # exported these (the script assigns them as plain shell vars).
    LEIT_BASE_URL="$LEIT_BASE_URL" LEIT_DEPLOY_KEY="$LEIT_DEPLOY_KEY" \
    python3 - <<'PY' 2>/dev/null || echo "enabled"
import os, json, urllib.request
base = os.environ.get("LEIT_BASE_URL", "").rstrip("/")
key = os.environ.get("LEIT_DEPLOY_KEY", "")
req = urllib.request.Request(base + "/api/rc/autocommit-toggle",
                             headers={"X-LEIT-Deploy-Key": key})
try:
    with urllib.request.urlopen(req, timeout=6) as r:
        d = json.loads(r.read().decode())
    print("disabled" if d.get("enabled") is False else "enabled")
except Exception:
    print("enabled")
PY
}

# Report this deploy run to the LEIT dashboard (dirty-tree check + what was
# swept + toggle state + status). Non-fatal: a reporting failure never fails
# the deploy. No-op when LEIT_DEPLOY_KEY is unset.
report_deploy_event() {  # $1 = repo root
    if [ -z "$LEIT_DEPLOY_KEY" ]; then
        echo "(LEIT_DEPLOY_KEY unset -- skipping deploy-event report)"
        return 0
    fi
    local head branch
    head="$(git -C "$1" rev-parse HEAD 2>/dev/null || echo unknown)"
    branch="$(git -C "$1" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    # Pass every field inline so the python child inherits them regardless of
    # export state (these are plain shell globals, not exported).
    LEIT_BASE_URL="$LEIT_BASE_URL" LEIT_DEPLOY_KEY="$LEIT_DEPLOY_KEY" \
    RC_HEAD="$head" RC_BRANCH="$branch" RC_DEPLOY_STATUS="$RC_DEPLOY_STATUS" \
    RC_TOGGLE_ENABLED="$RC_TOGGLE_ENABLED" RC_ALLOW_DIRTY="$RC_ALLOW_DIRTY" \
    RC_PRE_DIRTY="$RC_PRE_DIRTY" RC_SWEPT_PARTIALS="$RC_SWEPT_PARTIALS" \
    RC_SWEPT_SITEMAP="$RC_SWEPT_SITEMAP" \
    python3 - <<'PY' || true
import os, json, urllib.request

def lines(name):
    return [x for x in (os.environ.get(name, "") or "").splitlines() if x.strip()]

payload = {
    "branch": os.environ.get("RC_BRANCH", ""),
    "head": os.environ.get("RC_HEAD", ""),
    "status": os.environ.get("RC_DEPLOY_STATUS", ""),
    "toggle_enabled": os.environ.get("RC_TOGGLE_ENABLED", "") != "disabled",
    "allow_dirty": os.environ.get("RC_ALLOW_DIRTY", "") == "true",
    "dirty_files": lines("RC_PRE_DIRTY"),
    "swept_partials": lines("RC_SWEPT_PARTIALS"),
    "swept_sitemap": os.environ.get("RC_SWEPT_SITEMAP", "") == "true",
}
base = os.environ.get("LEIT_BASE_URL", "").rstrip("/")
key = os.environ.get("LEIT_DEPLOY_KEY", "")
req = urllib.request.Request(base + "/api/rc/deploy-event",
                             data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json",
                                      "X-LEIT-Deploy-Key": key},
                             method="POST")
try:
    with urllib.request.urlopen(req, timeout=6) as r:
        r.read()
    print("deploy event reported to LEIT dashboard")
except Exception as e:
    print("deploy-event report failed (non-fatal): %s" % e)
PY
}

run_deploy() {
    local repo_root allow_dirty=false arg
    repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    for arg in "$@"; do
        case "$arg" in
            --allow-dirty) allow_dirty=true ;;
            -h|--help) echo "Usage: bash deploy.sh [--allow-dirty]"; return 0 ;;
            *) echo "Unknown argument: $arg" >&2
               echo "Usage: bash deploy.sh [--allow-dirty]" >&2
               exit 2 ;;
        esac
    done
    RC_ALLOW_DIRTY="$allow_dirty"

    echo "=== Checking LEIT auto-commit toggle ==="
    RC_TOGGLE_ENABLED="$(fetch_autocommit_toggle)"
    echo "deploy-time auto-commit: $RC_TOGGLE_ENABLED"

    if [ "$RC_TOGGLE_ENABLED" = disabled ]; then
        # Operator has turned auto-commit off in the LEIT dashboard. Do not
        # regenerate or commit anything; they will rebuild + commit manually.
        echo ""
        echo "Auto-commit is DISABLED via the LEIT dashboard -- skipping partial"
        echo "regeneration. If the partials changed, rebuild and commit them"
        echo "manually before deploying."
        RC_PRE_DIRTY="$(frontend_dirty_files "$repo_root")"
        RC_SWEPT_PARTIALS=""
        RC_SWEPT_SITEMAP="false"
        RC_DEPLOY_STATUS="deployed"
    else
        # Regenerate + auto-commit (local only). On an unrelated-dirty abort,
        # report the aborted run so the dashboard reflects it, then stop.
        if ! autocommit_regenerated "$repo_root" "$allow_dirty"; then
            RC_DEPLOY_STATUS="aborted"
            report_deploy_event "$repo_root"
            exit 1
        fi
        RC_DEPLOY_STATUS="deployed"
    fi

    # Re-bake the chart statics + llms from live data (fail-soft, explicit files
    # only). Gated on the same auto-commit toggle as the partials.
    if [ "$RC_TOGGLE_ENABLED" != disabled ]; then
        echo "=== Re-baking chart statics + llms (fail-soft) ==="
        bake_chart_statics "$repo_root" || true
    fi

    echo "=== Pushing local commits to origin ==="
    # Carries the auto-commits above AND any commit you made locally right before
    # running deploy.sh. Without this the server only sees what is already on
    # origin. This is the ONLY push -- the auto-commits ride it.
    git -C "$repo_root" push origin master

    # Report what this deploy did (non-fatal).
    report_deploy_event "$repo_root"

    echo "=== Pulling latest code ==="
    ssh "$SERVER" "sudo bash -c 'cd $REMOTE_DIR && git pull origin master'"

    # Detect what changed in the pull. Use ORIG_HEAD (set by `git pull` to the
    # pre-pull commit) so multi-commit pulls are detected in full. HEAD~1 only
    # inspects the most recent commit, which silently misses changes when a
    # partials auto-commit lands on top of a real change.
    local changed backend_changed=false frontend_changed=false
    changed=$(ssh "$SERVER" "sudo bash -c 'cd $REMOTE_DIR && git diff --name-only ORIG_HEAD HEAD'")

    if echo "$changed" | grep -q "^backend/"; then
        backend_changed=true
    fi
    if echo "$changed" | grep -q "^frontend/\|^deploy/nginx.conf"; then
        frontend_changed=true
    fi

    echo ""
    echo "Backend changed: $backend_changed"
    echo "Frontend changed: $frontend_changed"

    if [ "$backend_changed" = true ]; then
        echo ""
        echo "=== Rebuilding backend ==="
        ssh "$SERVER" "sudo bash -c 'cd $REMOTE_DIR && docker compose up -d --build --no-deps backend'"
        # Restart nginx to pick up new backend container IP
        echo "=== Restarting nginx (new backend IP) ==="
        ssh "$SERVER" "sudo bash -c 'cd /root/proxy && docker compose restart nginx'"
    fi

    if [ "$frontend_changed" = true ]; then
        # Frontend is volume-mounted -- git pull already updated it.
        # Nginx config now lives in /root/proxy/nginx/conf.d/ (not in this repo).
        echo ""
        echo "=== Frontend updated (volume-mounted, no restart needed) ==="
    fi

    if [ "$backend_changed" = false ] && [ "$frontend_changed" = false ]; then
        echo ""
        echo "=== No backend or frontend changes detected ==="
    fi

    # Quick smoke test. The backend runs migrations on boot, so it may not
    # answer for a few seconds after the container starts -- retry before
    # warning so a normal boot delay is not reported as a failure.
    echo ""
    echo "=== Smoke test ==="
    local status="" i
    for i in $(seq 1 10); do
        status=$(curl -s -o /dev/null -w "%{http_code}" https://api.risingcompass.net/api/health)
        [ "$status" = "200" ] && break
        sleep 3
    done
    if [ "$status" = "200" ]; then
        echo "API OK (200)"
    else
        echo "WARNING: API returned $status after retries"
    fi

    echo ""
    echo "=== Deploy complete ==="
}

# Only run the deploy when executed directly. When sourced (e.g. by the staging
# test harness) the functions above are defined but nothing side-effecting runs.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    run_deploy "$@"
fi
