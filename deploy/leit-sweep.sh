#!/usr/bin/env bash
# Daily LEIT clutter sweep -- scans recent public Lyrical Charger additions for
# clutter (gibberish, unknown non-artists, content that belongs on the
# Creative/Curio Charger) and queues each finding in clutter_audits for human
# audit. Flag-only: it never changes the live site. Calls
# /api/admin/agent/cron/leit-sweep with X-Reading-Cron-Key (same service token
# as the daily reading -- the sweep runs in the same nightly cron lane).
#
# Schedule it AFTER the daily reading + iTunes refresh so the day's new
# submissions are settled. Suggested crontab line on le-projects-01:
#   30 16 * * *  /root/risingcompass-readings/leit-sweep.sh >> /root/risingcompass-readings/leit-sweep.log 2>&1
#
# On flagged findings the backend emails Chad a digest (alert key
# leit_sweep_digest) linking to the Audit Queue. Mirrors itunes.sh logging.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass LEIT Clutter Sweep"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/leit-sweep"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# 600s max-time: the batched Opus classification can add real time on a large
# backlog (bootstrap run); a normal daily sweep is small.
RESPONSE=$(docker run --rm --network le-proxy curlimages/curl:8.10.1 \
    -sS --max-time 600 -X POST \
    -H "X-Reading-Cron-Key: $RC_READING_CRON_KEY" \
    -w '\nHTTP_STATUS:%{http_code}\n' \
    "$ENDPOINT" 2>&1)
CURL_EXIT=$?

HTTP_STATUS=$(echo "$RESPONSE" | grep -oE 'HTTP_STATUS:[0-9]+' | tail -1 | cut -d: -f2)
BODY=$(echo "$RESPONSE" | sed '/^HTTP_STATUS:/d')

if [ $CURL_EXIT -ne 0 ]; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED curl exit=$CURL_EXIT body=$SNIPPET"
    alert_failure "$PROJECT" "curl failed (exit $CURL_EXIT): $BODY"
    exit 1
fi

if [ "$HTTP_STATUS" != "200" ]; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED status=$HTTP_STATUS body=$SNIPPET"
    alert_failure "$PROJECT" "HTTP $HTTP_STATUS: $BODY"
    exit 1
fi

# Success path -- the summary always carries a "scanned" field.
if ! echo "$BODY" | grep -q '"scanned"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED no-scanned-in-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no scanned): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
