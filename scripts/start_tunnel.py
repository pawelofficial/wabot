"""Start the cloudflared tunnel for run.sh in the background and print its process ID.

On Windows, uvicorn's auto-reload stops the bot with a Ctrl+C sent to the console, and Windows
delivers it to every process in that console: a cloudflared started by run.sh directly died with
every reload, and Telegram then got 530 from Cloudflare. Started here without a console
(CREATE_NO_WINDOW), it doesn't get those Ctrl+Cs; run.sh stops it when it exits.

Usage: python scripts/start_tunnel.py <cloudflared> <port> <log file>
"""

import subprocess
import sys


def main() -> None:
    cloudflared, port, log_file = sys.argv[1:]
    with open(log_file, "w") as log:
        process = subprocess.Popen(
            [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            # Windows: no console, so no console Ctrl+C. Elsewhere: its own session, so the
            # terminal's Ctrl+C doesn't reach it either (run.sh stops it).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=sys.platform != "win32",
        )
    print(process.pid)


if __name__ == "__main__":
    main()
