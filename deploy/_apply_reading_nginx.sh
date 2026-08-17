#!/usr/bin/env bash
# Insert the homepage + daily chart-page locations into the RC root-host server
# block, test, and reload. Idempotent (no-op if already present). Backs up first
# and aborts on a failed nginx -t without touching the live config.
#
# WHY. The homepage and the six daily chart pages render through
# js/chart-shell.js, which must NOT be re-implemented server-side (CLAUDE.md
# forbids it; three drifted copies had to be collapsed once already). The
# backend instead serves a plain data summary the shell overwrites on load, so
# a crawler that does not run JS sees the day's ranking and editorial.
#
# EXACT matches only, so `location /` keeps serving every asset beneath these
# paths and the bare-root match cannot swallow anything else.
#
# Sibling of _apply_topics_nginx.sh / _apply_shop_product_nginx.sh.
#     scp deploy/_reading_nginx_block.txt deploy/_apply_reading_nginx.sh <box>:/tmp/
#     sudo bash /tmp/_apply_reading_nginx.sh
set -euo pipefail

CONF=/root/proxy/nginx/conf.d/risingcompass.conf
BLOCK=/tmp/_reading_nginx_block.txt
MARKER='root /var/www/risingcompass-frontend;'
STAMP=$(date +%Y%m%d-%H%M%S)
BAK="${CONF}.bak-reading-${STAMP}"

if [ ! -f "$BLOCK" ]; then
    echo "ERROR: block file not found at $BLOCK" >&2
    exit 1
fi
if grep -q 'location = /charts/unified/' "$CONF"; then
    echo "already present -- nothing to do"
    exit 0
fi
if ! grep -qF "$MARKER" "$CONF"; then
    echo "ERROR: marker line not found in $CONF" >&2
    exit 1
fi

# ORDER MATTERS, AND GETTING IT WRONG 404s A LIVE PAGE.
#
# These locations hand the homepage and six chart pages to the backend. Until
# the backend actually carries the reading routes, each hand-off is a 404 on a
# page that worked a second earlier, served statically -- and one of them is
# the site root. That is exactly what happened on 2026-08-17 with the shop
# product page: nginx was applied before the deploy, the page 404'd, and that
# script's own backup was used to roll it back.
#
# So prove the routes exist before rewiring anything. These serve a real 200
# with the page in it, so a plain status check is enough -- unlike the shop
# product page, which 404s either way and needs its content type read.
CODE=$(cd /root/proxy && docker compose exec -T nginx \
        curl -s -o /dev/null -w '%{http_code}' \
        http://rc-backend:8000/charts/unified/ 2>/dev/null || true)
case "$CODE" in
    200)
        echo "backend serves the reading routes -- proceeding" ;;
    *)
        echo "ERROR: backend does not serve /charts/unified/ yet (HTTP ${CODE:-unreachable})." >&2
        echo "       Deploy the backend FIRST, then re-run this script." >&2
        exit 1 ;;
esac

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

# Exactly one. A duplicated location makes nginx refuse to start, and a zero
# means the marker matched nothing.
# NOTE: this list was once written with literal "\n" between the entries instead
# of real newlines, so the loop iterated over a stray `n` and the check failed
# every time on a needle that was never a location. The guard did its job and
# refused to touch the config, which is why it surfaced as a blocked apply rather
# than a broken nginx. Keep one quoted needle per line.
for needle in \
    'location = /' \
    'location = /charts/spotify/' \
    'location = /charts/itunes/' \
    'location = /charts/shazam/' \
    'location = /charts/youtube/' \
    'location = /charts/new-music-friday/' \
    'location = /charts/unified/'; do
    if [ "$(grep -c "^    $needle {" "$TMP")" != "1" ]; then
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
