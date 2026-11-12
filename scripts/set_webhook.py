"""Point the active channel's webhook (CHANNEL in .env) at a public URL, e.g. a new cloudflared
tunnel.

Telegram (CHANNEL=telegram): sets the bot's webhook to <url>/telegram/webhook with its secret
(telegram.set_webhook()) and prints the link trainees open, t.me/<bot>.

WhatsApp (CHANNEL=whatsapp): does the same as WhatsApp → Configuration → Webhook → Edit in the
Meta app dashboard: sets the Callback URL to <url>/webhook with WHATSAPP_VERIFY_TOKEN and
subscribes the `messages` field. Meta checks the URL right away (GET /webhook), so the bot must
be running and reachable through the URL.

Until it works, the registration is retried (a new tunnel takes a moment to be reachable).

Run from the project root (run.sh does this by itself after starting the tunnel):
    uv run python scripts/set_webhook.py https://something.trycloudflare.com
"""

import argparse
import sys
import time

import httpx

from wabot import telegram
from wabot.config import Settings, get_settings

# A new trycloudflare.com address takes a few seconds to become reachable. Checking it from this
# machine isn't reliable (a VPN's DNS may not resolve it yet while Meta or Telegram already
# can), so their own check is the test: registering is retried until it passes.
WAIT_FOR_TUNNEL = 120
RETRY_EVERY = 5


def retry(register) -> None:
    """Call register() (returns an error text, or None when it worked) until it works or
    WAIT_FOR_TUNNEL runs out."""
    deadline = time.monotonic() + WAIT_FOR_TUNNEL
    while (error := register()) is not None:
        if time.monotonic() > deadline:
            sys.exit(f"Setting the webhook failed (is the bot running and the tunnel up?): {error}")
        time.sleep(RETRY_EVERY)


def set_telegram(settings: Settings, base: str) -> None:
    def register() -> str | None:
        try:
            telegram.set_webhook(settings, f"{base}/telegram/webhook")
        except telegram.TelegramError as e:
            return str(e)
        return None

    retry(register)
    print(f"Webhook set: {base}/telegram/webhook")
    try:
        print(f"Trainees open the bot at https://t.me/{telegram.bot_username(settings)}")
    except telegram.TelegramError:
        pass


def set_whatsapp(settings: Settings, base: str) -> None:
    graph = f"https://graph.facebook.com/{settings.graph_api_version}"
    app_id = settings.whatsapp_app_id
    if not app_id:
        # The app the access token belongs to.
        response = httpx.get(f"{graph}/app", params={"access_token": settings.whatsapp_token})
        if response.is_error:
            sys.exit(
                f"Can't look up the app ID (HTTP {response.status_code}: {response.text}). "
                "Set WHATSAPP_APP_ID in .env, or renew WHATSAPP_TOKEN."
            )
        app_id = response.json()["id"]

    def register() -> str | None:
        try:
            response = httpx.post(
                f"{graph}/{app_id}/subscriptions",
                data={
                    "object": "whatsapp_business_account",
                    "callback_url": f"{base}/webhook",
                    "verify_token": settings.whatsapp_verify_token,
                    "fields": "messages",
                    # App access token: app ID and app secret.
                    "access_token": f"{app_id}|{settings.whatsapp_app_secret}",
                },
                timeout=30,
            )
        except httpx.HTTPError as e:
            return str(e)
        return f"HTTP {response.status_code}: {response.text}" if response.is_error else None

    retry(register)
    print(f"Webhook set: {base}/webhook")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="public base URL of the bot, without /webhook")
    args = parser.parse_args()
    base = args.url.rstrip("/").removesuffix("/webhook").removesuffix("/telegram")

    settings = get_settings()
    if missing := settings.missing_for_channel():
        sys.exit(f"CHANNEL={settings.channel} needs {', '.join(missing)} in .env")
    if settings.channel == "telegram":
        set_telegram(settings, base)
    else:
        set_whatsapp(settings, base)


if __name__ == "__main__":
    main()
