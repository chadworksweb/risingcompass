#!/usr/bin/env bash
# Build 6 daily social broadcast (Hockey Stick plan). Runs after the trending
# feeders (reading 08:00 -> itunes 09:00 -> shazam 10:00 -> youtube 11:00 UTC),
# so the day's Shazam/YouTube snapshots + the daily reading are already in place.
# Calls /api/admin/agent/cron/social-broadcast with X-Reading-Cron-Key (same
# service token as the daily reading).
#
# The backend selects the day's top trending calibrated verdicts + the daily
# reading, renders a card for each (Playwright screenshot of /cards/), pushes
# them to RC's Buffer queue (fan-out to every connected platform), and records
# each in social_posts so nothing is broadcast twice. Ships DARK: until
# SOCIAL_BROADCAST_ENABLED=true + Buffer is configured, the cron still selects /
# renders / ledgers (status='dark') but pushes nothing.
#
# Mirrors shazam.sh: on success appends '[date] {response JSON}' to
# social-broadcast.log; on failure appends '[date] FAILED ...' and fires the
# backup-config alert.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass Social Broadcast"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/social-broadcast"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# 600s max-time: card rendering (headless Chromium) + the Buffer pushes can add
# real wall-time across several items x platforms.
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

# Success path -- the summary always carries a buffer_configured field.
if ! echo "$BODY" | grep -q '"buffer_configured"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED unexpected-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no buffer_configured): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
