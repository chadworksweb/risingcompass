#!/usr/bin/env bash
# Insert the topic + theme location blocks into the RC root-host server block,
# test, and reload. Idempotent (no-op if already present). Backs up first and
# aborts on a failed nginx -t without touching the live config.
#
# WHY THIS EXISTS. The shared reverse proxy is not in any repo -- it carries
# other projects' server blocks too -- so RC's own location blocks lived in
# exactly one place, on one droplet, added by hand. A proxy rebuild from any
# other source drops them, and the failure mode is all 41 topic and theme
# detail pages 404ing at once with no obvious cause. This file is the record;
# running it is how the record gets back onto a box that lost it.
#
# Sibling of _apply_sitemap_nginx.sh, same shape and same guarantees. Run it on
# le-projects-01 as root:
#     scp deploy/_topics_nginx_block.txt deploy/_apply_topics_nginx.sh <box>:/tmp/
#     sudo bash /tmp/_apply_topics_nginx.sh
set -euo pipefail

CONF=/root/proxy/nginx/conf.d/risingcompass.conf
BLOCK=/tmp/_topics_nginx_block.txt
MARKER='root /var/www/risingcompass-frontend;'
STAMP=$(date +%Y%m%d-%H%M%S)
BAK="${CONF}.bak-topics-${STAMP}"

if [ ! -f "$BLOCK" ]; then
    echo "ERROR: block file not found at $BLOCK" >&2
    exit 1
fi
# Present already is the EXPECTED result on a healthy box: these routes have
# been live since 2026-08-15. Changing nothing is a success, not a skip.
if grep -q 'location @topic_detail' "$CONF"; then
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

# Sanity: each named location must now appear exactly once. A duplicate would
# make nginx refuse to start, and a zero means the marker matched nothing.
for needle in 'location @topic_detail' 'location @theme_detail' \
              'location /topics/' 'location /themes/'; do
    if [ "$(grep -c "$needle" "$TMP")" != "1" ]; then
        echo "ERROR: insertion produced unexpected result for '$needle'; leaving config untouched" >&2
        rm -f "$TMP"; exit 1
    fi
done

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
