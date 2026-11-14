import os
from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Just the database URL, so migrations run without the WhatsApp secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///wabot.db"


class Settings(DatabaseSettings):
    # The messenger the bot runs on: which webhook run.sh registers and where messages the bot
    # starts itself go. Replies always go back where the trainee's message came from, so both
    # webhooks can be live at once. Only the active channel's settings below need values.
    channel: Literal["telegram", "whatsapp"] = "telegram"

    # Telegram: the token @BotFather gives when the bot is created. The webhook's secret is
    # derived from it (telegram.webhook_secret()), so nothing else needs setting up.
    telegram_bot_token: str = ""

    # WhatsApp (Meta Cloud API).
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    # App secret (App settings → Basic) used to check Meta's webhook signature.
    whatsapp_app_secret: str = ""
    # Any string we choose; entered in Meta's webhook configuration.
    whatsapp_verify_token: str = ""
    graph_api_version: str = "v25.0"
    # Meta app ID (App settings → Basic); scripts/set_webhook.py looks it up if unset.
    whatsapp_app_id: str | None = None

    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    # Voice messages are turned into text by this OpenAI model (voice.py) before anything else
    # reads them; longer ones than voice_max_seconds get a fixed reply instead (cost, and a
    # report is a few sentences).
    openai_transcribe_model: str = "gpt-4o-mini-transcribe"
    voice_max_seconds: int = 300
    # What chat history a reply call gets. "marked": the whole recent history, with a marker
    # before the first message still waiting (not yet handled by a reply, conversation.Turn); the
    # LLM extracts only what's below it. "new_only": only the messages from the first waiting one
    # on (the code drops the rest) – use it if the
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
    # Your own number (digits only): the trainee scripts/add_trainee.py registers by default and
    # where the scripts send test messages (on Telegram, once that number has connected).
    whatsapp_test_recipient: str | None = None

    # Cloud Run (docs/DEPLOY.md). The reply to a message (the LLM call, saving, sending) runs
    # before the webhook answers instead of after it: Cloud Run only gives a service CPU while it
    # handles a request, and the webhook's request is that time. Telegram waits for the answer.
    reply_in_request: bool = False
    # The bucket the SQLite database lives in between starts (db_sync.py): downloaded at start
    # (deploy/start.sh), uploaded after every request that changed it. Empty: a local file only.
    db_bucket: str = ""

    # How much the bot logs (logs.py): DEBUG adds each message's text, the model's instructions
    # and its raw answer; INFO what happened to each message; WARNING only problems.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # The log file, besides the console; it rotates (logs.py), so it stays a few MB. Empty: no
    # file (production, where the console goes to Cloud Logging).
    log_file: str = "logs/wabot.log"

    # Signs the dashboard's session cookie; any long random string, kept secret. Without it
    # nobody can sign in, except with the dev login (which then uses a fixed key).
    dashboard_session_secret: str = ""
    # OAuth client ID (Google Cloud console → APIs & Services → Credentials, type "Web
    # application"); shows "Sign in with Google" on the login page. A trainer signs in with the
    # Google account whose email is trainers.email.
    google_client_id: str = ""
    # Local development only: the login page lists the trainers to sign in as, no Google.
    dashboard_dev_login: bool = False
    # The dashboard's admins: Google emails, comma-separated. A trainer signed in with one of
    # them also gets Trenerzy (adding trainers, dashboard/trainer_pages.py).
    admin_emails: str = ""

    @field_validator("log_level", mode="before")
    @classmethod
    def _any_case(cls, value):
        # LOG_LEVEL=debug works as well as DEBUG.
        return value.upper() if isinstance(value, str) else value

    def is_admin(self, email: str | None) -> bool:
        """Whether a trainer with this (lowercase) email is an admin (ADMIN_EMAILS)."""
        admins = {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}
        return email is not None and email.lower() in admins

    def missing_for_channel(self) -> list[str]:
        """The active channel's settings that are empty, as .env names."""
        if self.channel == "telegram":
            needed = {"TELEGRAM_BOT_TOKEN": self.telegram_bot_token}
        else:
            needed = {
                "WHATSAPP_TOKEN": self.whatsapp_token,
                "WHATSAPP_PHONE_NUMBER_ID": self.whatsapp_phone_number_id,
                "WHATSAPP_APP_SECRET": self.whatsapp_app_secret,
                "WHATSAPP_VERIFY_TOKEN": self.whatsapp_verify_token,
            }
        return [name for name, value in needed.items() if not value]

    @model_validator(mode="after")
    def _no_dev_login_in_production(self):
        # Cloud Run sets K_SERVICE. The dev login lets anyone sign in as any trainer.
        if self.dashboard_dev_login and os.environ.get("K_SERVICE"):
            raise ValueError("DASHBOARD_DEV_LOGIN must not be on in production (Cloud Run)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
