from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    rc_admin_key: str = "change-me"
    database_url: str = "sqlite:///./data/rising_compass.db"
    cors_origins: str = '["http://localhost:3000","https://risingcompass.com"]'

    # Anthropic API
    anthropic_api_key: str = ""

    # Genius API (lyrics)
    genius_access_token: str = ""

    # Email (Gmail SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    approval_email: str = ""

    # Site URL for approval links in emails
    site_url: str = "http://localhost:8000"

    @property
    def cors_origin_list(self) -> List[str]:
        return json.loads(self.cors_origins)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
