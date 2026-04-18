import json

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    rc_admin_key: str = ""  # REQUIRED — app won't start without it
    rc_api_key: str = ""  # REQUIRED — consumer API key for public endpoints
    database_url: str = "sqlite:///./data/rising_compass.db"
    turso_auth_token: str = ""  # required when database_url is libsql://...
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

    # Site URL for approval links in emails
    site_url: str = "http://localhost:8000"

    # Spotify API (for Lyrical Charger playlist resolution)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Musixmatch API (song search + lyrics for Lyrical Charger)
    musixmatch_api_key: str = ""

    # Lyrical Charger settings
    analyzer_session_ttl: int = 1800  # 30 minutes

    @model_validator(mode="after")
    def _validate_required_secrets(self):
        if not self.rc_admin_key or self.rc_admin_key == "change-me":
            raise ValueError("RC_ADMIN_KEY must be set to a strong secret (not 'change-me')")
        if not self.rc_api_key or self.rc_api_key == "change-me":
            raise ValueError("RC_API_KEY must be set to a strong secret (not 'change-me')")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return json.loads(self.cors_origins)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
