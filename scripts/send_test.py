"""Send a WhatsApp template message through the Meta Cloud API.

Scratch script for milestone 0. Reads settings from .env in the project root:

    WHATSAPP_TOKEN=...            # temporary access token from API Setup
    WHATSAPP_PHONE_NUMBER_ID=...  # "From" test number's Phone number ID
    WHATSAPP_TEST_RECIPIENT=...   # your number, digits only, e.g. 48123456789

Usage:
    python scripts/send_test.py [template_name] [language_code]
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

GRAPH_API_VERSION = "v25.0"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("'\"")
    return env


def send_template(token: str, phone_number_id: str, to: str, template: str, language: str) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": template, "language": {"code": language}},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main() -> None:
    template = sys.argv[1] if len(sys.argv) > 1 else "jaspers_market_plain_text_v1"
    language = sys.argv[2] if len(sys.argv) > 2 else "en_US"

    env = load_env(ENV_FILE)
    try:
        result = send_template(
            env["WHATSAPP_TOKEN"],
            env["WHATSAPP_PHONE_NUMBER_ID"],
            env["WHATSAPP_TEST_RECIPIENT"],
            template,
            language,
        )
    except urllib.error.HTTPError as e:
        # Meta returns a JSON error body explaining what went wrong.
        print(f"HTTP {e.code}: {e.read().decode()}")
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
