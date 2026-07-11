#!/usr/bin/env python3
"""Sync the RC shop's product cache from Printify.

Pulls every product from the configured Printify shop and upserts it into
shop_products (see app/services/shop_sync.py). Idempotent -- run it any time a
product's title/price/images/variants change in Printify, or after adding a new
product to the shop.

Usage (from backend/):
  .venv\\Scripts\\python.exe scripts\\sync_shop.py

Needs PRINTIFY_API_TOKEN + PRINTIFY_SHOP_ID in the environment / backend/.env.
NOTE: local dev points DATABASE_URL at the shared prod Postgres via the tunnel,
so this writes to the live shop_products table -- that is the intended path for
seeding prod.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.services import printify_service, shop_sync  # noqa: E402


def main() -> int:
    if not printify_service.is_configured():
        print("Printify is not configured (set PRINTIFY_API_TOKEN + PRINTIFY_SHOP_ID).")
        return 1
    db = SessionLocal()
    try:
        result = shop_sync.sync_printify_products(db)
    finally:
        db.close()
    print(f"fetched={result['fetched']} created={result['created']} "
          f"updated={result['updated']} ok={result['ok']}")
    for e in result.get("errors", []):
        print(f"  error: {e}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
