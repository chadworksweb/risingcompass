import json

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    rc_admin_key: str = ""  # REQUIRED — app won't start without it
    database_url: str = "sqlite:///./data/rising_compass.db"
    cors_origins: str = '["http://localhost:3000","http://127.0.0.1:3000","https://risingcompass.net","https://api.risingcompass.net"]'

    # Anthropic API
    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-4-5-20250929"

    # Genius API (lyrics)
    genius_access_token: str = ""

    # Email (Resend API)
    resend_api_key: str = ""
    email_from: str = "Rising Compass <compass@risingcompass.net>"
    approval_email: str = ""

    # Site URL for approval links in emails
    site_url: str = "http://localhost:8000"

    # Spotify API (for analyzer playlist resolution)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Analyzer settings
    analyzer_max_songs: int = 10
    analyzer_session_ttl: int = 1800  # 30 minutes

    @model_validator(mode="after")
    def _validate_required_secrets(self):
        if not self.rc_admin_key or self.rc_admin_key == "change-me":
            raise ValueError("RC_ADMIN_KEY must be set to a strong secret (not 'change-me')")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return json.loads(self.cors_origins)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
