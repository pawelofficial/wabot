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
    # Meta app ID (App settings → Basic); scripts/set_webhook.py looks it up if unset.
    whatsapp_app_id: str | None = None

    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    # Training plan injected into the prompt, for every trainee until plans live in the db.
    training_plan_file: str | None = "plans/example.txt"
    # Use utils.hardcoded_first_message() instead of the LLM for messages the bot starts.
    hardcoded_first_message: bool = False
    # Your own number (digits only); where the scripts send test messages.
    whatsapp_test_recipient: str | None = None
    # Until trainees have their own timezone (milestone 2).
    default_timezone: str = "Europe/Warsaw"


@lru_cache
def get_settings() -> Settings:
    return Settings()
