#!/usr/bin/env bash
# Insert the dynamic-sitemap location blocks into the RC root-host server block,
# test, and reload. Idempotent (no-op if already present). Backs up first and
# aborts on a failed nginx -t without touching the live config.
set -euo pipefail

CONF=/root/proxy/nginx/conf.d/risingcompass.conf
BLOCK=/tmp/_sitemap_nginx_block.txt
MARKER='root /var/www/risingcompass-frontend;'
STAMP=$(date +%Y%m%d-%H%M%S)
BAK="${CONF}.bak-${STAMP}"

if grep -q 'location = /sitemap.xml' "$CONF"; then
    echo "already present -- nothing to do"
    exit 0
fi
if ! grep -qF "$MARKER" "$CONF"; then
    echo "ERROR: marker line not found in $CONF" >&2
    exit 1
fi

cp -a "$CONF" "$BAK"
echo "backup: $BAK"

TMP=$(mktemp)
awk -v blockfile="$BLOCK" -v marker="$MARKER" '
    { print }
    index($0, marker) {
        while ((getline line < blockfile) > 0) print line
        close(blockfile)
    }
' "$CONF" > "$TMP"

# sanity: the blocks must now be present exactly once
if [ "$(grep -c 'location = /sitemap.xml' "$TMP")" != "1" ]; then
    echo "ERROR: insertion produced unexpected result; leaving config untouched" >&2
    rm -f "$TMP"; exit 1
fi

cp "$TMP" "$CONF"; rm -f "$TMP"

cd /root/proxy
if docker compose exec -T nginx nginx -t; then
    docker compose exec -T nginx nginx -s reload
    echo "RELOADED OK"
else
    echo "nginx -t FAILED -- restoring backup" >&2
    cp -a "$BAK" "$CONF"
    exit 1
fi
