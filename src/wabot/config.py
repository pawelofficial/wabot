from functools import lru_cache
from typing import Literal

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
    # What chat history a reply call gets. "marked": the whole recent history, with a marker
    # after the bot's last message; the LLM extracts only what's below it. "new_only": only the
    # trainee's messages since the bot's last message (the code drops the rest) – use it if the
    # LLM keeps re-extracting reports it already handled, at the cost of less context: a
    # correction of a training can't see the earlier report, so it can replace the whole day
    # with just the corrected part.
    history_mode: Literal["marked", "new_only"] = "marked"
    # Use utils.hardcoded_first_message() instead of the LLM for messages the bot starts.
    hardcoded_first_message: bool = False
    # Your own number (digits only); where the scripts send test messages.
    whatsapp_test_recipient: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
