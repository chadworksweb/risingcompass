#!/usr/bin/env bash
# LEC rubric drift check -- polls the Libra Engine Compass published rubric
# version (GET /api/rubric) and, when it has changed since RC last acknowledged
# it, emails an admin alert so RC's display-only core.json / tenets page can be
# reconciled with LEC's live scoring rubric. RC owns no scorer (LEC does), so
# this is the only signal that the constitution RC DISPLAYS has drifted from the
# one that actually SCORES. Calls /api/admin/agent/cron/lec-rubric-drift-check
# with X-Reading-Cron-Key (same service token as the daily reading -- runs in the
# same nightly cron lane). No Opus: a GET to LEC + a system_flags read/write.
#
# Suggested crontab line on le-projects-01 (after the morning chart lane):
#   40 8 * * *  /root/risingcompass-readings/lec-rubric-drift-check.sh >> /root/risingcompass-readings/lec-rubric-drift-check.log 2>&1
#
# On a version change the backend emits the lec_rubric_drift alert (default-on).
# Status values: initialized | unchanged | drifted | unreachable (LEC down is a
# soft no-op, not a failure). Mirrors leit-sweep.sh logging.
set -o pipefail
source /root/backup-config.sh

PROJECT="Rising Compass LEC Rubric Drift Check"
ENDPOINT="http://rc-backend:8000/api/admin/agent/cron/lec-rubric-drift-check"

RC_READING_CRON_KEY=$(grep -E '^RC_READING_CRON_KEY=' /root/rising-compass/.env | cut -d= -f2-)
if [ -z "$RC_READING_CRON_KEY" ]; then
    echo "[$(date)] FAILED setup: RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    alert_failure "$PROJECT" "RC_READING_CRON_KEY not readable from /root/rising-compass/.env"
    exit 1
fi

# No Opus on this path (GET /api/rubric + a system_flags row); 120s is ample.
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

# Success path -- the summary always carries a "status" field.
if ! echo "$BODY" | grep -q '"status"'; then
    SNIPPET=$(echo "$BODY" | head -c 500)
    echo "[$(date)] FAILED no-status-in-response body=$SNIPPET"
    alert_failure "$PROJECT" "Unexpected response (no status): $BODY"
    exit 1
fi

echo "[$(date)] $BODY"
