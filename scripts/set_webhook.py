"""Point Meta's WhatsApp webhook at a public URL, e.g. a new cloudflared tunnel.

Does the same as WhatsApp → Configuration → Webhook → Edit in the Meta app dashboard: sets the
Callback URL to <url>/webhook with WHATSAPP_VERIFY_TOKEN and subscribes the `messages` field.
Meta checks the URL right away (GET /webhook), so the bot must be running and reachable through
the URL; until it is, the registration is retried.

Run from the project root (run.sh does this by itself after starting the tunnel):
    uv run python scripts/set_webhook.py https://something.trycloudflare.com
"""

import argparse
import sys
import time

import httpx

from wabot.config import get_settings

# A new trycloudflare.com address takes a few seconds to become reachable. Checking it from this
# machine isn't reliable (a VPN's DNS may not resolve it yet while Meta already can), so Meta's own
# check is the test: registering is retried until Meta reaches the bot.
WAIT_FOR_TUNNEL = 120
RETRY_EVERY = 5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="public base URL of the bot, without /webhook")
    args = parser.parse_args()
    base = args.url.rstrip("/").removesuffix("/webhook")

    settings = get_settings()
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

    deadline = time.monotonic() + WAIT_FOR_TUNNEL
    while True:
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
            error = f"HTTP {response.status_code}: {response.text}" if response.is_error else None
        except httpx.HTTPError as e:
            error = str(e)
        if error is None:
            print(f"Webhook set: {base}/webhook")
            return
        if time.monotonic() > deadline:
            sys.exit(f"Setting the webhook failed (is the bot running and the tunnel up?): {error}")
        time.sleep(RETRY_EVERY)


if __name__ == "__main__":
    main()
