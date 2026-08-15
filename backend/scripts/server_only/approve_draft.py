"""Approve one or more pending AgentDrafts from inside the prod backend container.

Runs the SAME code path as the Approve & Publish button in the awaiting-lyrics
email (`app.routers.agent.approve_draft`), but against a direct DB session, so it
needs neither the email HMAC token nor an admin session cookie.

Usage (from the Windows host, no tunnel):

    ssh deploy@138.197.111.66 "docker exec -i rc-backend python - <ref> [<ref> ...]" \
      < "C:/Users/chad/Local Sites/rising-compass/backend/scripts/server_only/approve_draft.py"

Each arg is a full draft label, e.g. `itunes_download_usa_2026-08-01_draft`.

Set the editorial FIRST (`scripts/set_editorial.py`) -- approval does not
generate one and publishes whatever is already on the draft. Approval blocks
while any non-preorder song still lacks lyrics.

Read-modify-write: this is the one server_only script that WRITES. It publishes
the reading. Do not run it until every song in the draft is settled.
"""

import sys

from app.database import SessionLocal
from app.routers.agent import approve_draft

refs = sys.argv[1:]
if not refs:
    print("usage: approve_draft.py <draft_ref> [<draft_ref> ...]")
    raise SystemExit(2)

db = SessionLocal()
failed = False
for ref in refs:
    try:
        draft = approve_draft(ref, db)
        print(f"{ref} -> {getattr(draft, 'status', '?')}")
    except Exception as exc:  # noqa: BLE001 - report and continue to the next ref
        failed = True
        print(f"{ref} -> ERROR {type(exc).__name__}: {str(exc)[:200]}")
db.close()

raise SystemExit(1 if failed else 0)
