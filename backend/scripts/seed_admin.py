"""Create or reset an admin user for the Rising Compass admin dashboard.

Run from the backend directory with the same .env that the app uses
(DATABASE_URL + TURSO_AUTH_TOKEN, etc.) so the row lands in Turso.

    cd backend
    .venv/bin/python scripts/seed_admin.py
        # or on Windows
    .venv\\Scripts\\python.exe scripts\\seed_admin.py

Prompts for a username and password. If the username already exists,
the password is reset and the lockout cleared. Logs nothing to stdout
beyond confirmation — passwords never echo and never enter the DB in
plaintext.

Pass --revoke-sessions to also kill every active session for the user
(useful after a suspected compromise or password reset for an active
admin who's currently signed in elsewhere).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running this script from anywhere — make `app` importable.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models import AdminUser  # noqa: E402
from app.services import admin_auth as auth_svc  # noqa: E402


def prompt_password() -> str:
    while True:
        p1 = getpass.getpass("Password (input hidden): ")
        if len(p1) < 12:
            print("Password must be at least 12 characters.", file=sys.stderr)
            continue
        p2 = getpass.getpass("Confirm: ")
        if p1 != p2:
            print("Passwords do not match. Try again.", file=sys.stderr)
            continue
        return p1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", help="Username (prompts if omitted)")
    ap.add_argument("--email", help="Optional email")
    ap.add_argument("--role", default="admin", help="Role (default: admin)")
    ap.add_argument(
        "--revoke-sessions",
        action="store_true",
        help="Revoke every existing session for this user after the upsert",
    )
    args = ap.parse_args()

    username = args.username or input("Username: ").strip()
    if not username:
        print("Username required.", file=sys.stderr)
        return 1

    password = prompt_password()
    pw_hash = auth_svc.hash_password(password)

    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.username == username).first()
        if user is None:
            user = AdminUser(
                username=username,
                email=(args.email or None),
                password_hash=pw_hash,
                role=args.role,
                is_active=True,
            )
            db.add(user)
            db.commit()
            print(f"Created admin user '{username}' (id={user.id}).")
        else:
            user.password_hash = pw_hash
            if args.email:
                user.email = args.email
            user.is_active = True
            user.failed_login_count = 0
            user.locked_until = None
            user.updated_at = datetime.now(timezone.utc)
            db.commit()
            print(f"Updated admin user '{username}' (id={user.id}). Lockout cleared.")

        if args.revoke_sessions:
            n = auth_svc.revoke_all_for_user(db, user.id)
            print(f"Revoked {n} existing session(s).")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
