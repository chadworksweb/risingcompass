#!/usr/bin/env bash
# Weekly MaxMind GeoLite2 refresh for the consent-bar geo default
# (GET /api/geo-country -> app/routers/geo.py). Run by ROOT cron on
# le-projects-01. Keeps the Country + City DBs current so the EU/UK/EEA and
# California opt-in defaults stay accurate and within MaxMind's license terms.
#
# Sources MAXMIND_LICENSE_KEY from RC's .env so the key never lives in the
# crontab. After downloading, restarts the backend: geoip2 memory-maps the DB
# at first request and holds that inode, so an atomic file replace is NOT seen
# until the process restarts.
#
# Install (root crontab, weekly Mon 04:17 UTC):
#   17 4 * * 1 /bin/bash /root/rising-compass/deploy/geoip-cron.sh >> /var/log/geoip-refresh.log 2>&1

set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

REPO=/root/rising-compass
ENV_FILE="${REPO}/.env"

KEY=$(grep -E '^MAXMIND_LICENSE_KEY=' "${ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r"')
if [ -z "${KEY}" ]; then
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') ERROR: MAXMIND_LICENSE_KEY not found in ${ENV_FILE}" >&2
  exit 1
fi

echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') geoip refresh start ==="
MAXMIND_LICENSE_KEY="${KEY}" bash "${REPO}/deploy/refresh_geoip.sh"
( cd "${REPO}" && docker compose restart backend )
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') geoip refresh done ==="
