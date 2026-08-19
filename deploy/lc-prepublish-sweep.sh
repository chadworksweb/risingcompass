#!/usr/bin/env bash
# Lyrical Charger prepublish sweep -- publishes readings the reader neither
# accepted nor contested. Silence means accepted: most readers click nothing,
# so without this the Library would only ever receive the readings someone
# bothered to confirm. Calls /api/admin/agent/cron/lc-prepublish-sweep with
# X-Reading-Cron-Key (same service token as the daily reading).
#
# CADENCE IS MINUTES, NOT NIGHTS, and this is the one line to get right. Every
# other script in this lane is daily; HOLD_TTL is 30 minutes, so a nightly run
# would leave a whole day of readings unpublished, invisible to the song pages
# and uncounted by the public run cap. Crontab line on le-projects-01:
#   */10 * * * *  /root/risingcompass-readings/lc-prepublish-sweep.sh >> /root/risingcompass-readings/lc-prepublish-sweep.log 2>&1
#
# A no-op run is the NORMAL result and returns 200 with zeroes -- it stays a
# no-op entirely while lc_prepublish.enabled is off, because nothing is held.
# Because it runs every 10 minutes, only real failures alert; a quiet run is
# logged and nothing else.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass LC Prepublish Sweep"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/lc-prepublish-sweep"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# 120s max-time: publishing is DB work only, no model call. The batch is capped
# at 200 rows inside sweep_expired, so a backlog drains over consecutive runs
# rather than stretching any single one.
RESPONSE=$(docker run --rm --network le-proxy curlimages/curl:8.10.1 \
    -sS --max-time 120 -X POST \
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

# A failed publish inside the batch is logged by the backend and counted here.
# It does not fail the run: the remaining rows still published, and the failed
# ones stay held for the next pass.
if ! echo "$BODY" | grep -q '"failed": *0'; then
    echo "[$(date)] PARTIAL $BODY"
    alert_failure "$PROJECT" "Sweep reported publish failures: $BODY"
    exit 0
fi

echo "[$(date)] $BODY"
