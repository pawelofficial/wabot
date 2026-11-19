"""Read or change the running bot's database over HTTP (/admin/db, src/wabot/db_admin.py).

    uv run python scripts/db_admin.py "SELECT id, name FROM trainees"
    uv run python scripts/db_admin.py "UPDATE trainees SET name = :name WHERE id = :id" \
        -p name=Tomasz -p id=3
    uv run python scripts/db_admin.py -f fix.sql            # one statement from a file
    uv run python scripts/db_admin.py --download live.db    # a copy of the whole database

The key is DB_ADMIN_KEY and the address DB_ADMIN_URL (default https://tubealpha.pro), from the
environment or .env. Every statement is committed right away: SELECT first to see what an
UPDATE or DELETE will hit, and --download a backup before bigger changes.
"""

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

DEFAULT_URL = "https://tubealpha.pro"


def setting(name: str, default: str = "") -> str:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    return os.environ.get(name) or dotenv_values(env_file).get(name) or default


def value(text: str):
    """-p values: integers and null as such, everything else a string."""
    if text == "null":
        return None
    try:
        return int(text)
    except ValueError:
        return text


def print_table(columns: list[str], rows: list[list]) -> None:
    cells = [[("NULL" if v is None else str(v)) for v in row] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))


def main() -> int:
    # Polish names print on a Windows console too.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sql", nargs="?", help="one SQL statement")
    parser.add_argument("-f", "--file", help="read the statement from this file")
    parser.add_argument(
        "-p", "--param", action="append", default=[], metavar="NAME=VALUE", help=":NAME in the SQL"
    )
    parser.add_argument("--download", metavar="PATH", help="save a copy of the database here")
    parser.add_argument("--url", default=setting("DB_ADMIN_URL", DEFAULT_URL))
    args = parser.parse_args()

    key = setting("DB_ADMIN_KEY")
    if not key:
        sys.exit("DB_ADMIN_KEY isn't set (environment or .env)")
    headers = {"X-Admin-Key": key}
    base = args.url.rstrip("/")

    if args.download:
        response = httpx.get(f"{base}/admin/db/file", headers=headers, timeout=60)
        response.raise_for_status()
        Path(args.download).write_bytes(response.content)
        print(f"Saved {len(response.content)} bytes to {args.download}")
        return 0

    sql = Path(args.file).read_text(encoding="utf-8") if args.file else args.sql
    if not sql:
        parser.error("give a statement, -f FILE or --download PATH")
    params = {}
    for item in args.param:
        name, sep, text = item.partition("=")
        if not sep:
            parser.error(f"-p {item}: expected NAME=VALUE")
        params[name] = value(text)

    response = httpx.post(
        f"{base}/admin/db/sql", json={"sql": sql, "params": params}, headers=headers, timeout=60
    )
    if response.status_code != 200:
        is_json = "json" in response.headers.get("content-type", "")
        detail = response.json().get("detail") if is_json else ""
        sys.exit(f"{response.status_code}: {detail or response.text}")
    result = response.json()
    if result["columns"]:
        print_table(result["columns"], result["rows"])
        print(f"({result['rowcount']} rows)")
    else:
        print(f"OK, {result['rowcount']} rows changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
