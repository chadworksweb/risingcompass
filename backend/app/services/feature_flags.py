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


# --- Identity-resolution trgm fuzzy rung (Phase 2, ships DARK) --------------
# Gates Rung 3 (pg_trgm fuzzy fallback) in resolve_song_identity. Fail-CLOSED:
# absent flag = OFF, so fuzzy auto-linking + gray-band candidate emission stay
# dormant until an admin enables it after watching the merge-candidate queue.
IDENTITY_TRGM_KEY = "identity_trgm.enabled"
# Separate sub-gate for the AUTO-LINK action. With the rung enabled but autolink
# OFF (the default "watch" posture), a high-confidence trgm match is QUEUED as a
# merge candidate instead of silently linked -- so every near-match is reviewable
# before the rung is trusted to act on its own. Flip this on once the candidate
# stream looks right.
IDENTITY_TRGM_AUTOLINK_KEY = "identity_trgm.autolink"


def is_identity_trgm_enabled(db: Session) -> bool:
    return (_get_flag(db, IDENTITY_TRGM_KEY) or "false").lower() == "true"


def is_identity_trgm_autolink_enabled(db: Session) -> bool:
    return (_get_flag(db, IDENTITY_TRGM_AUTOLINK_KEY) or "false").lower() == "true"


# --- Audience Resonance slicer (ships DARK) --------------------------------
# Gates the live testimony classifier (services/resonance_slicer.py). Fail-CLOSED:
# absent flag = OFF, so submissions store a neutral verdict and NO model call is
# made until the approach + credits are settled and the model seam is wired.
RESONANCE_SLICER_KEY = "resonance_slicer.enabled"


def is_resonance_slicer_enabled(db: Session) -> bool:
    return (_get_flag(db, RESONANCE_SLICER_KEY) or "false").lower() == "true"


def set_resonance_slicer_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, RESONANCE_SLICER_KEY, enabled)


# --- Audience Resonance feature gate (dark launch, like album_charger) -------
# Fail-CLOSED: absent = DARK. While DARK the public reads serve the curated
# is_synthetic demo set (demo=true) and /submit is closed; when ON, reads serve
# only real rows and submissions work (the slicer is still gated separately by
# RESONANCE_SLICER_KEY). Flip live -> the synthetic demo auto-hides.
AR_ENABLED_KEY = "audience_resonance.enabled"


def is_audience_resonance_enabled(db: Session) -> bool:
    return (_get_flag(db, AR_ENABLED_KEY) or "false").lower() == "true"


def set_audience_resonance_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, AR_ENABLED_KEY, enabled)


def _set_flag(db: Session, key: str, enabled: bool) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    val = "true" if enabled else "false"
    if row:
        row.value = val
    else:
        db.add(SystemFlag(key=key, value=val))
    db.commit()


def set_identity_trgm_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, IDENTITY_TRGM_KEY, enabled)


def set_identity_trgm_autolink_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, IDENTITY_TRGM_AUTOLINK_KEY, enabled)


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


# --- Album Charger kill switch ---------------------------------------------
# Independent of the whole-LC kill switch above: closes ONLY the Album Charger
# tab (manual + search) while the single-song Song Charger stays open. Fails
# CLOSED -- an absent flag means disabled, so the Album Charger is off until an
# admin explicitly opens it (mirrors the launch.locked default).

ALBUM_DISABLED_KEY = "album_charger.disabled"
ALBUM_DISABLED_MESSAGE_KEY = "album_charger.disabled_message"
DEFAULT_ALBUM_DISABLED_MESSAGE = (
    "Album charging is closed right now. Single songs are still open."
)


def is_album_charger_disabled(db: Session) -> bool:
    # Absent flag -> disabled (fail closed). Only an explicit "false" opens it.
    return (_get_flag(db, ALBUM_DISABLED_KEY) or "true").lower() == "true"


def album_charger_disabled_message(db: Session) -> str:
    return _get_flag(db, ALBUM_DISABLED_MESSAGE_KEY) or DEFAULT_ALBUM_DISABLED_MESSAGE


def set_album_charger_disabled(db: Session, disabled: bool) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == ALBUM_DISABLED_KEY).first()
    if row:
        row.value = "true" if disabled else "false"
    else:
        db.add(SystemFlag(key=ALBUM_DISABLED_KEY, value="true" if disabled else "false"))
    db.commit()


def set_album_charger_disabled_message(db: Session, message: str | None) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == ALBUM_DISABLED_MESSAGE_KEY).first()
    if message is None:
        if row:
            db.delete(row)
            db.commit()
        return
    if row:
        row.value = message
    else:
        db.add(SystemFlag(key=ALBUM_DISABLED_MESSAGE_KEY, value=message))
    db.commit()


# --- Discussion (public per-song/artist comments) kill switch --------------
# Closes the public Discussion widget on song + artist pages. Fails CLOSED --
# an absent flag means disabled, so Discussion ships DARK until an admin opens
# it (no users yet). Mirrors album_charger.disabled / launch.locked. The widget
# checks /api/comments/availability and stays hidden when closed; writes 503.

COMMENTS_DISABLED_KEY = "comments.disabled"


def is_comments_disabled(db: Session) -> bool:
    # Absent flag -> disabled (fail closed). Only an explicit "false" opens it.
    return (_get_flag(db, COMMENTS_DISABLED_KEY) or "true").lower() == "true"


def set_comments_disabled(db: Session, disabled: bool) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == COMMENTS_DISABLED_KEY).first()
    if row:
        row.value = "true" if disabled else "false"
    else:
        db.add(SystemFlag(key=COMMENTS_DISABLED_KEY, value="true" if disabled else "false"))
    db.commit()


# --- Lyrical Charger daily rate limits -------------------------------------
# Per-IP (anon) and per-user (signed-in) daily caps for the calibrate-*
# endpoints, tunable from the LC admin section. Stored as text in
# system_flags; an absent flag falls back to the code default.

LC_ANON_DAILY_LIMIT_KEY = "lyrical_charger.anon_daily_limit"
LC_USER_DAILY_LIMIT_KEY = "lyrical_charger.user_daily_limit"
# Free-tier daily free-charge allotment (distinct from the slowapi backstop
# above): how many fresh single-song runs a FREE-TIER account gets per UTC day
# at zero credit cost. See billing.charge_song / billing_config.DAILY_FREE_CHARGES.
LC_FREE_DAILY_CHARGES_KEY = "lyrical_charger.free_daily_charges"
DEFAULT_LC_USER_DAILY_LIMIT = 100


def _default_anon_daily_limit() -> int:
    # Lazy import keeps feature_flags free of a billing_config dependency at
    # module load (feature_flags is imported very early by the routers).
    from app import billing_config
    return billing_config.ANON_CHARGER_DAILY_LIMIT


def _default_free_daily_charges() -> int:
    from app import billing_config
    return billing_config.DAILY_FREE_CHARGES


def _read_int_flag(db: Session, key: str, default: int) -> int:
    raw = _get_flag(db, key)
    if raw is None:
        return default
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return val if val >= 0 else default


def _set_int_flag(db: Session, key: str, value: int | None) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    if value is None:
        if row:
            db.delete(row)
            db.commit()
        return
    if row:
        row.value = str(int(value))
    else:
        db.add(SystemFlag(key=key, value=str(int(value))))
    db.commit()


def lyrical_charger_anon_daily_limit(db: Session) -> int:
    return _read_int_flag(db, LC_ANON_DAILY_LIMIT_KEY, _default_anon_daily_limit())


def lyrical_charger_user_daily_limit(db: Session) -> int:
    return _read_int_flag(db, LC_USER_DAILY_LIMIT_KEY, DEFAULT_LC_USER_DAILY_LIMIT)


def lyrical_charger_free_daily_charges(db: Session) -> int:
    return _read_int_flag(db, LC_FREE_DAILY_CHARGES_KEY, _default_free_daily_charges())


def set_lyrical_charger_anon_daily_limit(db: Session, value: int | None) -> None:
    _set_int_flag(db, LC_ANON_DAILY_LIMIT_KEY, value)


def set_lyrical_charger_user_daily_limit(db: Session, value: int | None) -> None:
    _set_int_flag(db, LC_USER_DAILY_LIMIT_KEY, value)


def set_lyrical_charger_free_daily_charges(db: Session, value: int | None) -> None:
    _set_int_flag(db, LC_FREE_DAILY_CHARGES_KEY, value)


# --- Scrape Shield (anti-scrape stack) -------------------------------------
# Genius-style defense on the public read path. Three independent enforcement
# toggles, all FAIL-OPEN (absent = OFF) so the shield ships in observe-only
# mode: it scores + logs without blocking until an admin flips a switch after
# watching the bot feed. The numeric tunables size the per-IP read limiter and
# the bot-score block threshold. See services/scrape_shield.py.
SHIELD_RATELIMIT_ENABLED_KEY = "scrape_shield.ratelimit_enabled"
SHIELD_BOTSCORE_ENABLED_KEY = "scrape_shield.botscore_enabled"
SHIELD_TOKEN_ENABLED_KEY = "scrape_shield.token_enabled"
SHIELD_READ_PER_MINUTE_KEY = "scrape_shield.read_per_minute"
SHIELD_READ_PER_DAY_KEY = "scrape_shield.read_per_day"
SHIELD_BOTSCORE_THRESHOLD_KEY = "scrape_shield.botscore_threshold"

# Defaults: generous for humans, hard cap on enumeration. A real reader browses
# a few pages a minute; a scraper pulling the sitemap blows past these fast.
DEFAULT_SHIELD_READ_PER_MINUTE = 120
DEFAULT_SHIELD_READ_PER_DAY = 3000
DEFAULT_SHIELD_BOTSCORE_THRESHOLD = 60  # one ua_deny hit = 60, so a scripted UA trips it


def is_shield_ratelimit_enabled(db: Session) -> bool:
    return (_get_flag(db, SHIELD_RATELIMIT_ENABLED_KEY) or "false").lower() == "true"


def is_shield_botscore_enabled(db: Session) -> bool:
    return (_get_flag(db, SHIELD_BOTSCORE_ENABLED_KEY) or "false").lower() == "true"


def is_shield_token_enabled(db: Session) -> bool:
    return (_get_flag(db, SHIELD_TOKEN_ENABLED_KEY) or "false").lower() == "true"


def shield_read_per_minute(db: Session) -> int:
    return _read_int_flag(db, SHIELD_READ_PER_MINUTE_KEY, DEFAULT_SHIELD_READ_PER_MINUTE)


def shield_read_per_day(db: Session) -> int:
    return _read_int_flag(db, SHIELD_READ_PER_DAY_KEY, DEFAULT_SHIELD_READ_PER_DAY)


def shield_botscore_threshold(db: Session) -> int:
    return _read_int_flag(db, SHIELD_BOTSCORE_THRESHOLD_KEY, DEFAULT_SHIELD_BOTSCORE_THRESHOLD)


def set_shield_ratelimit_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, SHIELD_RATELIMIT_ENABLED_KEY, enabled)


def set_shield_botscore_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, SHIELD_BOTSCORE_ENABLED_KEY, enabled)


def set_shield_token_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, SHIELD_TOKEN_ENABLED_KEY, enabled)


def set_shield_read_per_minute(db: Session, value: int | None) -> None:
    _set_int_flag(db, SHIELD_READ_PER_MINUTE_KEY, value)


def set_shield_read_per_day(db: Session, value: int | None) -> None:
    _set_int_flag(db, SHIELD_READ_PER_DAY_KEY, value)


def set_shield_botscore_threshold(db: Session, value: int | None) -> None:
    _set_int_flag(db, SHIELD_BOTSCORE_THRESHOLD_KEY, value)


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


# --- LEC calibration ------------------------------------------------------
# The lec.enabled flag was the Phase-2 migration toggle that let RC route the
# calibrator's scoring half to the Libra Engine Compass (LEC) service while
# keeping the in-process calibrator as a fallback. Phase 3 (2026-06-16) deleted
# RC's in-process scorer entirely: LEC is now the sole scorer, unconditionally,
# so the toggle and its fail-closed fallback no longer have a meaning. The flag
# (and its is_lec_calibration_enabled / set_lec_calibration_enabled accessors)
# were removed with that deletion. A stale `lec.enabled` row may linger in prod
# system_flags; it is inert.


# --- Taxonomy: DB drives the tagger (Phase 2a cutover) ---------------------
# When ON, the live ether tagger builds its prompt + valid-slug set from the
# ether_topics scope/examples rows instead of the code constants (ETHER_TAXONOMY).
# Fail-CLOSED (absent flag = OFF): the tagger reads CODE until an admin flips
# this, so a half-finished DB definition can't silently corrupt live
# classification. The code constants stay as the automatic fallback even when
# this is on (empty/unreachable DB -> code), so a flip is reversible. Mirrors
# the launch.locked fail-closed toggle pattern.

TAXONOMY_DB_DRIVEN_KEY = "taxonomy_db_driven.enabled"


def is_taxonomy_db_driven(db: Session) -> bool:
    # Absent flag -> OFF (fail closed). Only an explicit "true" routes to the DB.
    return (_get_flag(db, TAXONOMY_DB_DRIVEN_KEY) or "false").lower() == "true"


def set_taxonomy_db_driven(db: Session, enabled: bool) -> None:
    _set_flag(db, TAXONOMY_DB_DRIVEN_KEY, enabled)


# --- Faultline (internal error ledger) kill switch -------------------------
# Gates Faultline capture writes. Capture is fail-safe regardless -- this is a
# deliberate off switch for the writes/noise, not a safety mechanism. Fails
# OPEN (absent flag = enabled): a fresh install captures by default, matching
# the "durable by default" intent. Toggle from Site Admin (a later phase).

FAULTLINE_ENABLED_KEY = "faultline.enabled"


def is_faultline_enabled(db: Session) -> bool:
    # Absent flag -> enabled (fail open). Only an explicit "false" disables it.
    return (_get_flag(db, FAULTLINE_ENABLED_KEY) or "true").lower() == "true"


def set_faultline_enabled(db: Session, enabled: bool) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == FAULTLINE_ENABLED_KEY).first()
    if row:
        row.value = "true" if enabled else "false"
    else:
        db.add(SystemFlag(key=FAULTLINE_ENABLED_KEY, value="true" if enabled else "false"))
    db.commit()


# --- Sentinel Auditor Team (ships DARK) ------------------------------------
# Gates the whole public Sentinel Auditor surface: the apply form, the auditor
# portal, finding submission, and the leaderboard. Fails CLOSED (absent flag =
# off), so the program is invisible until an admin opens it -- the admin triage
# side (/api/admin/sentinel/*) is NOT gated by this, so applications and findings
# can be configured/reviewed while the public side is dark. Mirrors the
# launch.locked / album_charger.disabled fail-closed pattern.

SENTINEL_AUDITOR_KEY = "sentinel_auditor.enabled"


def is_sentinel_auditor_enabled(db: Session) -> bool:
    # Absent flag -> disabled (fail closed). Only an explicit "true" opens it.
    return (_get_flag(db, SENTINEL_AUDITOR_KEY) or "false").lower() == "true"


def set_sentinel_auditor_enabled(db: Session, enabled: bool) -> None:
    _set_flag(db, SENTINEL_AUDITOR_KEY, enabled)
