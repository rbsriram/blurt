"""Create a double-clickable Blurt.app so Blurt lives in Applications, not a terminal.

macOS only for now. The bundle is a thin wrapper: its launcher execs the *currently
installed* interpreter (`sys.executable`) with `-m blurt.cli`. So it always launches the
exact Blurt the user installed (pipx, pip, or the bootstrap venv), from anywhere, with no
dependency on a source checkout. The icon ships inside the package, so any install can
stamp it into the bundle.
"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

APP_NAME = "blurt.app"
_LEGACY_APP_NAME = "Blurt.app"  # pre-1.1 bundles used a capitalized name; clean them up

_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>blurt</string>
  <key>CFBundleDisplayName</key><string>blurt</string>
  <key>CFBundleIdentifier</key><string>com.rbsriram.blurt</string>
  <key>CFBundleExecutable</key><string>blurt</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundleIconFile</key><string>Blurt</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
"""


def _user_apps() -> Path:
    """Per-user Applications folder: writable without admin, shows in Launchpad/Spotlight."""
    d = Path.home() / "Applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_path() -> Path:
    return _user_apps() / APP_NAME


def is_installed() -> bool:
    return app_path().exists()


def supported() -> bool:
    return sys.platform == "darwin"


def install_app() -> Path:
    """Write (or overwrite) blurt.app into ~/Applications and return its path."""
    if not supported():
        raise RuntimeError("The double-click app is macOS-only for now; run `blurt` instead.")

    import shutil

    from . import __version__

    # Remove a pre-1.1 capitalized bundle so the dock/Launchpad do not show two Blurts.
    legacy = _user_apps() / _LEGACY_APP_NAME
    if legacy.exists():
        shutil.rmtree(legacy, ignore_errors=True)

    app = app_path()
    contents = app / "Contents"
    macos = contents / "MacOS"
    res = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)

    (contents / "Info.plist").write_text(_INFO_PLIST.format(version=__version__))

    launcher = macos / "blurt"
    # Quote the interpreter path; it can contain spaces (e.g. under "Application Support").
    launcher.write_text(f'#!/bin/bash\nexec "{sys.executable}" -m blurt.cli\n')
    launcher.chmod(0o755)

    _copy_icon(res / "Blurt.icns")
    _record_app_added()
    return app


def remove_app() -> list[Path]:
    """Remove the Blurt.app bundle(s) we created. Returns the paths removed."""
    import shutil

    removed = []
    for name in (APP_NAME, _LEGACY_APP_NAME):
        p = _user_apps() / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)
    return removed


def _copy_icon(dest: Path) -> None:
    """Stamp the packaged icon into the bundle; a missing icon is not fatal."""
    try:
        data = resources.files("blurt").joinpath("assets/Blurt.icns").read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return
    dest.write_bytes(data)


def _marker_path() -> Path:
    """A flag recording that blurt has added its app at least once. Lives beside the DB."""
    from .config import settings

    return Path(settings.db_path).parent / ".app-added"


def _record_app_added() -> None:
    try:
        m = _marker_path()
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("1")
    except OSError:
        pass


def clear_app_marker() -> None:
    """Forget that the app was added, so a later fresh install will add it again."""
    try:
        _marker_path().unlink()
    except OSError:
        pass


def ensure_installed() -> Path | None:
    """First-run convenience: create the app the first time only. Returns the path just
    once (so the caller can announce it). If the app was added before and the user has
    since trashed it, this does NOT resurrect it, deleting it is a real choice. No-op on
    non-macOS. Use `blurt install-app` to add it back deliberately."""
    if not supported() or is_installed() or _marker_path().exists():
        return None
    try:
        return install_app()
    except Exception:
        return None  # never let a packaging hiccup block the actual app launch
