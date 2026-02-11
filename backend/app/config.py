from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    rc_admin_key: str = "change-me"
    database_url: str = "sqlite:///./data/rising_compass.db"
    cors_origins: str = '["http://localhost:3000","https://risingcompass.com"]'

    @property
    def cors_origin_list(self) -> List[str]:
        return json.loads(self.cors_origins)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
