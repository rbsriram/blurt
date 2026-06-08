"""Run Blurt as a native desktop window instead of a browser tab.

The server is the same FastAPI app; this just runs it in a background thread and
points a native OS webview at it (Cocoa/WebKit on macOS, GTK/WebKit on Linux).
The window owns the app's lifecycle: close it and Blurt is gone, like any app,
with no stray browser tab to babysit.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import uvicorn

_MIN_W, _MIN_H = 480, 560


def serve_in_background(app, host: str, port: int) -> uvicorn.Server:
    """Start the server in a daemon thread and return the running Server.

    Signal handlers are disabled because uvicorn can only install them on the
    main thread, and the main thread is reserved for the GUI event loop.
    """
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    threading.Thread(target=server.run, daemon=True).start()
    return server


def open_window(url: str, on_closed=None) -> None:
    """Open a native window at `url` and block until it is closed.

    The window sizes itself to the current screen on first run and then remembers
    whatever size and position you leave it at, like any native app. A size carried
    over from a big external monitor is clamped to fit a smaller laptop screen.
    """
    import webview

    _brand_macos_app()
    geom = _load_geometry()
    api = _JsApi()
    window = webview.create_window(
        "blurt",
        url,
        width=geom["width"],
        height=geom["height"],
        x=geom.get("x"),
        y=geom.get("y"),
        min_size=(_MIN_W, _MIN_H),
        js_api=api,
    )
    api._window = window  # set after creation: the dialog is only called on user action

    # Track size/position as they change and persist on close, so the next launch
    # reopens exactly where you left it.
    state = dict(geom)
    window.events.resized += lambda w, h: state.update(width=int(w), height=int(h))
    window.events.moved += lambda x, y: state.update(x=int(x), y=int(y))
    window.events.closing += lambda: _save_geometry(state)
    _install_menu_cleanup(window)
    _install_dock_behavior(window, state)

    webview.start()
    if on_closed is not None:
        on_closed()


def _install_menu_cleanup(window) -> None:
    """Build blurt's macOS menu bar once the window is shown. AppKit requires menu work
    on the main thread, so the build is deferred there via AppHelper."""
    if sys.platform != "darwin":
        return
    done = {"ran": False}

    def _schedule(*_):
        if done["ran"]:
            return
        done["ran"] = True
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_build_menu_bar, window)
        except Exception:
            pass

    window.events.shown += _schedule


# Holds the app delegate so it is not garbage collected while NSApp references it weakly.
_app_delegate = None


def _install_dock_behavior(window, geom_state) -> None:
    """Make the red close button behave like a normal Mac app: it hides the window to the
    dock instead of quitting. The app keeps running; clicking the dock icon brings the
    window back, and Cmd+Q (or blurt > Quit) actually quits. macOS only."""
    if sys.platform != "darwin":
        return
    flags = {"quitting": False}

    def _on_closing():
        # `closing` fires on the red X / Cmd+W. Unless we are really quitting, cancel the
        # close (return False) and just hide the window, so the app stays alive in the dock.
        if flags["quitting"]:
            return None
        window.hide()
        return False

    window.events.closing += _on_closing

    def _schedule(*_):
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_apply_app_delegate, window, geom_state, flags)
        except Exception:
            pass

    window.events.shown += _schedule


def _apply_app_delegate(window, geom_state, flags) -> None:
    """Install an NSApplication delegate that reopens the window on a dock click and lets
    Cmd+Q quit. Runs on the main thread. Replaces pywebview's minimal delegate, replicating
    the two methods it provides."""
    try:
        import AppKit
        import objc
        from Foundation import NSObject

        global _app_delegate

        Delegate = globals().get("_BlurtAppDelegate")
        if Delegate is None:

            class _BlurtAppDelegate(NSObject):
                def initWithWindow_state_flags_(self, win, st, fl):
                    self = objc.super(_BlurtAppDelegate, self).init()
                    if self is None:
                        return None
                    self._win = win
                    self._state = st
                    self._flags = fl
                    return self

                def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, has_visible):
                    if not has_visible:  # dock click while the window is hidden -> show it
                        self._win.show()
                    return True

                def applicationShouldTerminateAfterLastWindowClosed_(self, _app):
                    return False  # hiding the window is not quitting

                def applicationShouldTerminate_(self, _app):
                    self._flags["quitting"] = True
                    _save_geometry(self._state)  # remember size/position on real quit too
                    return AppKit.NSTerminateNow

                def applicationSupportsSecureRestorableState_(self, _app):
                    return True

            Delegate = _BlurtAppDelegate
            globals()["_BlurtAppDelegate"] = Delegate

        delegate = Delegate.alloc().initWithWindow_state_flags_(window, geom_state, flags)
        _app_delegate = delegate  # pin it
        AppKit.NSApplication.sharedApplication().setDelegate_(delegate)
    except Exception:
        pass  # if this fails, the window simply quits on close, as before


# Keeps the menu's action controller alive for the process lifetime; NSMenu holds only a
# weak target, so without this reference the actions would crash once Python GC'd it.
_menu_controller = None


class _JsApi:
    """Exposed to the web UI as `window.pywebview.api`. Only for things the browser
    sandbox cannot do, currently: picking a real folder path for the notes location."""

    def __init__(self):
        self._window = None

    def pick_folder(self):
        """Show a native folder chooser; return the chosen absolute path, or None."""
        import webview

        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None


def _eval_js_async(window, code: str) -> None:
    """Run JS in the web view from a background thread.

    Menu actions fire on the main (UI) thread, and pywebview's evaluate_js blocks that
    thread waiting on the web view, which deadlocks the whole app. Handing the call to a
    worker thread lets the main thread keep pumping the UI, so the JS actually runs.
    """
    threading.Thread(target=lambda: window.evaluate_js(code), daemon=True).start()


def _build_menu_bar(window) -> None:
    """Replace pywebview's generic default menu with a conventional, useful one.

    Follows the standard macOS layout (App / Edit / View / Window / Help) instead of
    pywebview's Python 'About' panel and 'Services' noise. App-specific items reuse the
    front end's own features over the JS bridge rather than reinventing them.
    """
    try:
        import webbrowser

        import AppKit
        import objc
        from Foundation import NSObject

        from . import __version__

        global _menu_controller

        cmd = AppKit.NSEventModifierFlagCommand
        shift = AppKit.NSEventModifierFlagShift
        option = AppKit.NSEventModifierFlagOption
        control = AppKit.NSEventModifierFlagControl

        Controller = globals().get("_BlurtMenuController")
        if Controller is None:

            class _BlurtMenuController(NSObject):
                def initWithWindow_(self, win):
                    self = objc.super(_BlurtMenuController, self).init()
                    if self is None:
                        return None
                    self._win = win
                    return self

                def about_(self, _sender):
                    # A native About panel, but populated with blurt's identity, not the
                    # host Python's (which is what the default panel would show).
                    # "Version" is the parenthetical build number; blank it so the panel
                    # shows just "Version 1.1.0", not the host Python's build.
                    opts = {
                        "ApplicationName": "blurt",
                        "ApplicationVersion": __version__,
                        "Version": "",
                    }
                    try:
                        from importlib import resources

                        ref = resources.files("blurt").joinpath("static/blurt-icon.png")
                        with resources.as_file(ref) as fp:
                            icon = AppKit.NSImage.alloc().initWithContentsOfFile_(str(fp))
                        if icon is not None:
                            opts["ApplicationIcon"] = icon
                    except Exception:
                        pass
                    AppKit.NSApplication.sharedApplication().orderFrontStandardAboutPanelWithOptions_(
                        opts
                    )

                def help_(self, _sender):
                    _eval_js_async(self._win, "window.__blurtHelp && window.__blurtHelp()")

                def theme_(self, _sender):
                    _eval_js_async(self._win, "window.__blurtTheme && window.__blurtTheme()")

                def settings_(self, _sender):
                    _eval_js_async(self._win, "window.__blurtSettings && window.__blurtSettings()")

                def open_(self, _sender):
                    # Open the always-current scratchpad.md in the user's default app. If it
                    # does not exist yet (no notes saved), open its folder instead.
                    import subprocess

                    from .config import settings

                    path = settings.export_md_path
                    subprocess.run(["open", str(path if path.exists() else path.parent)], check=False)

                def github_(self, _sender):
                    webbrowser.open("https://github.com/rbsriram/blurt")

            Controller = _BlurtMenuController
            globals()["_BlurtMenuController"] = Controller

        controller = Controller.alloc().initWithWindow_(window)
        _menu_controller = controller  # pin it

        app = AppKit.NSApplication.sharedApplication()
        main = AppKit.NSMenu.alloc().init()

        def submenu(title):
            item = AppKit.NSMenuItem.alloc().init()
            menu = AppKit.NSMenu.alloc().initWithTitle_(title)
            item.setSubmenu_(menu)
            main.addItem_(item)
            return menu

        def add(menu, title, action, key="", target=None, mods=None):
            item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
            if target is not None:
                item.setTarget_(target)
            if mods is not None:
                item.setKeyEquivalentModifierMask_(mods)
            return item

        def sep(menu):
            menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # App menu (its title is ignored; macOS shows CFBundleName, i.e. "blurt").
        app_menu = submenu("blurt")
        add(app_menu, "About blurt", "about:", target=controller)
        sep(app_menu)
        add(app_menu, "Settings…", "settings:", ",", target=controller)  # ⌘, by convention
        sep(app_menu)
        add(app_menu, "Hide blurt", "hide:", "h")
        add(app_menu, "Hide Others", "hideOtherApplications:", "h", mods=cmd | option)
        add(app_menu, "Show All", "unhideAllApplications:")
        sep(app_menu)
        add(app_menu, "Quit blurt", "terminate:", "q")

        # File: your notes ARE scratchpad.md (always current); this opens it.
        file_menu = submenu("File")
        add(file_menu, "Open scratchpad", "open:", "o", target=controller)

        # Edit: the standard text-editing items, so typing, copy/paste and undo work.
        edit = submenu("Edit")
        add(edit, "Undo", "undo:", "z")
        add(edit, "Redo", "redo:", "z", mods=cmd | shift)
        sep(edit)
        add(edit, "Cut", "cut:", "x")
        add(edit, "Copy", "copy:", "c")
        add(edit, "Paste", "paste:", "v")
        add(edit, "Select All", "selectAll:", "a")

        # View: blurt's own theme toggle plus standard full screen.
        view = submenu("View")
        add(view, "Dark / Light", "theme:", target=controller)
        sep(view)
        add(view, "Enter Full Screen", "toggleFullScreen:", "f", mods=cmd | control)

        # Window: the conventional minimize/zoom; macOS manages the rest.
        window_menu = submenu("Window")
        add(window_menu, "Minimize", "performMiniaturize:", "m")
        add(window_menu, "Zoom", "performZoom:")
        app.setWindowsMenu_(window_menu)

        # Help: opens blurt's in-app keyboard cheatsheet, plus a link to the project.
        # setHelpMenu_ registers it as the standard Help menu (macOS adds its own search
        # field, which is the expected convention).
        help_menu = submenu("Help")
        add(help_menu, "blurt Help", "help:", "?", target=controller)
        add(help_menu, "blurt on GitHub", "github:", target=controller)
        app.setHelpMenu_(help_menu)

        app.setMainMenu_(main)
    except Exception:
        pass  # menu polish is cosmetic; never let it crash the app


def _screen_visible_size() -> tuple[int, int] | None:
    """The usable screen area (menu bar and Dock excluded), in points; None off-macOS."""
    try:
        from AppKit import NSScreen

        vf = NSScreen.mainScreen().visibleFrame()
        return int(vf.size.width), int(vf.size.height)
    except Exception:
        return None


def _default_geometry() -> dict:
    """A roomy portrait window sized to the screen: comfortable on a 13" laptop and
    on a 27" monitor alike. No x/y so the platform centers it."""
    screen = _screen_visible_size()
    if screen:
        sw, sh = screen
        return {
            "width": max(_MIN_W, min(1000, int(sw * 0.46))),
            "height": max(_MIN_H, min(1040, int(sh * 0.88))),
        }
    return {"width": 820, "height": 900}


def _geometry_path() -> Path | None:
    db = os.environ.get("BLURT_DB_PATH")
    return Path(db).parent / "window.json" if db else None


def _load_geometry() -> dict:
    path = _geometry_path()
    if not path or not path.exists():
        return _default_geometry()
    try:
        saved = json.loads(path.read_text())
    except Exception:
        return _default_geometry()
    if not isinstance(saved, dict) or "width" not in saved or "height" not in saved:
        return _default_geometry()

    # Clamp a remembered size to the current screen so a window saved on a big monitor
    # still fits a smaller one; re-center if the saved position lands off-screen.
    screen = _screen_visible_size()
    if screen:
        sw, sh = screen
        saved["width"] = max(_MIN_W, min(int(saved["width"]), sw))
        saved["height"] = max(_MIN_H, min(int(saved["height"]), sh))
        x, y = saved.get("x"), saved.get("y")
        if x is None or y is None or not (0 <= x <= sw - 100 and 0 <= y <= sh - 80):
            saved.pop("x", None)
            saved.pop("y", None)
    return saved


def _save_geometry(geom: dict) -> None:
    path = _geometry_path()
    if not path:
        return
    try:
        path.write_text(json.dumps(geom))
    except Exception:
        pass  # losing window geometry is never worth failing a close over


def _brand_macos_app() -> None:
    """Make macOS show blurt's identity, not the host Python's.

    The menu bar name and the standard About panel (name, version, copyright) are read
    from the *running process's* main bundle. The launcher execs an external interpreter,
    so macOS resolves that bundle to Python's, which is why the menu says "Python" and
    About shows the Python version and PSF copyright. Overriding these keys in the
    bundle's in-memory info dict is the standard fix for a Python GUI app.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle

        from . import __version__

        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = "blurt"
            info["CFBundleShortVersionString"] = __version__
            info["CFBundleVersion"] = __version__
            info["NSHumanReadableCopyright"] = "just type. it remembers."
    except Exception:
        pass  # cosmetic only; never block the launch over branding
