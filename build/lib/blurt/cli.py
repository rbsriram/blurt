"""The `blurt` command: boot the app and open it in your browser.

This is the friendly launcher (what `pipx install blurt` exposes). It picks a data
directory for your notes, makes sure the local embedding model is available, opens
your browser, and starts the server. Plain `python -m blurt` runs the bare server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

EMBED_MODEL = "nomic-embed-text"


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / "blurt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, timeout: float = 1.5) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _ensure_model() -> None:
    """If Ollama is up but the embedding model is missing, pull it (one time)."""
    tags = _get("http://localhost:11434/api/tags")
    if tags is None:
        print("Blurt needs Ollama for its semantic search.")
        print("  Install it from https://ollama.com/download, start it, then run `blurt` again.")
        print("  (Blurt still runs without it; exact-text search works, the smart peek does not.)\n")
        return
    if EMBED_MODEL.encode() in tags:
        return
    if not _which("ollama"):
        print(f"Ollama is running but the model is missing. Run: ollama pull {EMBED_MODEL}\n")
        return
    print(f"Pulling the embedding model ({EMBED_MODEL}, ~270MB, one time)...")
    subprocess.run(["ollama", "pull", EMBED_MODEL], check=False)


def _which(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


def main() -> None:
    os.environ.setdefault("BLURT_DB_PATH", str(_data_dir() / "blurt.db"))

    from .app import create_app
    from .config import settings

    url = f"http://{settings.host}:{settings.port}"

    # Already running? Just open the browser and stop.
    if _get(f"{url}/api/status", timeout=0.5) is not None:
        print(f"Blurt is already running at {url}")
        webbrowser.open(url)
        return

    _ensure_model()

    def _open_when_ready() -> None:
        for _ in range(80):
            if _get(f"{url}/api/status", timeout=0.5) is not None:
                webbrowser.open(url)
                return
            time.sleep(0.3)

    threading.Thread(target=_open_when_ready, daemon=True).start()

    print(f"Starting Blurt on {url} ...  (Ctrl+C to stop)")
    import uvicorn

    try:
        uvicorn.run(create_app(), host=settings.host, port=settings.port, log_level="warning")
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
