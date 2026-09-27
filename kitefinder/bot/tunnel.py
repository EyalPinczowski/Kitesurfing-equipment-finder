"""A free public HTTPS address for the Mini App: a Cloudflare "quick tunnel" (no account).

`cloudflared tunnel --url http://localhost:PORT` prints a https://….trycloudflare.com address;
it changes each start, so the bot points its menu button at the new one every time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading

TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def parse_tunnel_url(line: str) -> str | None:
    m = TUNNEL_URL.search(line or "")
    return m.group(0) if m else None


def start_tunnel(
    port: int, binary: str = "cloudflared", timeout: float = 60.0, popen=subprocess.Popen
) -> tuple[subprocess.Popen, str]:
    """Starts cloudflared and returns (process, public URL). Raises RuntimeError on failure."""
    if popen is subprocess.Popen and shutil.which(binary) is None:
        raise RuntimeError("cloudflared is not installed — in Termux: pkg install cloudflared")
    proc = popen(
        [binary, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    found: list[str] = []
    ready = threading.Event()

    def read_logs():
        # keeps reading for as long as cloudflared runs: a full pipe would stall the tunnel
        for line in proc.stdout:
            if not found and (url := parse_tunnel_url(line)):
                found.append(url)
                ready.set()
        ready.set()  # cloudflared exited

    threading.Thread(target=read_logs, daemon=True).start()
    if ready.wait(timeout) and found:
        return proc, found[0]
    proc.terminate()
    raise RuntimeError("cloudflared did not report a tunnel address")
