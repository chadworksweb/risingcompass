#!/usr/bin/env bash
# Insert the shop product-page location into the RC root-host server block,
# test, and reload. Idempotent (no-op if already present). Backs up first and
# aborts on a failed nginx -t without touching the live config.
#
# WHY. /shop/product.html serves EVERY product via ?p=slug. Served statically it
# ships one hardcoded <title>Shop</title>, no canonical and no og:*, so all
# products are byte-identical raw HTML under different URLs -- the shape a
# crawler collapses as duplicates. Routing it to the backend lets page_ssr
# inject per-product meta and a Product JSON-LD.
#
# EXACT match (`location = `), so every other file under /shop/ -- shop.js,
# shop.css, the checkout and thank-you directories -- keeps being served off
# disk. This block cannot swallow them.
#
# Sibling of _apply_topics_nginx.sh and _apply_sitemap_nginx.sh, same shape and
# same guarantees. Run it on le-projects-01 as root:
#     scp deploy/_shop_product_nginx_block.txt deploy/_apply_shop_product_nginx.sh <box>:/tmp/
#     sudo bash /tmp/_apply_shop_product_nginx.sh
set -euo pipefail

CONF=/root/proxy/nginx/conf.d/risingcompass.conf
BLOCK=/tmp/_shop_product_nginx_block.txt
MARKER='root /var/www/risingcompass-frontend;'
STAMP=$(date +%Y%m%d-%H%M%S)
BAK="${CONF}.bak-shopproduct-${STAMP}"

if [ ! -f "$BLOCK" ]; then
    echo "ERROR: block file not found at $BLOCK" >&2
    exit 1
fi
if grep -q 'location = /shop/product.html' "$CONF"; then
    echo "already present -- nothing to do"
    exit 0
fi
if ! grep -qF "$MARKER" "$CONF"; then
    echo "ERROR: marker line not found in $CONF" >&2
    exit 1
fi

# ORDER MATTERS, AND GETTING IT WRONG 404s A LIVE PAGE.
#
# This location hands /shop/product.html to the backend. Until the backend
# actually carries the ssr_shop_product route, that hand-off is a 404 on a page
# that worked a second earlier, served statically. That is exactly what happened
# on 2026-08-17: nginx was applied before the deploy, the product page 404'd,
# and this script's own backup was used to roll it back.
#
# So prove the route exists before rewiring anything. A bare
# /shop/product.html returns 404 EITHER way -- the route answers 404 for a
# missing ?p= by design -- but the two 404s differ in kind: the route replies
# with the HTML template, while a backend that has never heard of the path
# replies with FastAPI's application/json {"detail":"Not Found"}. Content type
# is the tell.
CT=$(cd /root/proxy && docker compose exec -T nginx \
        curl -s -o /dev/null -w '%{content_type}' \
        http://rc-backend:8000/shop/product.html 2>/dev/null || true)
case "$CT" in
    text/html*)
        echo "backend serves the route (content-type: $CT) -- proceeding" ;;
    *)
        echo "ERROR: backend does not serve /shop/product.html yet (content-type: ${CT:-unreachable})." >&2
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
if [ "$(grep -c 'location = /shop/product.html' "$TMP")" != "1" ]; then
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
