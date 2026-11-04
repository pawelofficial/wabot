import os
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
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
    # False (default): every weight and training report is saved as a new row, so nothing a
    # trainee sent is overwritten – a correction ("sorki, 81") adds a row, and the newest row
    # of a day is the current value. True: one entry per trainee per day, a newer report
    # updates it (entries.py).
    daily_updates_only: bool = False
    # Use utils.hardcoded_first_message() instead of the LLM for messages the bot starts.
    hardcoded_first_message: bool = False
    # Your own number (digits only); where the scripts send test messages.
    whatsapp_test_recipient: str | None = None

    # Signs the dashboard's session cookie; any long random string, kept secret. Without it
    # nobody can sign in, except with the dev login (which then uses a fixed key).
    dashboard_session_secret: str = ""
    # OAuth client ID (Google Cloud console → APIs & Services → Credentials, type "Web
    # application"); shows "Sign in with Google" on the login page. A trainer signs in with the
    # Google account whose email is trainers.email.
    google_client_id: str = ""
    # Local development only: the login page lists the trainers to sign in as, no Google.
    dashboard_dev_login: bool = False

    @model_validator(mode="after")
    def _no_dev_login_in_production(self):
        # Cloud Run sets K_SERVICE. The dev login lets anyone sign in as any trainer.
        if self.dashboard_dev_login and os.environ.get("K_SERVICE"):
            raise ValueError("DASHBOARD_DEV_LOGIN must not be on in production (Cloud Run)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
