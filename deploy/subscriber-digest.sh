#!/usr/bin/env bash
# Reading-digest send (Build 2b, Phase 2). Sends RC's latest daily reading as a
# short note to confirmed on-site subscribers. Calls
# /api/admin/agent/cron/subscriber-digest with X-Reading-Cron-Key (same service
# token as the daily reading). Cadence is set by the crontab line that invokes
# this -- recommended WEEKLY to start (protect a young list's deliverability),
# dial up to daily later. Run AFTER the 08:00 UTC reading so the digest carries
# the freshest reading. Dedup is by reading date, so a re-run never double-sends.
#
# Mirrors itunes.sh: appends '[date] {response JSON}' to subscriber-digest.log on
# success; on failure appends '[date] FAILED ...' and fires the backup alert.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass Subscriber Digest"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/subscriber-digest"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

RESPONSE=$(docker run --rm --network le-proxy curlimages/curl:8.10.1     -sS --max-time 600 -X POST     -H "X-Reading-Cron-Key: $RC_READING_CRON_KEY"     -w '\nHTTP_STATUS:%{http_code}\n'     "$ENDPOINT" 2>&1)
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

# Success response carries a "status" field (ok / no_reading).
if ! echo "$BODY" | grep -q '"status"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED unexpected-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no status): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
