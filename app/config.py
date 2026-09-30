import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Tests point ENCORE_ENV_FILE at /dev/null so a developer's .env can never leak real credentials into a test run.
    model_config = SettingsConfigDict(env_file=os.environ.get("ENCORE_ENV_FILE", ".env"), extra="ignore")

    database_url: str
    frontend_origin: str = "http://localhost:8000"
    serve_frontend: bool = False
    min_lead_days: int = 2
    weekends_ahead: int = 8
    admin_password: str = ""
    session_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
