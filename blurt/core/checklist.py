"""Server-side checkbox toggling.

The UI never sends edited note content for a checkbox tick (that would be a way
to silently rewrite a note while bypassing the index). Instead it sends only the
ordinal of the checkbox it clicked, and the server flips that one marker here.
This keeps the toggle deterministic and tamper-proof: the only thing that can
change is a single `[ ]` <-> `[x]` character.

A "checkbox" is a list item whose marker is `-`/`*` followed by `[ ]` or `[x]`,
counted top-to-bottom across the note. This matches exactly what `md()` renders
as a checkbox in the front end, so the ordinals line up.
"""

from __future__ import annotations

import re

# Anchored per line (re.M); only spaces/tabs around the marker so it never spans
# lines. Captures the bracket halves so we can swap just the middle character.
_CHECKBOX = re.compile(r"^(?P<pre>[ \t]*[-*][ \t]+\[)(?P<mark>[ xX])(?P<post>\])", re.M)


def set_checkbox(content: str, index: int, checked: bool) -> str | None:
    """Set the `index`-th checkbox (0-based) to checked/unchecked.

    Returns the new content, or None if `index` is out of range (e.g. the note
    changed under the user). Idempotent: setting an already-correct box is a no-op
    rewrite, which makes double-clicks safe.
    """
    new_mark = "x" if checked else " "
    state = {"n": 0, "hit": False}

    def repl(m: re.Match[str]) -> str:
        i = state["n"]
        state["n"] += 1
        if i != index:
            return m.group(0)
        state["hit"] = True
        return f"{m['pre']}{new_mark}{m['post']}"

    new = _CHECKBOX.sub(repl, content)
    return new if state["hit"] else None
