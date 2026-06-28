"""Publish the rank-weighting change to the public changelog (Dev Ledger).

Creates one admin-authored `change` item and cuts a CalVer release, which is
what marks it shipped + public. Idempotent: if an item with the same title
already exists it does nothing.

RUN AFTER DEPLOY. The changelog says "what shipped"; this flips the entry
public, so run it once the code change is live (local dev shares the prod DB
via the tunnel, so running it here writes the live changelog).

    cd backend
    .venv\\Scripts\\python.exe scripts\\seed_changelog_weighting.py
"""

from app.database import SessionLocal
from app.models import AdminUser, DevLedgerItem
from app.services import dev_ledger

VERSION = "2026.06.28"
TITLE = "Group charges now weight the top the same way at any chart size"
BODY = (
    "The charge you see for a whole group of songs, like a daily reading or a "
    "full year, is a blend of every song in it, with higher-ranked songs "
    "counting for more. Until now that blend was tuned for a list of exactly "
    "20 songs. As the yearly and historical charts grow toward 100 songs, that "
    "old tuning quietly flattened the result: the number one song stopped "
    "standing out from the rest, and a year measured at 100 songs no longer "
    "lined up with the same year measured at 10.\n\n"
    "We replaced the formula with one that holds its shape at any size. A "
    "song's rank now carries the same weight whether a chart lists 20 songs or "
    "200, so the top keeps its lead and two years stay comparable even when one "
    "is measured deeper than the other. The result tracks how people actually "
    "listen: the number one song counts for roughly eight times the twentieth "
    "and twenty-five times the hundredth, while the songs further down still "
    "count, just less.\n\n"
    "Charge readings on a single song are unchanged. This only affects how a "
    "group of songs is combined into one number."
)
AREA = "methodology"


def main():
    db = SessionLocal()
    try:
        existing = (
            db.query(DevLedgerItem)
            .filter(DevLedgerItem.title == TITLE)
            .first()
        )
        if existing is not None:
            print(f"Already present (id={existing.id}, "
                  f"public={existing.is_public}, version={existing.version}). "
                  "Nothing to do.")
            return

        admin = db.query(AdminUser).order_by(AdminUser.id).first()
        admin_id = admin.id if admin else None

        item = dev_ledger.create_change(
            db, title=TITLE, body=BODY, stage="done", area=AREA,
            admin_id=admin_id,
        )
        dev_ledger.cut_release(
            db, item_ids=[item.id], version=VERSION, admin_id=admin_id,
        )
        print(f"Published changelog item id={item.id} as {VERSION} "
              "(shipped + public).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
