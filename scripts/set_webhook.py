"""Point Meta's WhatsApp webhook at a public URL, e.g. a new cloudflared tunnel.

Does the same as WhatsApp → Configuration → Webhook → Edit in the Meta app dashboard: sets the
Callback URL to <url>/webhook with WHATSAPP_VERIFY_TOKEN and subscribes the `messages` field.
Meta checks the URL right away, so the bot must be running and reachable through the URL.

Run from the project root (run.sh does this by itself after starting the tunnel):
    uv run python scripts/set_webhook.py https://something.trycloudflare.com
"""

import argparse
import sys
import time

import httpx

from wabot.config import get_settings

# A new trycloudflare.com address takes a few seconds to become reachable.
WAIT_FOR_TUNNEL = 60


def wait_until_reachable(url: str) -> bool:
    deadline = time.monotonic() + WAIT_FOR_TUNNEL
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


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

    if not wait_until_reachable(base):
        sys.exit(f"{base} isn't reachable – is the bot running and the tunnel up?")

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
    if response.is_error:
        sys.exit(f"Setting the webhook failed: HTTP {response.status_code}: {response.text}")
    print(f"Webhook set: {base}/webhook")


if __name__ == "__main__":
    main()
