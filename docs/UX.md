# BLURT — UX Spec (canonical)

This is the authoritative description of how Blurt behaves and feels. It
supersedes the UI sections of `docs/SCRATCHPAD_PRD.md` where they differ.
Rationale for notable choices lives in `docs/DECISIONS.md`.

The north star: **capture is frictionless, retrieval is keyboard-only, the
intelligence stays out of your way until you want it.** No mouse is ever
required. Nothing pops up that you didn't summon.

---

## 1. Layout — input pinned to the bottom

The input is always fixed at the bottom of the viewport. The stream scrolls
upward above it, newest entry nearest the input (chat/terminal model). The peek
sits between the stream and the input, and is empty unless there are matches.

```
┌─────────────────────────────────────────┐
│  [stream — scrolls up, oldest at top]    │
│                                          │
│  thu feb 27 · Mom birthday March 15.     │
│  friday   · Raj's number is 050-2222.    │
│  sunday   · Home wifi: homenet-9921.     │  ← newest, nearest input
├─────────────────────────────────────────┤  ← hairline
│  [peek zone — empty unless matches]      │
├─────────────────────────────────────────┤  ← hairline, only when peek active
│  just type.|                             │  ← input, fixed bottom
└─────────────────────────────────────────┘
```

- Stream is `flex: 1; overflow-y: auto; flex-direction: column-reverse`.
- Peek zone is capped (~180px), scrolls internally, collapses to 0 when empty.
- Input bar is `flex-shrink: 0`, textarea caps at ~5 lines then scrolls inside.
- Full-width with comfortable gutters. No central column.

---

## 2. The peek

The peek is the signature feature: while you type, Blurt quietly shows existing
notes that resemble what you're writing, so you notice you're repeating or
updating something.

### Trigger
- Fires on every keystroke, **180ms** debounce.
- **Minimum 2 words.** A single word is not enough context; it returns noise.
  The peek is for mid-thought capture, not search.
- Each in-flight request aborts the previous one (`AbortController`), so results
  never land out of order and never flicker.

### What it shows
- A faint **count label** when there's more than one match: `3 matches · ↑ to
  browse`. No label for a single match.
- The matching **active** notes only, most-relevant first, one faint line each
  (timestamp · snippet, matched terms subtly underlined, truncated with ellipsis).
- Nothing at all when there are no matches. Silence means no conflict; keep typing.

Superseded ("dead", struck-through) notes are **archived, not searched** — they
never appear in the peek or in search results. They remain only as history you
can restore. No time or compute is spent looking through them.

### Visual
- Resting lines are quiet but legible (the top match is glanceable without
  arrowing in). The focused line brightens and gains a 2px left accent border.
  Expanded line wraps to full content and brightens further.
- No borders, no cards, no backgrounds. Just text. Lines fade in with a small
  stagger (~30ms each).

---

## 3. Keyboard flow

The cursor stays visually in the input the whole time. Peek browsing is handled
by intercepting keys at the input based on peek state.

### State machine
```
TYPING ──180ms, ≥2 words, matches──▶ PEEK VISIBLE (no focus)

  enter the peek:
    Cmd/Ctrl+↑              ─▶ always; focuses the top (newest) match
    ↑  (input is 1 line)   ─▶ also focuses the top match
    ↑  (input is multiline)─▶ moves the cursor up (normal); use Cmd+↑ for peek

  in the peek (cursor stays at the bottom; the STREAM never scrolls):
    ↑ / ↓                  ─▶ cycle through matches (bare arrows, no modifier)
    ↓ past the newest      ─▶ exit peek, cursor back to END of input
    Enter                  ─▶ edit the focused note INLINE in the peek (full text)
                              Cmd+Enter saves, Esc collapses back to the list
    Backspace / Delete     ─▶ supersede the focused note (⌘Z to undo)
    Cmd/Ctrl+C             ─▶ copy the focused note's content
    Esc                    ─▶ close peek, cursor back to END of input
```

**No-scroll principle:** everything you do to a peeked note (read, edit,
supersede, copy) happens inline in the peek zone, just above the input. The peek
never scrolls or jumps the stream. You stay where your hands are.

### Key table
| Key | Context | Action |
|-----|---------|--------|
| any char | always | update peek, reset peek focus |
| `Cmd/Ctrl+↑` | peek has matches | enter the peek, focus top match |
| `↑` | input single line, peek has matches | enter the peek, focus top match |
| `↑` | input multiline, not in peek | move cursor up (normal) |
| `↑` / `↓` | already in the peek | cycle matches; `↓` past newest exits to input |
| `Enter` | peek line focused | edit that note inline in the peek (`Cmd+Enter` saves, `Esc` backs out) |
| `Backspace`/`Delete` | peek line focused | supersede that note (undoable) |
| `Cmd/Ctrl+C` | peek line focused | copy that note's content |
| `Esc` | peek visible | close peek, cursor to END of input |
| `Cmd/Ctrl+Enter` | always | save current input (ignores peek state) |
| `Cmd/Ctrl+Z` | after a supersede/delete | undo it (no popup) |
| `Cmd/Ctrl+F` | always | open search |
| `Ctrl+Shift+D` | always | dark / light |
| `?` | not in search | toggle cheatsheet |

### Critical
- Any `↑`/`↓`/`Backspace` that acts on the peek must `preventDefault()` so the
  page never scrolls and the textarea cursor/text doesn't change.
- Entering the peek: `Cmd/Ctrl+↑` always works; bare `↑` only when the input is a
  single line, so multi-line cursor movement is never hijacked.
- Once focused in the peek, bare `↑`/`↓` cycle the list and `Backspace`/`Delete`
  supersede the focused note. While merely typing (not focused in the peek),
  those keys behave normally.

---

## 4. Capture, edit, supersede

- **Save:** `Cmd/Ctrl+Enter`. Instant (write is sub-ms; embedding is background).
  Input clears, new note appears at the bottom of the stream.
- **Edit:** click a stream note (or it's the target of a peek action) to edit in
  place; `Cmd/Ctrl+Enter` saves a new version and supersedes the old; `Esc`
  cancels and returns the cursor to the input **at the end of your text**.
- **Supersede / delete:** hover a stream note for a faint `×`; or `Backspace`
  on a focused peek line. Either way it's **undoable** with `⌘Z` (and via the
  "show retired" view).
- **Retired notes are hidden** from the stream by default; a faint
  "show retired" toggle reveals them (struck through) with a restore control.
- Editing a multi-fact note in place is the way to change one fact while keeping
  the rest. Superseding retires the whole note.

---

## 5. Search (summoned, keyboard-only)

- `Cmd/Ctrl+F` or the top `search` button opens a centered overlay over a dimmed
  background.
- Live results as you type (semantic + exact), matched terms highlighted, with a
  relevance floor so weak/noise matches are dropped (shows "no matches" instead).
- **Arrow-navigable:** `↑`/`↓` move through results, `Enter` jumps to that note
  in the stream (scroll + flash), `Esc` closes. No mouse needed.

---

## 6. Cheatsheet

- Auto-shows once on first ever load (~4s, then fades). After that, only on `?`.
- `?` toggles; `Esc` closes. Small panel above the input, faint, summoned only.
- Lists the shortcuts in §3. Voice input is **v2**; it is not shown until built.

---

## 7. Aesthetic

- Looks like paper / a plain dark notepad. Off-white `#f9f7f4` light, near-black
  `#0f0f0f` dark. Mono font (local stack, no web fetch).
- One hairline divider, one quiet `/` anchor at the bottom. No toolbars, no
  icons beyond the single `search` affordance, no chrome.
- Tight entry spacing (it's a scratchpad, not a document). Hover brightens text
  and reveals the `×`; nothing else moves.
- Markdown (incl. tables) renders inline in the stream, escape-first and
  XSS-safe; the input stays plain text.

---

## 8. Performance / feel — snappy is a HARD requirement

The peek and search must feel instant. This is not a polish item; it is the
product. Anything that makes them feel like they're "thinking" is a bug.

- **Warm model.** The embedding model stays resident in Ollama (`keep_alive`), so
  there is never a cold-load stall on the first keystroke after a pause. Warm
  embed is ~20ms; KNN is sub-millisecond.
- **Short debounce + abort-stale.** Peek ~120ms, search ~100ms. Every keystroke
  aborts the in-flight request (`AbortController`) so requests never queue and
  results never land out of order or flicker.
- **Optimistic UI.** Save / edit / supersede update straight from the response;
  no refetch, no spinner.
- **Background indexing never blocks** an interactive peek or search request.
- Target: from keystroke-pause to visible result, well under ~200ms on the
  local network.

---

## 9. Retrieval architecture (supporting the peek)

- The vector index holds ACTIVE chunks only. Superseding removes a note's
  vectors; restoring re-adds them from the stored embedding (no re-embedding).
  Dead notes are archived and never searched — zero query-time cost.
- `/api/suggest` returns `{ match, score, matches }` where `matches` is the
  ranked list of ACTIVE matches for the peek; `match`/`score` stay the single
  best for the API contract.
- Search and the peek both exclude superseded notes entirely.
