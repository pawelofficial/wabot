from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Just the database URL, so migrations run without the WhatsApp secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///wabot.db"


class Settings(DatabaseSettings):
    whatsapp_token: str
    whatsapp_phone_number_id: str
    # App secret (App settings → Basic) used to check Meta's webhook signature.
    whatsapp_app_secret: str
    # Any string we choose; entered in Meta's webhook configuration.
    whatsapp_verify_token: str
    graph_api_version: str = "v25.0"

    anthropic_api_key: str
    claude_model: str = "claude-opus-5-5"
    # Until trainees have their own timezone (milestone 2).
    default_timezone: str = "Europe/Warsaw"


@lru_cache
def get_settings() -> Settings:
    return Settings()
