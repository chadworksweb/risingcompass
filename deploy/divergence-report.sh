#!/usr/bin/env bash
# Calibrator v3 feedback organ -- the divergence report. Scans audience
# signals (vibe-needle pushes, clustered misread reports) for songs where the
# crowd systematically opposes the stored verdict and NOMINATES them for a
# human-ruled re-read. Read-only: it never moves a charge. Reports zero rows
# until there is traffic. Calls /api/admin/agent/cron/divergence-report with
# X-Reading-Cron-Key (same service token as the daily reading lane).
#
# Weekly is plenty at launch. Suggested crontab line on le-projects-01:
#   15 17 * * 1  /root/risingcompass-readings/divergence-report.sh >> /root/risingcompass-readings/divergence-report.log 2>&1
#
# On nominations the backend emails Chad a digest (alert key
# divergence_digest) linking each song's admin detail page. Mirrors
# leit-sweep.sh logging.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass Divergence Report"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/divergence-report"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# Pure SQL on the backend; 120s is generous.
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

echo "[$(date)] $BODY"
