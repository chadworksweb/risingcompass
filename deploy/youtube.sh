#!/usr/bin/env bash
# Daily ingestion-discovery refresh (YouTube Trending - USA, top 20) -- runs daily
# after the 08:00 UTC daily reading + iTunes + Shazam, so any overlap with the
# other charts is already calibrated -> free cache hits. Calls
# /api/admin/agent/cron/refresh-chart-snapshot/youtube with X-Reading-Cron-Key
# (same service token as the daily reading).
#
# Backend reads YouTube Music's Trending chart (via ytmusicapi), writes an
# UNPUBLISHED snapshot, creates a draft, auto-calibrates cache hits, and emails
# Chad the list of songs still awaiting lyrics. Chad supplies lyrics manually
# (calibrate_song.py) then clicks Approve in the email. The YouTube slot is an
# ingestion feeder (no public panel), so approval settles the draft; the value is
# the newly calibrated songs entering the Library. The fetch is the only
# automated step.
#
# Mirrors shazam.sh: on success appends '[date] {response JSON}' to youtube.log;
# on failure appends '[date] FAILED ...' and fires the backup-config alert.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass YouTube Trending - USA"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/refresh-chart-snapshot/youtube"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# -w writes 'HTTP_STATUS:NNN' on its own line after the body so we can
# split status from body even when curl --fail isn't used. 600s max-time:
# the chart fetch is quick, but the cache-hit calibration loop can add real time.
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

# Success path -- every refresh response (fresh draft / pinned / already
# published) includes a chart_source field.
if ! echo "$BODY" | grep -q '"chart_source"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED no-chart_source-in-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no chart_source): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
