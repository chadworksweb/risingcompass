#!/usr/bin/env bash
# MONTHLY all-time stream-ALBUMS refresh (Most-Streamed Albums of All Time --
# Spotify GLOBAL lifetime streams, top 100). The streaming-era twin of the RIAA
# best-sellers board. Scrapes kworb.net/spotify/albums.html, upserts the 100
# rows, and auto-links calibration from any already-charged Release. No
# Anthropic calls -- uncharged albums simply render untagged until charged.
#
# Calls /api/admin/agent/cron/refresh-alltime-stream-albums with
# X-Reading-Cron-Key (reuses the reading-cron lane).
#
# Suggested crontab (le-projects-01), 09:10 UTC on the 1st of each month
# (just after the songs board at 09:00):
#   10 9 1 * * /root/risingcompass-readings/alltime-stream-albums.sh >> /root/risingcompass-readings/alltime-stream-albums.log 2>&1
#
# Mirrors alltime-streams.sh.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass All-Time Stream Albums"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/refresh-alltime-stream-albums"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

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

if ! echo "$BODY" | grep -q '"updated"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED no-updated-in-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no updated field): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
