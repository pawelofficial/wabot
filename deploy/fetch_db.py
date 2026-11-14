"""At start on Cloud Run (deploy/start.sh): download the database from the bucket (DB_BUCKET):
its last copy, gs://<bucket>/wabot.db (db_sync.py uploads it after every change), or on the very
first start the seed, gs://<bucket>/seed/wabot.db (uploaded by hand, docs/DEPLOY.md). Neither:
the migrations then create an empty database.

Usage: python deploy/fetch_db.py /data/wabot.db
"""

import os
import sys
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
OBJECTS = ("wabot.db", "seed/wabot.db")


def main() -> None:
    target = sys.argv[1]
    bucket = os.environ["DB_BUCKET"]
    credentials, _ = google.auth.default(scopes=[SCOPE])
    session = AuthorizedSession(credentials)
    for name in OBJECTS:
        response = session.get(
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}",
            params={"alt": "media"},
            timeout=60,
        )
        if response.status_code == 404:
            continue
        response.raise_for_status()
        with open(target, "wb") as f:
            f.write(response.content)
        print(f"Database from gs://{bucket}/{name} ({len(response.content)} bytes)")
        return
    print(f"No database in gs://{bucket} yet: starting with an empty one")


if __name__ == "__main__":
    main()
