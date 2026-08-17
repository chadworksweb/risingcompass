#!/usr/bin/env bash
# Nightly cover-art sweep -- resolves release-group MBIDs for releases that have
# none (which is what unlocks their cover art), then song-level cover art for
# songs never checked, then warms the Cover Art Archive cache for anything new.
# Calls /api/admin/agent/cron/cover-art with X-Reading-Cron-Key (same service
# token as the daily reading -- this runs in the same nightly cron lane).
#
# Before this existed nothing was automatic: song cover art was a hand-run
# chunked script, and release MBIDs were only ever a side effect of a
# MusicBrainz catalogue resolve or an Album Charger run, so a release created by
# hand for a terminal album read could never show art at all.
#
# EVERY RUN IS BOUNDED. MusicBrainz is 1 req/sec and 503s freely under load, so
# each pass takes a slice (15 releases + 60 songs by default) and leaves the rest
# for tomorrow. A backlog drains over several nights instead of hammering MB
# once. Nothing here is on the calibration hot path.
#
# Schedule it AFTER the readings so the day's new songs are already calibrated.
# Suggested crontab line on le-projects-01:
#   45 9 * * *  /root/risingcompass-readings/cover-art.sh >> /root/risingcompass-readings/cover-art.log 2>&1
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass Cover Art Sweep"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/cover-art"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# 900s max-time: a full slice is ~15 release lookups plus ~60 song resolves at
# roughly 2s each, and MusicBrainz backs off under load.
RESPONSE=$(docker run --rm --network le-proxy curlimages/curl:8.10.1 \
    -sS --max-time 900 -X POST \
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

# Success path -- the summary always carries a "releases" key.
if ! echo "$BODY" | grep -q '"releases"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED no-releases-in-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no releases): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
