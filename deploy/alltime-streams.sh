#!/usr/bin/env bash
# MONTHLY all-time stream-chart refresh (Most-Streamed Songs of All Time --
# Spotify GLOBAL lifetime streams, top 100). Scrapes kworb.net, upserts the 100
# chart rows, and fills calibration from existing songs (cache hits only -- the
# cron makes NO Anthropic calls). Songs not yet calibrated are emailed to Chad
# as an awaiting-lyrics list; he supplies lyrics via calibrate_song.py and they
# fill in on the next run.
#
# This list moves on a multi-month scale, so monthly is the right cadence. Calls
# /api/admin/agent/cron/refresh-alltime-streams with X-Reading-Cron-Key (same
# service token as the daily reading -- reuses the reading-cron lane).
#
# Suggested crontab (le-projects-01), 09:00 UTC on the 1st of each month:
#   0 9 1 * * /root/risingcompass-readings/alltime-streams.sh >> /root/risingcompass-readings/alltime-streams.log 2>&1
#
# Mirrors itunes.sh: on success appends '[date] {response JSON}'; on failure
# appends '[date] FAILED ...' and fires the backup-config alert.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass All-Time Streams"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/refresh-alltime-streams"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# -w writes 'HTTP_STATUS:NNN' on its own line after the body so we can split
# status from body. 600s max-time: the scrape is near-instant, but resolving
# 100 songs against the songs table can add some time.
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

# Success path -- the refresh response always includes an 'updated' field.
if ! echo "$BODY" | grep -q '"updated"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED no-updated-in-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no updated field): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
