#!/usr/bin/env python3
"""Insert the PostHog first-party reverse-proxy location blocks into the
risingcompass.net frontend server block. Run as root on the proxy host:

    cat _ph_proxy_patch.py | ssh deploy@host "sudo python3 -"

Idempotent. Backs up to risingcompass.conf.bak-before-ph-proxy. Anchors on the
unique frontend `location /` (try_files index.html) so it can't land in the
api. server block (which uses `location / { proxy_pass }`)."""
import pathlib
import sys

CONF = pathlib.Path("/root/proxy/nginx/conf.d/risingcompass.conf")

ANCHOR = (
    "    location / {\n"
    "        try_files $uri $uri/ /index.html;\n"
    "    }"
)

BLOCK = (
    "    # PostHog reverse proxy (first-party -> ad-blocker resistant). Added\n"
    "    # 2026-05-30. /ph/static/* serves the JS bundle + session-replay\n"
    "    # recorder from PostHog's assets host; /ph/* carries events, flags and\n"
    "    # replay data to the ingestion host. Must precede `location /`.\n"
    "    location ^~ /ph/static/ {\n"
    "        proxy_pass https://us-assets.i.posthog.com/static/;\n"
    "        proxy_set_header Host us-assets.i.posthog.com;\n"
    "        proxy_ssl_server_name on;\n"
    "    }\n"
    "    location ^~ /ph/ {\n"
    "        proxy_pass https://us.i.posthog.com/;\n"
    "        proxy_set_header Host us.i.posthog.com;\n"
    "        proxy_ssl_server_name on;\n"
    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "    }\n\n"
)


def main():
    text = CONF.read_text()
    if "/ph/static/" in text:
        print("ALREADY_PATCHED")
        return 0
    if ANCHOR not in text:
        print("ANCHOR_NOT_FOUND")
        return 2
    backup = CONF.with_name("risingcompass.conf.bak-before-ph-proxy")
    backup.write_text(text)
    CONF.write_text(text.replace(ANCHOR, BLOCK + ANCHOR, 1))
    print("PATCHED; backup at", backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
