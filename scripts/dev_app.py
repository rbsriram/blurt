"""Launch a native window running THIS clone's code (whatever branch is checked out).

Invoked by scripts/dev-app.sh, which sets the env (isolated data dir, port, and the
"blurt dev" window title). It bypasses the normal launcher's macOS-bundle handoff on
purpose, so the dev build never touches or relaunches the installed production app.
Not shipped in the wheel; a dev-only tool.
"""

from blurt.app import create_app
from blurt.config import settings
from blurt.desktop import open_window, serve_in_background

server = serve_in_background(create_app(), settings.host, settings.port)
open_window(
    f"http://{settings.host}:{settings.port}",
    on_closed=lambda: setattr(server, "should_exit", True),
)
