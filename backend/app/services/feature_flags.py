"""Lightweight feature-flag accessors.

Backed by the `system_flags` table. Keys are dotted strings; values are
stored as text and parsed on read.
"""

from sqlalchemy.orm import Session

from app.models import SystemFlag


LC_DISABLED_KEY = "lyrical_charger.disabled"
LC_DISABLED_MESSAGE_KEY = "lyrical_charger.disabled_message"
DEFAULT_LC_DISABLED_MESSAGE = (
    "Drop your email below and we'll let you know the moment it's back."
)


def _get_flag(db: Session, key: str) -> str | None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    return row.value if row else None


def is_lyrical_charger_disabled(db: Session) -> bool:
    return (_get_flag(db, LC_DISABLED_KEY) or "false").lower() == "true"


def lyrical_charger_disabled_message(db: Session) -> str:
    return _get_flag(db, LC_DISABLED_MESSAGE_KEY) or DEFAULT_LC_DISABLED_MESSAGE


def set_lyrical_charger_disabled(db: Session, disabled: bool) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == LC_DISABLED_KEY).first()
    if row:
        row.value = "true" if disabled else "false"
    else:
        db.add(SystemFlag(key=LC_DISABLED_KEY, value="true" if disabled else "false"))
    db.commit()


def set_lyrical_charger_disabled_message(db: Session, message: str | None) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == LC_DISABLED_MESSAGE_KEY).first()
    if message is None:
        if row:
            db.delete(row)
            db.commit()
        return
    if row:
        row.value = message
    else:
        db.add(SystemFlag(key=LC_DISABLED_MESSAGE_KEY, value=message))
    db.commit()


# --- Launch lock -----------------------------------------------------------
# Soft pre-launch gate for the public sign-up form AND the billing checkout
# (subscribe / buy credits). Default is LOCKED when the flag has never been
# set, so a fresh deploy is closed until explicitly opened from admin -- no
# redeploy needed to open. Sign-IN for existing accounts is unaffected.

LAUNCH_LOCKED_KEY = "launch.locked"
LAUNCH_LOCKED_MESSAGE_KEY = "launch.locked_message"
DEFAULT_LAUNCH_LOCKED_MESSAGE = (
    "Sign-ups and subscriptions aren't open to the public just yet. Thanks for your patience."
)


def is_launch_locked(db: Session) -> bool:
    # Absent flag -> locked (fail closed). Only an explicit "false" opens it.
    return (_get_flag(db, LAUNCH_LOCKED_KEY) or "true").lower() == "true"


def launch_locked_message(db: Session) -> str:
    return _get_flag(db, LAUNCH_LOCKED_MESSAGE_KEY) or DEFAULT_LAUNCH_LOCKED_MESSAGE


def set_launch_locked(db: Session, locked: bool) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == LAUNCH_LOCKED_KEY).first()
    if row:
        row.value = "true" if locked else "false"
    else:
        db.add(SystemFlag(key=LAUNCH_LOCKED_KEY, value="true" if locked else "false"))
    db.commit()


def set_launch_locked_message(db: Session, message: str | None) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == LAUNCH_LOCKED_MESSAGE_KEY).first()
    if message is None:
        if row:
            db.delete(row)
            db.commit()
        return
    if row:
        row.value = message
    else:
        db.add(SystemFlag(key=LAUNCH_LOCKED_MESSAGE_KEY, value=message))
    db.commit()
