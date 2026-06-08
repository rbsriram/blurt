"""The `blurt` command: boot the app and open it in a native desktop window.

This is the friendly launcher (what `pipx install blurt` exposes). It picks a data
directory for your notes, makes sure the local embedding model is available, starts
the server, and opens Blurt in its own window. The window owns the app's lifecycle:
close it and Blurt is gone, with no browser tab to babysit.

Set BLURT_BROWSER=1 to open a browser tab instead (also the automatic fallback if
the native window backend is unavailable). Plain `python -m blurt` runs the bare
server with no window at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
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


def _want_browser() -> bool:
    return os.environ.get("BLURT_BROWSER", "").lower() in {"1", "true", "yes", "on"}


def _install_app_command() -> None:
    from .installer import install_app, supported

    if not supported():
        print("The double-click app is macOS-only for now. On this system, just run `blurt`.")
        return
    path = install_app()
    print(f"Added blurt to your Applications:\n  {path}")
    print("Open it from Launchpad or Spotlight, or drag it to your dock.")


def _offer_desktop_app() -> None:
    """On the first macOS run, drop Blurt into ~/Applications so it is double-clickable.
    Silent on every later run; never blocks the launch if it fails."""
    from .installer import ensure_installed

    created = ensure_installed()
    if created is not None:
        print(f"Added blurt to your Applications ({created.name}). Look for it in Launchpad.")


def _uninstall_command() -> None:
    """Remove the app and leave the notes alone. Your scratchpad stays where it is; if you
    ever want it gone, delete the folder yourself. The package goes via pip/pipx."""
    from .config import settings
    from .installer import clear_app_marker, remove_app, supported

    if supported():
        removed = remove_app()
        print(f"Removed {removed[0].name} from your Applications." if removed
              else "No blurt app in Applications (already gone).")
    clear_app_marker()

    print(f"\nYour notes are kept, untouched:\n  {settings.export_md_path}")
    print("\nTo finish removing the program:\n  pipx uninstall blurt    (or: pip uninstall blurt)")


def _wait_ready(url: str, tries: int = 80) -> bool:
    for _ in range(tries):
        if _get(f"{url}/api/status", timeout=0.5) is not None:
            return True
        time.sleep(0.3)
    return False


def main() -> None:
    # Resolve the data location first, so subcommands and the marker file see the same
    # paths the running app would.
    os.environ.setdefault("BLURT_DB_PATH", str(_data_dir() / "blurt.db"))

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in {"install-app", "--install-app"}:  # (re)create the double-clickable app
        _install_app_command()
        return
    if arg in {"uninstall", "--uninstall"}:  # remove the app, optionally the data
        _uninstall_command()
        return

    _offer_desktop_app()

    from .app import create_app
    from .config import settings

    url = f"http://{settings.host}:{settings.port}"

    # Already running in another process? Attach a window (or tab) to it and stop.
    if _get(f"{url}/api/status", timeout=0.5) is not None:
        print(f"Blurt is already running at {url}")
        _present(url)
        return

    _ensure_model()

    print(f"Starting Blurt on {url} ...")
    from .desktop import serve_in_background

    server = serve_in_background(create_app(), settings.host, settings.port)
    if not _wait_ready(url):
        print("Blurt did not come up in time. Check the logs above.")
        sys.exit(1)

    _present(url, server)


def _present(url: str, server=None) -> None:
    """Show Blurt: a native window by default, a browser tab on request/fallback.

    Blocks until the user closes the window (or, in browser mode, until Ctrl+C).
    """
    if not _want_browser():
        try:
            from .desktop import open_window

            stop = (lambda: setattr(server, "should_exit", True)) if server else None
            open_window(url, on_closed=stop)
            return
        except Exception as e:  # backend missing or failed: fall back to a browser tab
            print(f"Native window unavailable ({e}); opening your browser instead.")

    webbrowser.open(url)
    if server is None:  # attached to an already-running server; nothing to keep alive
        return
    print("Blurt is running. Press Ctrl+C to stop.")
    try:
        while not server.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        sys.exit(0)


if __name__ == "__main__":
    main()
