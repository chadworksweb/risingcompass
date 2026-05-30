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
    rc_lyrics_supply_key: str = ""  # OPTIONAL — service token for POST /api/admin/agent/drafts/{ref}/songs/{id}/lyrics. Lets terminal scripts supply lyrics without the browser session cookie. Distinct from other service keys so a leak is scoped to lyrics supply only.

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
    # Domain scope for the rc_admin_session cookie. In prod set to
    # "risingcompass.net" so the one cookie is valid on both the root host
    # and api.risingcompass.net (same-site subdomains), giving one shared
    # admin login across the Site Admin (root) and API Admin (api.) sections.
    # Leave unset locally so the cookie stays host-only on localhost.
    admin_cookie_domain: str = ""
    # DigitalOcean Managed Postgres DSN, reached via the PgBouncer pool.
    # Form: postgresql+psycopg://USER:PASS@HOST:25061/rc-pool?sslmode=require
    # Local dev tunnels port 25061 through the droplet, so HOST is 127.0.0.1.
    database_url: str = "sqlite:///./data/rising_compass.db"
    # Direct (session-mode) DSN used ONLY by pg_dump in services/backup.py.
    # Must point at the direct connection (port 25060 / database defaultdb),
    # NOT the PgBouncer pool -- transaction pooling breaks pg_dump. Falls back
    # to database_url if unset.
    backup_database_url: str = ""
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
    ether_audit_notify_email: str = ""  # falls back to misread_notify_email when unset
    admin_alert_email: str = ""  # admin inbox for activity heartbeat + moderation alerts (subject prefixed [RC-ACTIVITY] / [RC-MOD])

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

    # Stripe (Lyrical Charger donations). Same Stripe account as chadlewine.com,
    # but a separate webhook endpoint with its own signing secret.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Distinct webhook secret for Stripe Identity events. Keeps a leaked
    # donation-webhook signing key from forging Tier 2 verification events
    # (and vice versa). Set when the Identity webhook endpoint is created
    # in the Stripe dashboard.
    stripe_identity_webhook_secret: str = ""
    # URL the user lands on after the Stripe-hosted verification flow.
    # Defaults to /account/ on the frontend.
    stripe_identity_return_url: str = ""

    # Billing (monetization M1-M6). Subscription + credit-pack Stripe price
    # IDs and a dedicated webhook signing secret so a leaked donation or
    # identity webhook key can't forge billing events (and vice versa).
    stripe_billing_webhook_secret: str = ""
    stripe_price_plus: str = ""        # monthly subscription, Plus tier
    stripe_price_pro: str = ""         # monthly subscription, Pro tier
    stripe_price_pack_25: str = ""     # one-time credit pack, 25 credits
    stripe_price_pack_100: str = ""    # one-time credit pack, 100 credits
    stripe_price_pack_300: str = ""    # one-time credit pack, 300 credits
    # Where to send the user after a successful Checkout. Falls back to
    # /account/?billing=success on site_url.
    stripe_billing_return_url: str = ""

    # PostHog server-side analytics (revenue + async album events). Same
    # project as the frontend snippet; the project API key (phc_...) is what
    # the Python SDK uses to ingest. Unset -> server-side capture is a no-op
    # (e.g. local dev), so the rest of billing/album work is unaffected.
    posthog_api_key: str = ""
    posthog_host: str = "https://us.i.posthog.com"

    # Audience Vibe — gap threshold that opens an admin review case.
    # Roadmap calls this "TBD"; starting at 25 and tunable from .env.
    vibe_review_threshold: int = 25

    # Clerk (Public Participation: Tier 1 auth — email + phone). The middleware
    # verifies JWTs against the JWKS endpoint and trusts Clerk's verification
    # claims for email + phone. All OAuth providers are disabled in the Clerk
    # dashboard; Twilio Lookup carrier filter is required to be ON so VoIP /
    # disposable numbers don't defeat ban-stickiness.
    clerk_publishable_key: str = ""
    clerk_secret_key: str = ""
    clerk_jwks_url: str = ""  # e.g. https://<frontend-api>.clerk.accounts.dev/.well-known/jwks.json
    clerk_authorized_party: str = ""  # e.g. https://risingcompass.net — rejected if azp claim mismatches

    # Prose provenance anchoring (societal_effects_prose). Ships DARK
    # (provenance_enabled=False) until the public anchor repo + write creds and
    # the `ots` CLI are provisioned on the server. When on, the sweep cron
    # publishes hash-only records to provenance_repo_path (a pre-cloned,
    # write-authenticated working copy of the public provenance repo) and
    # OpenTimestamps each batch; the upgrade cron confirms them on Bitcoin.
    # Fail-soft and off the calibration hot path -- never blocks a calibration.
    provenance_enabled: bool = False
    provenance_repo_path: str = ""  # local working clone of the public provenance repo
    provenance_jsonl_name: str = "societal-prose.jsonl"  # cumulative human-readable log within the repo
    provenance_ots_bin: str = "ots"  # opentimestamps-client CLI (on PATH via requirements)
    provenance_git_author: str = "rc-provenance <provenance@risingcompass.net>"
    # Service token for the provenance cron endpoints (X-Provenance-Cron-Key).
    # Distinct from the other cron lanes so a leak stays scoped to provenance.
    rc_provenance_cron_key: str = ""

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
