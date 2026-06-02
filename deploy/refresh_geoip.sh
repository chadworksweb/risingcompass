#!/usr/bin/env bash
# Download / refresh the MaxMind GeoLite2-Country database used by the cookie
# consent bar's geo-aware default (GET /api/geo-country -> app/routers/geo.py).
#
# Run on the server (le-projects-01). Requires a free MaxMind account + license
# key: https://www.maxmind.com/en/geolite2/signup -> Account -> Manage License Keys.
#
# Usage:
#   MAXMIND_LICENSE_KEY=xxxxxxxx bash deploy/refresh_geoip.sh
#
# Optional env:
#   GEOIP_DEST_DIR   target dir (default /root/geoip; matches the docker-compose
#                    `/root/geoip:/geoip:ro` mount and GEOIP_DB_PATH).
#
# Idempotent: writes GeoLite2-Country.mmdb atomically. The backend reads the
# file at first request and caches the reader, so after a refresh restart the
# backend to pick up the new DB:
#   cd /root/rising-compass && docker compose restart backend
#
# Cron (weekly, MaxMind updates Tue/Fri) -- DB key lives in the crontab env:
#   17 4 * * 1 MAXMIND_LICENSE_KEY=xxxx bash /root/rising-compass/deploy/refresh_geoip.sh >> /var/log/geoip-refresh.log 2>&1

set -euo pipefail

: "${MAXMIND_LICENSE_KEY:?Set MAXMIND_LICENSE_KEY (from your MaxMind account)}"
DEST_DIR="${GEOIP_DEST_DIR:-/root/geoip}"
DEST="${DEST_DIR}/GeoLite2-Country.mmdb"
EDITION="GeoLite2-Country"
URL="https://download.maxmind.com/app/geoip_download?edition_id=${EDITION}&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz"

mkdir -p "${DEST_DIR}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Downloading ${EDITION}..."
curl -fsSL "${URL}" -o "${TMP}/geoip.tar.gz"

echo "Extracting..."
tar -xzf "${TMP}/geoip.tar.gz" -C "${TMP}"

# The archive expands to GeoLite2-Country_YYYYMMDD/GeoLite2-Country.mmdb
MMDB="$(find "${TMP}" -name "${EDITION}.mmdb" -print -quit)"
if [ -z "${MMDB}" ]; then
  echo "ERROR: ${EDITION}.mmdb not found in archive" >&2
  exit 1
fi

# Atomic replace so a concurrent read never sees a half-written file.
cp "${MMDB}" "${DEST}.tmp"
mv -f "${DEST}.tmp" "${DEST}"
echo "Installed ${DEST} ($(du -h "${DEST}" | cut -f1))"
echo "Restart the backend to load it: cd /root/rising-compass && docker compose restart backend"
