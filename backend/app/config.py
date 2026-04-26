import json

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # rc_admin_key is no longer used for human admin auth (replaced by
    # session cookies via /api/rc-admin-{token}/login). It still signs
    # one-time HMAC tokens used in approval emails (auth.py:create_approval_token)
    # and is the legacy fallback for verify_backup_key when RC_BACKUP_KEY
    # is unset. Required so the HMAC remains stable across deploys.
    rc_admin_key: str = ""
    rc_api_key: str = ""  # REQUIRED — public consumer key (LC web tool, RC frontend)
    rc_service_key: str = ""  # OPTIONAL — first-party service key (chadlewine, internal scripts)
    rc_backup_key: str = ""  # OPTIONAL — service token for the cron-driven /api/admin/backup endpoint. Falls back to rc_admin_key during transition.
    rc_reading_cron_key: str = ""  # REQUIRED in prod — service token for the cron-driven /api/admin/agent/cron/calibrate-live endpoint. Distinct from rc_backup_key so a leaked reading key can't trigger backups (and vice versa).

    # Admin session policy
    admin_session_idle_seconds: int = 28800  # 8h sliding window
    admin_session_absolute_seconds: int = 86400  # 24h absolute cap
    admin_login_max_failures_per_window: int = 5
    admin_login_window_seconds: int = 900  # 15 min
    admin_lockout_threshold: int = 10  # consecutive failures → temp lock
    admin_lockout_duration_seconds: int = 3600  # 1h

    # Obscured login URL prefix — login page lives at /api/rc-admin-{token}/login.
    # Anything other than this token returns 404, so port scanners hitting
    # /api/admin/login or similar don't find a form to submit.
    admin_login_url_token: str = ""
    database_url: str = "sqlite:///./data/rising_compass.db"
    turso_auth_token: str = ""  # required when database_url is libsql://...
    # Embedded replica: when set, reads go to a local SQLite file synced
    # from the Turso primary every turso_sync_interval seconds. Writes still
    # round-trip to the primary. Unset = connect directly to Turso.
    turso_replica_path: str = ""
    turso_sync_interval: int = 30
    cors_origins: str = '["http://localhost:3000","http://127.0.0.1:3000","https://risingcompass.net","https://api.risingcompass.net"]'

    # Anthropic API
    anthropic_api_key: str = ""
    agent_model: str = "claude-opus-4-6"

    # Genius API (lyrics)
    genius_access_token: str = ""

    # Email (Resend API)
    resend_api_key: str = ""
    email_from: str = "Rising Compass <compass@risingcompass.net>"
    approval_email: str = ""
    misread_notify_email: str = ""

    # Site URL for approval links in emails
    site_url: str = "http://localhost:8000"

    # Spotify API (for Lyrical Charger playlist resolution)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Musixmatch API (song search + lyrics for Lyrical Charger)
    musixmatch_api_key: str = ""

    # Lyrical Charger settings
    analyzer_session_ttl: int = 1800  # 30 minutes

    # Cloudflare Turnstile (bot protection — both keys required to activate)
    turnstile_site_key: str = ""
    turnstile_secret: str = ""

    # Audience Vibe — gap threshold that opens an admin review case.
    # Roadmap calls this "TBD"; starting at 25 and tunable from .env.
    vibe_review_threshold: int = 25

    # DO Spaces backup destination
    do_spaces_key: str = ""
    do_spaces_secret: str = ""
    do_spaces_bucket: str = ""
    do_spaces_region: str = "nyc3"
    do_spaces_prefix: str = "rising-compass/daily"
    backup_retention_days: int = 30

    @model_validator(mode="after")
    def _validate_required_secrets(self):
        if not self.rc_admin_key or self.rc_admin_key == "change-me":
            raise ValueError("RC_ADMIN_KEY must be set to a strong secret (not 'change-me')")
        if not self.rc_api_key or self.rc_api_key == "change-me":
            raise ValueError("RC_API_KEY must be set to a strong secret (not 'change-me')")
        if not self.admin_login_url_token or len(self.admin_login_url_token) < 6:
            raise ValueError(
                "ADMIN_LOGIN_URL_TOKEN must be set (>=6 chars). The admin "
                "login page lives at /api/rc-admin-{token}/login; without "
                "this set, the page returns 404."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return json.loads(self.cors_origins)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
