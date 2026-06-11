# DECISIONS — architecture decision record

Why things are the way they are, especially where we diverged from the original
PRD (`docs/SCRATCHPAD_PRD.md`). The PRD is a sketch; this file is what we
actually did and the reasoning. Newest decisions can be appended at the end.

---

### 1. Embeddings via Ollama `nomic-embed-text`, behind a swappable seam
The PRD specified Ollama + nomic. Kept it: it was already installed, gives 768-d
embeddings, and runs fast on Apple silicon for single queries (~27ms). But the
`Embedder` is a thin protocol (`embed_documents` / `embed_query` / `health`), so
an in-process backend (e.g. fastembed/ONNX) can replace it with zero changes to
the rest of the app. That would remove Ollama as a hard dependency for the core
feature; logged as a future option, not done in v1.

### 2. sqlite-vec installed as a pip package, not a hand-loaded extension
The PRD warned that sqlite-vec is "a loadable extension, not a pip package —
handle this carefully." That is **stale**. `pip install sqlite-vec` ships the
binary and `sqlite_vec.load(conn)` loads it. Verified v0.1.9 with cosine KNN.
No manual extension juggling needed.

### 3. nomic task prefixes (`search_document:` / `search_query:`)
nomic-embed-text is trained with task prefixes; using them is the single biggest
retrieval-quality lever. Stored chunks and ghost text are embedded as
`search_document`; the search endpoint embeds the query as `search_query`. On by
default (`embed_use_prefixes`).

### 4. Ghost threshold lowered 0.78 → 0.62 (empirically tuned)
The PRD's 0.78 only fired on near-identical rephrasings, making the ghost feel
like exact match. Measured doc-doc cosine on real notes: genuine semantic
matches (reworded, non-overlapping vocabulary) land ~0.64-0.91; unrelated notes
sit below ~0.57. 0.62 fires on meaning while rejecting noise, with a clean gap.
Tunable via `BLURT_GHOST_THRESHOLD`.

### 5. Two ghost word-gates, because the test suite contradicts itself
`docs/test_suite.py` requires a 3-word input ("Raj's number is") to fire but a
1-word input ("Tax") not to — incompatible with a single 5-word gate. Resolved
by splitting concerns: a **server floor** (`ghost_min_words_server` = 2) rejects
trivially short inputs, and a **client trigger** (`GHOST_MIN_WORDS` = 3, in
app.js) decides when the UI bothers to ask. Satisfies both tests and is the
right design anyway.

### 6. Hybrid search (lexical + vector), not pure vector — NEW, not in PRD
Pure semantic search cannot reliably find an exact token (a code, ID, phone
number) the instant it is saved, before embedding completes. Added a lexical
substring pass that runs alongside vector KNN; exact matches lead, semantic
fills the rest. This both improved the product (exact terms findable
immediately) and made a timing-flaky test robust by removing its dependence on
embedding latency.

### 7. Self-contained, escape-first Markdown renderer — overrides PRD's DOMPurify
The PRD said render Markdown and sanitize with DOMPurify (a CDN/bundled dep).
Instead we render Markdown in `app.js` by HTML-escaping the entire string first,
then applying a fixed set of formatting transforms on the escaped text. We only
ever emit tags we construct ourselves, so user content can never become live
markup — XSS-safe by construction, no sanitizer, no dependency, no network
fetch. This honors the PRD's own "no network calls" principle better than the
PRD's own suggestion did.

### 8. Local font stack, no Google Fonts fetch
The PRD floated loading JetBrains Mono from Google Fonts. That is a network call
on every load, contradicting local-first. We use a local font stack
(`JetBrains Mono`, `SF Mono`, `Menlo`, …) and fall back to system mono. Looks
great, zero network.

### 9. Active-only vector index; supersede/restore without re-embedding
`chunks.embedding` (BLOB) is the durable source of truth. `vec_chunks` (a vec0
virtual table) is a derived index holding vectors for **active** chunks only.
Superseding deletes a note's vectors from the index; restoring re-inserts them
from the stored BLOB. Result: search excludes superseded notes for free, and
restore costs nothing (no re-embedding). `chunks.id == vec_chunks.rowid` is the
join key.

### 10. Background indexing: incremental + batched
Saves never wait on embeddings. A single asyncio worker drains a queue,
processes entries in groups (so they become searchable progressively during a
bulk insert, not all-or-nothing), and batches embed calls for throughput. Batch
size balances interactive latency against bulk speed.

### 11. Package layout `blurt/`, not the PRD's flat tree
The PRD showed top-level `db/ core/ api/ static/`. Cross-importing top-level
dirs is import-path-fragile. We use a single `blurt/` package with submodules
and a root `main.py` entry point. Cleaner, no `sys.path` hacks.

### 12. Optimistic UI
Save/edit/supersede update the DOM immediately from the server's response
instead of refetching the stream. The app feels instant ("zappy"), which is the
product's soul.

### 13. Bulk-indexing timing tests are throughput-bound, accepted as such
`test_query_with_100_entries` (3s) and `test_1000_entry_search_performance`
(10s) assume embedding throughput (~100 docs/s) that real local nomic on
consumer hardware does not reach (~10-25 docs/s measured). With hybrid search +
incremental indexing they pass in a clean run, but they are inherently sensitive
to machine load. They test hardware speed, not app correctness. CI runs the
offline unit suite instead; the integration suite is a local gate.

### 14. DB file hardened to `chmod 600` in code, not setup.sh
The PRD put this in setup.sh, but the DB is created at runtime, not setup time.
`Database` sets owner-only permissions when it opens the file, so it is always
enforced regardless of how the app was started.

### 15. Layout width + scroll fix (from live owner feedback)
Container widened from 760px to 1100px (the 760 cap left large side gutters on
the owner's window). Fixed a white-on-scroll bug: `html, body { height: 100% }`
pinned them to one viewport, exposing the bare browser canvas past the content;
changed to `min-height` so the background grows with the content. (Later
superseded by the full-width, bottom-pinned layout in `docs/UX.md`.)

### 16. UX redesign — `docs/UX.md` is the canonical interaction spec
After hands-on testing the owner and I redesigned the interaction model. The new
spec (`docs/UX.md`) is approved; implementation is the next session's work.
Key calls and the reasoning:

- **Input pinned to the bottom, stream scrolls up (column-reverse).** Matches the
  chat/terminal mental model: capture where your hands are, history flows up.
- **The "replace" ghost action was removed entirely.** It was destructive
  disguised as friendly: typing a fragment and hitting "replace" silently retired
  a whole paragraph and substituted the fragment. Nobody would predict that.
- **Peek is a keyboard-browsable list, fully inline, the stream never scrolls.**
  The earlier single-peek + "+N like this" + route-to-search was friction
  (forced a mouse, jumped context). The fix: browse matches in place with
  arrows, act on them inline. Enter edits the focused note inline; the no-scroll
  rule is explicit because the owner specifically hates scroll/rescroll.
- **Bare `↑` is unsafe as a peek trigger** (it is cursor-up in multi-line input).
  Resolution: `Cmd/Ctrl+↑` always enters the peek; bare `↑` only when the input
  is a single line.
- **Supersede is `Backspace`/`Delete` on a focused peek line, not `Ctrl+U`.**
  More intuitive, and only fires once you have deliberately arrowed into a line
  (so it never eats text mid-type). Reverses the brief `Ctrl+U` experiment.
- **Dead (superseded) notes are archived, not searched.** They are dropped from
  the peek and search entirely — no compute spent on history. (This kept the
  active-only vector index; an earlier idea to keep all vectors and filter was
  dropped as unnecessary.)
- **Single-word peeks dropped; 2-word minimum.** The peek is for mid-thought
  capture, not lookup; one word is noise. Use search for single terms.
- **Undo stays keyboard (`⌘Z`), no popup.** The floating toast was replaced by a
  subtle inline affordance; undo must never require a mouse.
- **Snappy is a hard requirement, not polish** (UX.md §8): warm model, short
  debounce (~120ms peek / ~100ms search), abort-stale fetches.
- **`?` cheatsheet** for discoverability instead of permanent chrome. Voice input
  remains v2 and is not shown until built.

### 17. Tailscale exposure (owner request)
Bound the server to the host's Tailscale IP (not `0.0.0.0`) so the owner can use
it from another personal device over the tailnet, without exposing it to the
local/home network. Still no auth: acceptable for a single-person tailnet, gated
on Tailscale's encryption + ACLs. Wider exposure still needs the v2 auth work.

### 18. `docs/UX.md` front end built; peek/search/cheatsheet implementation calls
Implemented the canonical UX spec. The non-obvious calls made while building:

- **`/api/suggest` now returns `matches`** (ranked list of ACTIVE entries, each
  with its `score`) alongside the unchanged `match`/`score`/`more`. The peek
  renders this list; the single-best `match` stays for the test contract. The
  server already filters to above-threshold active notes, so the client trusts
  the list as-is (no second threshold pass needed).
- **2-word floor restored on both ends.** `ghost_min_words_server` and
  `ghost_min_words_client` are both 2 now (were 1 and 5). Supersedes decision #5's
  split-gate hack: a single 2-word floor satisfies both surviving ghost tests
  ("Raj's number is" fires; "   " does not) and matches UX.md §2. The old 5-word
  client gate was an interim experiment.
- **Peek ordering: most-relevant nearest the input.** Both `#stream` and `#peek`
  are `flex-direction: column-reverse`, so DOM index 0 renders at the bottom
  (nearest the cursor). The peek is built line_0..line_n then the count label, so
  visually the count rides on top and the top match sits by the input. Entering
  the peek focuses index 0; `↑` walks up to less-relevant/older matches; `↓` from
  index 0 exits back to the input. "Top match" in the spec = most-relevant (§2),
  not strictly newest — relevance order is what `suggest` returns and what §2 asks
  for.
- **`?` cheatsheet is gated to an empty input.** UX.md §3 says `?` toggles the
  cheatsheet "not in search", but a global `?` would make it impossible to type a
  literal question mark in a note. Resolution: `?` summons the cheatsheet only
  when the compose box is empty (and search is closed); with any draft text it is
  a normal character. Preserves frictionless capture, which outranks the literal
  spec wording here.
- **Column-reverse infinite scroll.** In a `column-reverse` stream `scrollTop` is
  0 at the bottom and grows negative upward, so "older" loads trigger when
  `scrollHeight - clientHeight - |scrollTop|` drops under 600px. The stream owns
  the scroll now (not the window), since the layout is a fixed-height flex column.
- **Abort-stale everywhere.** Peek (120ms) and search (100ms) each carry an
  `AbortController` that cancels the in-flight request on the next keystroke, plus
  a sequence guard, so results never land out of order or flicker (UX.md §8).
- **Verified in a real browser** (Playwright, headless Chromium) against the
  running server: bottom-pinned layout, peek list + count + underlined terms,
  `⌘↑` enter + arrow browse, arrow-navigable search overlay, `?` cheatsheet, zero
  console errors. The old "+N like this / route-to-search" ghost and the
  top-input layout are fully removed.

### 19. No top chrome; first-load splash; search is keyboard-only (owner request)
Hands-on, the owner cut all top chrome. The topbar is gone entirely — the `blurt`
wordmark, the `show retired` toggle, and the `search` button are removed. The
stream now runs to the top of the viewport.

- **Search is summoned only by `⌘/Ctrl+F`** (overlay unchanged). No visible
  affordance; the cheatsheet documents the key.
- **Retired notes have no toggle/restore-UI anymore.** They stay hidden; the only
  recovery is `⌘Z` immediately after a retire (the durable history still exists in
  the DB for a future view if ever wanted). Removed the now-dead `showRetired`
  state, `renderToggle`, `restoreEntry`, and the superseded/restore branch of
  `entryNode` — the stream only ever renders active notes.
- **First-load splash.** On the first visit (gated by `blurt-splash-seen` in
  localStorage), the word `blurt` swells in the center of the paper and fades over
  ~1.8s, then hands off to the focused input with the cheatsheet shown so a new
  user sees the keys. No splash on subsequent loads; the cheatsheet is then `?`-
  only. This is the single brand moment, replacing the removed wordmark.
- **The first-load cheatsheet is rendered INLINE in the notepad** (`#welcome`),
  not as the floating card — it reads like the keys are jotted into the empty pad
  just above the input. It clears on the first keystroke (or `Esc`). The small
  floating panel is reserved for the later `?` summon. Both share one key list.

### 20. Empty-pad intro, test-only erase, footer timestamp, global Esc (owner pass 2)
More live owner feedback while testing:

- **The new-notepad intro now plays whenever the pad loads BLANK**, not once-ever.
  Dropped the `blurt-splash-seen` localStorage gate; instead, if `loadStream`
  finds no `.entry` and there is no draft, run the splash + inline welcome. So a
  fresh install AND an erase-then-reload both feel like a new pad. With any notes
  present it is skipped and you land straight in. The erase action also replays
  the intro in place.
- **Test-only erase control.** A faint top-right `erase` button, shown ONLY when
  `/api/status` reports `testing: true` (i.e. the server ran with
  `BLURT_TESTING=1`). Two-click confirm ("erase" → "erase — sure?" for 3s →
  wipe) so a stray click can't nuke data; calls the existing `DELETE
  /api/test/reset`. `status` gained a `testing` boolean for this. In any normal
  run the control never appears and the endpoint 404s. The owner's tailnet demo
  is deliberately run with `BLURT_TESTING=1` so the control is live for testing;
  acceptable on a single-person tailnet, same trust basis as #17.
- **Timestamp moved to a bottom-right footer, fainter** (10px, opacity ~0.45,
  brightens on hover). Each entry is now `body` + a `.entry-foot` flex row holding
  the hover-only retire `×` (left) and the timestamp (right).
- **Esc closes the search overlay from the GLOBAL handler**, not only the search
  input's own handler. Bug: if focus left the input while the overlay was open
  (focus on `body`), the input handler never fired and Esc did nothing. Now Esc
  closes search regardless of focus — every action stays a single keystroke.

### 21. Copy/spacing pass; "retire" reworded to "delete" (owner pass 3)
- **"Retire" → "delete" in all user-facing copy.** The owner found "retire"
  confusing ("if I change and save it's already gone"). Editing a note already
  supersedes the old one; the `×` / `Backspace` action is just a plain delete, so
  it now says "delete" (cheatsheet line, `×` tooltip, the inline undo stub
  "deleted …", the peek hint). Internal identifiers (`retireEntry`,
  `.retired-stub`) are unchanged — the DB concept is still "supersede".
- **Dropped the "undo a retire" cheatsheet line.** The `⌘Z` undo still works; it
  is taught contextually by the inline "deleted … · undo (⌘Z)" stub that appears
  for ~8s after a delete, so it does not need a permanent cheatsheet row.
- **Peek-edit commit clears the input.** After `⌘Enter` on an inline peek edit,
  the compose box is blanked (the typed text was only the trigger that surfaced
  the note; once the note is updated it is stale). `Esc`-ing out of the editor
  without saving keeps the text, by design.
- **Cheatsheet panel enlarged + screen-centered** (was 520px bottom-anchored; now
  `min(760px, 100vw-48px)`, vertically centered, 15px, roomier grid) so it scales
  with the window. Removed the wasteful footer hint text from both the floating
  cheatsheet and the inline welcome.
- **Search placeholder simplified** to just "search" (dropped "semantic + exact").
- **Tighter stream.** Note vertical padding cut (7px→2px), footer margin removed,
  stream gap 0, body line-height 1.45 — notes sit close together (~41px each).
- **Data reset to empty.** The owner wanted to test cold as a new user, so
  `blurt.db` was wiped (no seed). The earlier "keep 3 scenarios" idea was dropped
  at the owner's instruction.

### 22. Whole-note delete in the peek requires `⌘/Ctrl+Delete` (owner request)
- **Bare Backspace no longer deletes a focused match.** While browsing the peek,
  deleting a whole note now takes one deliberate `⌘/Ctrl+Delete` (handled for both
  `Backspace` and `Delete`, since the Mac "delete" key reports as `Backspace`).
  Rationale (owner): bare Backspace should always feel letter-by-letter; it must
  not double as a note-nuke. With the gate in place, bare Backspace falls through
  to the compose textarea and edits the draft — and the textarea's `input` handler
  resets the peek focus, so the act of editing cleanly exits the peek.
- **Copy updated to match.** Peek hint reads "`${MOD}+delete` delete"; the
  cheatsheet row is `${MOD}+delete`; the `app.js` header docstring now says
  "delete (Cmd/Ctrl+Delete)". The undoable supersede behavior itself is unchanged
  (`⌘Z` still restores).

### 23. Checklists: render in `md()`, toggle IN PLACE (no supersede, no re-embed)
- **A `-`/`*` item whose content is `[ ]`/`[x]` renders as a clickable checkbox**
  (`md()`), escape-first like everything else (a `<b>` inside the label stays inert
  text). The box is its own click target; clicking the label text still opens the
  note editor. The tick is a CSS checkmark (no glyph-font dependency).
- **Ticking a box does NOT supersede or re-embed the note.** This is the key call.
  A checkbox flip is retrieval-neutral, so paying the normal edit cost (append a new
  entry, retire the old, re-chunk, re-embed) would churn the note's id/position and
  waste embedding for nothing. Instead a dedicated path rewrites the single marker
  character in place: same entry id, same timestamps, vectors untouched
  (`db.set_content_in_place`). A real *text* edit still goes through PATCH (supersede
  + re-embed) as before. The slightly stale stored chunk text (still holds the old
  `[ ]`) is irrelevant to search — nobody queries the literal marker, and the
  embedding is unchanged for practical purposes.
- **The UI never sends edited content for a tick — only the checkbox's ordinal.**
  `PATCH /entries/{id}/checkbox {index, checked}`. The server flips the index-th
  checkbox itself (`core/checklist.set_checkbox`), so a checkbox click can NEVER
  silently rewrite note text while bypassing the index. `index` is the 0-based
  top-to-bottom ordinal, matched exactly by `md()`'s `data-cbi`. Idempotent (sets to
  the requested state, so double-clicks are safe); out-of-range → 409. Optimistic in
  the client, reverts on failure.

### 24. List-continuation keystrokes in the compose box
- **Enter continues a list**: repeats `- `/`* `, increments `N.`, or starts a fresh
  `- [ ] ` after a checkbox item; Enter on an *empty* item exits the list (drops the
  marker). Shift+Enter is still a soft line break. Keyboard-only, no toolbar — matches
  the product. Uses `setRangeText` so the browser's native undo still works, then
  fires a synthetic `input` so draft-save/auto-grow/ghost stay in sync.

### 25. Markdown export: `⌘/Ctrl+S` download + always-current `scratchpad.md` mirror
- **`⌘/Ctrl+S` downloads the whole active stream** as `blurt-<date>.md` (overrides
  the browser "save page" dialog). Uses the existing `GET /api/export/markdown`.
- **An always-current `scratchpad.md` is mirrored next to `blurt.db`** on every
  mutation (create/edit/delete/restore/tick), so a plain, app-independent, human-
  readable copy of everything always exists on disk while the DB stays the fast
  source of truth. On by default (`BLURT_AUTO_EXPORT_MD`). Written off the request
  path: a `MarkdownMirror` worker DEBOUNCES (~1s, coalescing bursts) and writes
  ATOMICALLY (temp file + `os.replace`) so a reader never sees a half-written file;
  it also flushes once at boot and at shutdown. Both the endpoint and the mirror
  share one renderer (`core/exporter.render_stream_markdown`). `scratchpad.md` is
  git-ignored.

### 26. Default reading font size reduced (owner feedback)
- Owner found the type a touch large. Dropped the base stream text 15px→14px and the
  input 16px→15px. JetBrains Mono and the layout are unchanged. Reversible knob; can
  go smaller or become a live control if the owner wants.

### 27. Hands-on bug fixes: stray horizontal scroll, cramped peek edit, Esc-out
Three issues from the owner's first real session:
- **Stray horizontal scrollbar the moment a note rendered.** Root cause: the scroll
  containers (`#stream`, `#peek`, `#search-results`) set `overflow-y: auto`, and CSS
  promotes the *other* axis to `auto` too when it is `visible`. Their rows use a
  `margin: 0 -10px` (`-8px` for results) "full-bleed" highlight that sticks past the
  container, so once a row existed the container overflowed horizontally. Fix: give
  each container matching side padding (10px / 8px) so the bleed lands inside the box.
  `overflow-x: hidden` was rejected because it would clip the focused row's left
  accent bar (which sits in the bleed). Any future bleed-row-in-a-scroll-container must
  carry the same padding.
- **Cramped peek editor + flaky Esc** (an intermediate fix making the in-peek editor
  roomy + Esc-robust) was **superseded the same day by #28** — the in-peek editor was
  removed entirely. Don't reintroduce a `.peek-edit` / `#peek.editing` editor.

### 28. Editing a peek match happens IN PLACE in the stream, not in the peek
The owner's second pass rejected the in-peek editor outright: with the note already
visible in the stream, opening a second editable copy down in the peek read as
duplication ("why can't we edit in line itself"). He also hit two-press Esc and a
broken `↑` afterward. New model:
- **Enter on a peek match scrolls to that note in the stream and opens ITS editor
  in place** (`editPeekFocused` → `openEditor`, which is the same roomy editor as
  clicking a note). If the match isn't currently rendered, the stream pages older
  notes in until it is. No second editor, no duplication.
- **The peek is CLEARED when the edit opens** (not hidden-and-restored). The first
  attempt hid it with a `#peek.suppressed` class and restored it on cancel, but that
  made Esc two-press (Esc #1 closed the editor *and* popped the peek back, Esc #2
  dismissed the peek — the owner reported exactly this). Clearing instead means there's
  nothing left to dismiss: **one Esc backs out cleanly.** `openEditor` keeps an
  `onSaved` hook (blank the trigger text on save); no `onCancel`/suppress needed.
- **`↑` re-summons the peek** for the current draft when it isn't showing: the
  ArrowUp handler, finding no matches but ghostable text, fires the ghost and steps
  into the peek on arrival (`fireGhost(enterAfter=true)`). So a single `↑` brings the
  peek back after an edit, without keeping stale matches around.
- Removed: `peekEditKeydown`, `exitPeekEdit`, the `state.peek.editing` flag, the
  in-peek `<textarea>` render branch, the `.peek-edit` / `#peek.editing` / `.suppressed`
  CSS, and the setPeek suppress-guard.
- **`clearPeek()` moved INTO `openEditor`** so it fires for *every* edit path, not just
  peek-launched. The owner still hit two-press Esc when he *clicked* a visible note to
  edit while the peek was up (the click path didn't clear it). Clearing on every editor
  open means no peek ever sits behind an editor → one Esc, always.
- **`#stream.editing` calms the other notes while editing**: `openEditor` toggles the
  class; CSS suppresses `.entry:hover` background and the hover `×` on all rows so only
  the edit box stands out. (The owner saw a neighbouring note "highlighted" — it was its
  hover state, triggered when `scrollIntoView` slid it under the stationary cursor.)

### 29. Enter saves; Shift+Enter is a new line (chat-app model, owner request)
- Switched the primary save key from `⌘/Ctrl+Enter` to plain **Enter**. Blurt is a
  quick-capture pad and the owner wanted the universal chat convention (WhatsApp/Slack:
  Enter sends, Shift+Enter for a line break). Capture speed wins over free multi-line
  typing; pasting multi-line text is unaffected (newlines come in via paste, not Enter).
- **Shift+Enter inserts a new line, and continues a list** if you're in one — the
  list-continuation from #24 moved off plain Enter onto Shift+Enter (continuing a list
  *is* making a new line, so this is the natural home and avoids colliding with save).
- **`⌘/Ctrl+Enter` still saves** as an unadvertised alias (muscle-memory safety net).
- Empty/whitespace input still no-ops on Enter (no accidental blank notes — `saveEntry`
  already guards). Browsing the peek, Enter still edits the focused match (#28).
- Copy updated: cheatsheet now `enter → save`, `shift+enter → new line`; empty-pad hint
  says "press enter".
- **The in-stream note editor uses the same model** (added when the owner asked how to
  save an edit): Enter (or ⌘/Ctrl+Enter) saves the edit, Shift+Enter is a new line and
  continues a list. `maybeContinueList(ta)` was generalized to take any textarea so the
  editor and the compose box share it.

### 30. Quick undo of the last save (owner request)
- After saving a note, **`⌘/Ctrl+Z` (or a brief inline "undo" link) removes it** —
  `offerSaveUndo` sets `state.undoFn` to delete the just-created note. This closes the
  gap: delete and edit were already undoable via the same `state.undoFn` slot; a fresh
  *create* was not.
- **If the pad is still empty (nothing typed since), the note's text returns to the
  input** and the ghost re-fires, so it's exactly as if never saved. If the user has
  already started a new note, undo just removes the saved note and leaves their draft
  alone (no clobber).
- Single-level undo by design: `state.undoFn` always holds the most recent undoable
  action (create / delete / edit), matching the existing model. The inline link fades
  after ~5s but `⌘Z` keeps working until the next action. Undo = supersede (the note is
  hidden + restorable + pulled from search/mirror), consistent with the delete path; no
  hard delete. No cheatsheet row — taught contextually by the inline link (cf. #21).

### 31. Highlight fill is a subtle cool blue-grey, not the orange accent (owner pick)
- The owner disliked the burnt-orange (`--accent-soft`) tint on hover / focused peek
  line / edit box / search results — read as muddy. Offered four subtle options; he
  chose **cool blue-grey**. New `--highlight` var: `rgba(40,70,110,0.06)` light,
  `rgba(140,170,210,0.07)` dark. Replaced every `--accent-soft` background fill with it
  and removed `--accent-soft` (now unused). The orange `--accent` stays as the thin
  left accent bar / checkbox fill / selection, so "selected" still has a color cue.

### 32. Editing a note updates it IN PLACE — supersede-on-edit dropped (owner confusion)
- The owner edited a note, hit Enter, and got a `deleted "There once was..."` undo stub
  while the edited note jumped to the bottom. That was the **append-only edit model**
  leaking: `PATCH` used to `replace_entry` (insert a new entry + supersede the old,
  pointing old→new), so an edit looked like a delete-plus-recreate.
- **Now `PATCH /entries/{id}` edits in place**: `set_content_in_place` rewrites the
  content on the SAME row (same id, `created_at`, position), then `clear_chunks` drops
  the stale embeddings and the entry is re-enqueued for a background re-embed. Exact-text
  search hits the new content immediately; semantic catches up. This unifies the model
  with the checkbox tick (already in-place) — the only difference is a text edit re-embeds.
- **Frontend `applyEdit` swaps the node in place** (no `undoStub`, no `prependEntry`):
  the note shows updated where it sits. Undo restores the previous text via another
  in-place `PATCH` (the client holds the old content) and shows an `updated · undo (⌘Z)`
  hint (shared `showUndoHint`, same as save-undo #30).
- **Tradeoff:** edits no longer keep a restorable prior *version* in the DB (supersede
  history is gone for edits; delete/restore still supersede). Acceptable for a scratchpad,
  and one-level undo covers the immediate oops. Removed `db.replace_entry`. No CLAUDE.md
  invariant is broken (vec_chunks still indexes active chunks only; content still verbatim).

### 33. Static UI served `no-cache`; edit box auto-grows (owner: stale + bounded)
- **Stale UI.** The owner kept reporting fixes "not working" that worked in incognito —
  the browser was serving a cached `app.js`. Starlette's StaticFiles sends ETag/
  Last-Modified but no `Cache-Control`, so browsers heuristically cache and skip
  revalidation. Fix: a `_NoCacheStatic(StaticFiles)` subclass overrides `get_response`
  to add `Cache-Control: no-cache` — the browser still gets cheap 304s but always
  revalidates, so UI edits show on every reload. (One last hard-reload is needed to
  evict the already-cached copy; after that it's automatic.)
- **Bounded edit box.** The in-stream editor was capped at `rows=12` and scrolled. The
  owner wanted it to take whatever vertical space is available and show the whole note,
  scrolling only if it exceeds the screen. Now `openEditor` sets `rows=1` and grows the
  textarea to its `scrollHeight` (with an `input` listener); CSS caps it at `82vh` with
  `overflow-y:auto`, so a normal multi-line note is fully visible and only a
  taller-than-viewport note scrolls inside.

### 34. Cheatsheet gains a "formatting" section (owner: how do I do other input types?)
- The owner asked how to make bullets/checklists/etc. and noted the cheatsheet showed no
  shortcuts for them. There are none by design — formatting is **typed markdown** ("just
  type", no toolbar, per #19). The gap was discoverability. Added a `formatting · just
  type it` section to the `?` cheatsheet (`formatListHtml`): bullet `- `, numbered `1. `,
  checklist `- [ ]` (click to tick), heading `# `, bold `**`, italic `*`, code `` ` ``,
  quote `> `, link `[text](url)`. Lists auto-continue on Shift+Enter (already in the keys
  section). Left the first-load `#welcome` keys-only to keep onboarding light; the full
  reference lives under `?`. Factored the shared `rowsToDl` renderer.

### 35. Accent de-oranged to slate blue-grey; cheatsheet bigger + click-outside (owner)
- **No more orange.** The owner disliked the burnt-orange accent ("keep it simple bro").
  Offered slate / monochrome / indigo; he chose **slate blue-grey**, cohesive with the
  blue-grey `--highlight` from #31. `--accent` #b4541a→`#41698f` light, #e08a4e→`#88b0d4`
  dark; `--selection` retinted to match. Every accent use (edit/peek bar, checked
  checkbox, links, labels, splash) flows from the var, so two lines recolored everything.
  Supersedes #31's "orange accent stays" note — the accent is now slate too.
- **Cheatsheet bigger:** width 760→900px, padding/font bumped, and the key/format lists
  now lay out in **two columns** of pairs on wide screens (one column under 620px) so it
  fills the space instead of being one tall list.
- **Click-outside to dismiss:** `showCheatsheet` adds a deferred `mousedown` listener
  (`onCheatsheetOutside`) that hides it when you click outside the panel; removed on hide.
  Esc and `?` still toggle it too.

### 36. Slash menu (`/`) for formatting (owner idea — markdown prefixes are fiddly)
- The owner found typed markdown prefixes (esp. `- [ ]` with its exact spacing) hard to
  remember/type, and asked for a Notion/Linear-style `/` menu. Built it — and it's a good
  fit for an open-source app since the pattern is familiar everywhere.
- **Trigger:** typing `/` at the **start of a line** (regex `^/([\w-]*)$` on the current
  line up to the caret) opens a popup above the input. Line-start-only so a `/` inside
  text or a URL (`http://`) never triggers it. Typing filters (`/to` → to-do); `↑/↓` move,
  `Enter`/`Tab` pick, `Esc` closes, click picks (mousedown+preventDefault keeps focus),
  blur/save close it.
- **Picking replaces the typed `/query` with the markdown** (`setRangeText` from the
  line start), so it's just an on-ramp — the underlying format is still plain markdown that
  renders and exports normally. Items: to-do `- [ ]`, bullet, numbered, heading `#`,
  subheading `##`, quote `>`, code block (caret parked inside the fences), divider `---`.
  Hand-typed markdown still works unchanged.
- Scope v1: the **compose box** only (not the in-stream editor) — can extend later. New
  `#slashmenu` element, `state.slash`, `updateSlashMenu/renderSlash/chooseSlash/closeSlash`,
  wired into the compose input/keydown. Cheatsheet gained a `/` row.

### 37. Bare URLs auto-link; ⌘/Ctrl-click opens them (owner request)
- `md()` now auto-links **bare `http(s)://` URLs, `www.` URLs, and emails** (not just
  `[text](url)`) — like every other notes app. `linkify` does it in one left-to-right
  pass (each URL consumed once, so a bare URL inside a markdown link isn't double-wrapped);
  trailing sentence punctuation (`. , ! ?`) is peeled out so "see https://x." keeps the dot.
  `www.` gets an `http://` href; emails get `mailto:`.
- **Code-span safety:** rewrote `inlineMd` to `split` on `` `code` `` and format only the
  non-code parts, so a URL/`**` inside backticks stays literal. (Replaced an earlier
  NUL-placeholder attempt that risked colliding with real text — the split is collision-free.)
- **XSS:** unchanged guarantee — `md` escapes first, `linkify` only ever emits anchors we
  build, and only `https?:`/`mailto:`/`/` schemes are honored for `[text](url)` links;
  a bare `javascript:...` is not matched as a link. Verified `<script>` stays inert.
- **Opening:** in the stream, a **bare click still edits the note** (links don't hijack
  editing); **⌘/Ctrl-click opens** the link in a new tab (`window.open`). Links carry a
  `⌘/ctrl-click to open` tooltip and a pointer cursor. Search-result links open on plain
  click (no edit context there).

### 38. Open-sourced (owner asked to commit + push)
- Published at https://github.com/rbsriram/blurt, MIT, public. One clean initial
  commit authored by the owner (rbsriram), no bot trailer on that first commit; later
  commits carry a `Co-Authored-By: Claude Opus 4.8` trailer (owner switched from
  "skip it" to "friendly Claude Code credit"). CI (lint + import smoke + offline unit
  tests, 3.11/3.12) is green; Issues + Discussions on. Sanitized before going public:
  removed the owner's Tailscale IP (was only in STATE.md), rewrote STATE.md as a clean
  status doc, and untracked CLAUDE.md (internal AI brief, kept local, gitignored).

### 39. Security: Host-header validation (anti-DNS-rebinding)
- For a distributed no-auth local app, the real browser threat is a malicious page
  resolving its own domain to 127.0.0.1 and hitting the local API. Added a middleware
  (`_guard_host` in `app.py`) that 403s any request whose `Host` is not a known
  localhost name (or the host the server was deliberately bound to). The attacker's page
  sends its own domain as Host, so it is refused. Documented in docs/SECURITY.md.

### 40. Distribution is pip/pipx, not Homebrew (owner: "why not pip install")
- Homebrew was a dead end: the owner's very new brew wants Xcode 26.3 to build any
  formula from source, and even a no-compile formula fails that preflight. Pivoted to a
  Python `blurt` console command (`blurt/cli.py`: checks Ollama, opens the browser,
  starts the server) so `pipx install git+https://github.com/rbsriram/blurt` just works,
  cross-platform, no compiler. A curl `install.sh` remains as a secondary option (it
  bootstraps a venv and hands off to the same `blurt`). The `rbsriram/homebrew-blurt` tap
  was created early in this exploration and later **deleted**: the formula never installed
  on the owner's own machine (the Xcode preflight above), and pipx is the committed path,
  so an orphaned tap was only confusing.
- Packaging bugs found and fixed: (a) static UI + db/schema.sql were not bundled in the
  wheel — added `[tool.setuptools.package-data]`; (b) a stale `build/` dir had been
  committed and setuptools used it as the build staging dir, shipping wrong versions —
  untracked it and gitignored `build/`/`dist/`. Caught both via a version-string mismatch.

### 41. Public positioning: "scratchpad", README hero, demo GIF (owner)
- Word is **scratchpad**, not notepad (the owner's word, more distinctive, the category
  the product is built around). Swapped it in the GitHub description and pyproject.
  README keeps "the dumbest notepad you have ever seen" (a visual metaphor in the
  owner's story). Headline tagline: **just type. it remembers.**
- README leads with a dark-mode **demo GIF** (`docs/demo.gif`) recorded with Playwright
  driving the real app (multi-match peek + in-place edit, credential-free seed) and
  ffmpeg for the GIF. Added a "What it needs" hardware note. The owner's first-person
  "why I built this" story is in his own words.
- Process note: the owner asked to run all public-facing copy past him before it ships.

### 42. `blurt` opens a native desktop window, not a browser tab (owner)
- Owner's complaint: it lives in a browser, so "what if the browser is closed?" A tab is
  not an app. Made `blurt` open Blurt in its own native window that owns the app's
  lifecycle: close the window and Blurt quits, no stray tab to babysit.
- Backend choice: **pywebview** over Electron/Tauri. It is Python-native (no Node, no
  Rust, no bundled Chromium), free, BSD-licensed, and renders through the OS webview
  (Cocoa/WebKit on macOS via pyobjc, GTK/WebKit on Linux). The install stays tiny because
  there is no second browser engine to ship. Same FastAPI app, unchanged.
- Mechanics (`blurt/desktop.py`): uvicorn runs in a daemon thread with signal handlers
  disabled (they can only install on the main thread, which the GUI event loop needs),
  the launcher waits for `/api/status`, then `webview.start()` blocks on the main thread
  until the window closes, which flips `server.should_exit`. Window is a tall 560x860
  column to suit the single vertical note stream; min size 420x520.
- `BLURT_BROWSER=1` opens a browser tab instead, and the same browser path is the
  automatic fallback if the webview backend is missing or fails to start. Attaching a
  window to an already-running server still works (it just presents, no second server).
### 43. Double-clickable `Blurt.app`, created by the install itself (owner: "click and done", "others should install simply")
- Owner wanted to launch from the dock/Applications, not a terminal, AND wanted that to
  be true for anyone who installs, not just this dev checkout. So the bundle is created
  **at runtime by the installed package**, not built from the repo.
- `blurt/installer.py` writes `~/Applications/Blurt.app` whose launcher execs
  `sys.executable -m blurt.cli`. Key move: `sys.executable` is whatever Python the user
  installed Blurt into (pipx venv, `pip --user`, or the install.sh bootstrap venv), and
  `blurt` is a real install there, so `-m blurt.cli` resolves from any CWD. No repo path,
  no `PYTHONPATH` hack, works for every install method. `~/Applications` (per-user) avoids
  needing admin; it still shows in Launchpad/Spotlight.
- Two entry points: `blurt install-app` (explicit, re-runnable) and a silent first-run
  `ensure_installed()` in `cli.py` that drops the app in on the first `blurt` and is a
  no-op afterward. A packaging failure never blocks the actual launch.
- Launching through a real bundle (vs. `python ...` in a terminal) is what makes the menu
  bar say **Blurt** instead of **Python** and gives the dock icon, so no separate fix was
  needed for the name.
- Icon: `scripts/make-icon.py` draws it (Pillow + `iconutil`, build-time only) and writes
  `blurt/assets/Blurt.icns`, which is **shipped in the wheel** (`package-data`) so any
  install can stamp it into the bundle. A dark slate squircle, off-white lowercase "b",
  slate-blue caret beside it (reads "bl" and as a typing cursor), palette from the UI
  accent. Rejected py2app/PyInstaller (heavy, signing-prone) for the thin wrapper.
- Editable-installed the dev `.venv` (`pip install -e .`) so the same code path works in
  development too; dropped the earlier dev-only `scripts/make-macos-app.sh`.
- Still not done: Windows/Linux equivalents, code signing/notarization (Gatekeeper may
  warn on a downloaded-then-quarantined copy; a locally-created bundle is not quarantined).

### 44. Brand splash on every load; the keys tutorial only for a blank pad (owner)
- Previously the "blurt" splash AND the inline keys cheatsheet both played only on a blank
  pad. Owner wanted the brand moment on every load (each launch should feel like opening
  the app) but did not want returning users re-shown the keys.
- Decoupled the two (`app.js` boot): every load runs the splash; the keys are written into
  the pad only when it loads blank (first run or post-erase). With notes present it is a
  brand flash, no tutorial.
- Honored the owner's standing "instant" preference by giving the returning-load splash a
  shorter animation (`splash-quick`, ~0.9s) vs. the full ~1.8s new-pad swell, so it reads
  as a quick flash rather than a wait. Also reset the `run`/`quick` classes after each play
  so the splash can replay (the post-erase intro was previously a no-op on repeat).
- Kept the wordmark "blurt" in the splash rather than the app icon image: it renders
  instantly as text (no image load) and is the existing brand moment.

### 45. Window: screen-relative default size, remembered geometry, "Blurt" menu name (owner, on the real app)
- Owner ran the bundled app and hit three things: the menu bar said **Python**, the window
  felt **narrow**, and he uses both a 27" monitor and a 13" Air, so any fixed size is wrong
  for one of them.
- Menu name: the app menu takes its name from the *running process's* main bundle, and the
  launcher execs an external interpreter, so macOS resolves that to Python's bundle.
  `desktop._set_macos_app_name` overrides `CFBundleName` in the in-memory info dict via
  pyobjc before the window opens (the standard fix for a Python GUI). Fixes the terminal
  `blurt` launch too, not just the bundle.
- Size: dropped fixed dimensions. `_default_geometry` sizes to the current screen
  (`NSScreen.visibleFrame`): ~46% width capped at 1000, ~88% height capped at 1040, with
  floors. Proportional on any display instead of tiny on a big monitor or oversized on a
  laptop.
- Remembered geometry: `window.json` next to the DB stores width/height/x/y, updated on
  the `resized`/`moved` events and flushed on `closing` (a locking event in pywebview, so
  the write completes before the window goes away). On load the saved size is **clamped to
  the current screen** and an off-screen position is dropped (re-center), so a window sized
  on the BenQ still fits the Air. A bad/missing file falls back to the screen default; a
  write failure never blocks the close.

### 46. A conventional macOS menu bar, lowercase brand, icon in the splash (owner)
- The brand is lowercase **blurt** everywhere: bundle renamed `blurt.app` (a pre-1.1
  `Blurt.app` is removed on install), `CFBundleName`/`DisplayName` lowercased, window title
  and in-process app name lowercased.
- First pass over-corrected (stripped the menu to Hide/Quit, dropped About and Help). Owner
  pushback: "do not reinvent the wheel", "keep the necessary ones". Rebuilt the standard
  macOS layout in AppKit (`desktop._build_menu_bar`, on the main thread via `shown` +
  `AppHelper`): **blurt** (About blurt, Hide/Hide Others/Show All, Quit), **Edit** (undo/
  redo/cut/copy/paste/select all, so text editing keeps its shortcuts), **View** (Dark /
  Light, Enter Full Screen), **Window** (Minimize, Zoom), **Help** (blurt Help, blurt on
  GitHub).
- App-specific items reuse the front end over the JS bridge instead of duplicating logic:
  Help and View>Dark/Light call `window.__blurtHelp` / `__blurtTheme` (exposed in app.js),
  which run the existing cheatsheet and theme toggle. A module global pins the action
  controller because NSMenu holds only a weak target.
- Deadlock bug found in testing: menu actions fire on the main UI thread, and pywebview's
  `evaluate_js` blocks that thread waiting on the web view, so Help/Dark-Light froze the
  whole app. Fixed by running the JS call on a worker thread (`_eval_js_async`); the main
  thread stays free to service it.
- About panel: the standard panel reads name/version/copyright from the *running process's*
  bundle, i.e. Python's, so it showed "Version 1.1.0 (3.12.13)" and a PSF copyright.
  `_brand_macos_app` overrides `CFBundleName`/`CFBundleShortVersionString`/`CFBundleVersion`/
  `NSHumanReadableCopyright` in the in-memory bundle info, and the About action passes blurt's
  icon and a blank build-version. Copyright line is the existing tagline "just type. it
  remembers." (owner: do not invent taglines, keep it consistent).
- Help menu is registered via `setHelpMenu_`, so macOS adds its standard search field. Owner
  initially questioned it, then chose to keep it as the platform convention.
- Splash now shows the **app icon** (a 512px PNG written next to the iconset by
  `make-icon.py`, shipped in `static/`) instead of the wordmark, so the launch moment
  matches the dock/app icon. Owner's call, for brand consistency.
- The whole menu build is wrapped so a failure degrades to pywebview's default rather than
  crashing the app.

### 47. File menu (export + reveal), fixed in-window download, scale-checked to 300 (owner)
- Added a **File** menu (between blurt and Edit): "Export as Markdown…" (⌘S, the on-demand
  dated copy) and "Reveal scratchpad.md in Finder" (opens Finder on the always-current
  mirror at `settings.export_md_path`, or its folder if no notes exist yet). Owner asked for
  a simple, obvious place to download and to find the durable file.
- Bug found while answering "how does export work": in the native window `Cmd+S` flashed
  "saved a markdown copy" but saved nothing, because WKWebView ignores `<a download>` blob
  downloads unless `webview.settings['ALLOW_DOWNLOADS']` is on (it defaults off). Turned it
  on, so the download now raises a native Save dialog (defaulting to Downloads). The
  always-on `scratchpad.md` mirror was never affected; only the explicit download was.
- Two export paths, kept distinct: the **mirror** (`scratchpad.md`, continuous, beside the
  DB at `~/.local/share/blurt/`) and the **on-demand copy** (⌘S / File > Export, a dated
  snapshot the user saves anywhere).
- Scale check (owner: "ensure it works for 100+ entries"): seeded 300 notes against a
  throwaway server. Saves stayed instant (300 posted in ~0.2s total; embeddings run in the
  background), indexing drained to 0 pending immediately, search ran ~17ms median, the ghost
  ~18ms median with correct matches, export and the mirror produced the full 22KB in ~1ms,
  and pagination returned all 300. No cliff; the active-only vector index keeps interactive
  paths fast well past 100 entries.

### 48. One export concept (the scratchpad file), a Settings pane, and a plain uninstall (owner)
- Owner found two ways to get Markdown (Cmd+S download vs the scratchpad.md mirror)
  confusing: same content, two buttons. Committed to the Obsidian-vault model, your notes
  ARE scratchpad.md, always current. Removed the on-demand export entirely (Cmd+S handler,
  the JS function, the cheatsheet row, the File-menu item, the in-window download enabling).
  Cmd+S is still swallowed so the browser's save-page dialog never appears. The File menu is
  now one item: "Open scratchpad".
- "Reveal in Finder" was dropped as low-value. Folder choice and update checks moved into a
  proper **Settings** pane (the owner's idea), reached via `blurt > Settings… (⌘,)`. It is an
  in-app panel (same family as the cheatsheet), so it also works in browser mode (minus the
  native folder picker).
- Configurable notes folder: the readable `scratchpad.md` can live in any folder (e.g. inside
  an Obsidian/Dropbox folder). Persisted in `settings.json` beside the DB; `config.notes_dir`
  reads it; `POST /api/notes-dir` re-points the live mirror, writes the file at its new home,
  and removes the stale one. The index DB never moves, so a synced folder is safe. The native
  folder picker is exposed to the web UI via a pywebview `js_api` bridge (`_JsApi.pick_folder`).
- Update check: `GET /api/update-check` reads `__version__` from raw GitHub `main` and
  compares. Owner chose notify-and-show-the-command over a fragile one-click updater, so it
  surfaces `pipx upgrade blurt` with a copy button when a newer version exists.
- Uninstall, kept deliberately simple (owner: "do not over-engineer; if the scratchpad stays
  it stays"). `blurt uninstall` removes the app bundle and prints where the notes are and the
  `pipx/pip uninstall blurt` line. It never deletes notes; no prompt, no purge flag. To make a
  manual trash of blurt.app stick, a `.app-added` marker records that the app was added once,
  so first-run auto-add does not resurrect a bundle the user deliberately deleted (`blurt
  install-app` adds it back on purpose). `blurt uninstall` clears the marker so a future fresh
  install adds it again.

### 49. Cut a real v1.1.0 release; fix the README's pipx/Ollama cliff (owner, end-user test)
- Owner installed Blurt as a fresh end user and hit two snags the README glossed over:
  `pipx` was not installed (README led with it as "the easy way" but never said how to get
  it), and Ollama is a hard prerequisite shown only as a bare link. Fixed the README: Ollama
  is step 1 with a real command (`brew install ollama` or the download), and a "no pipx?
  install it once" block precedes the install command.
- Found a release gap while in there: `pyproject` and STATE said **v1.1.0**, but there was
  **no `v1.1.0` tag or GitHub release**, and `install.sh` was pinned to **v1.0.3**. So
  `pipx install git+...` served current `main`, but the curl installer and tarball paths
  silently served months-old code. Fixed by tagging + releasing **v1.1.0** and bumping
  `install.sh` to match. Release ritual going forward: bump `pyproject` version, tag `vX.Y.Z`,
  cut the GitHub release, and bump `install.sh`'s `VERSION` pin in the same motion so the
  non-pip paths never go stale again.

### 50. `blurt` hands off to blurt.app so the dock identity is right; drop the IP from the launch line (owner)
- Launching `blurt` from a shell ran a bare Python process, so the dock showed the generic
  Python rocket icon AND the hover label "Python". Worse, typing `blurt` (rocket) and then
  opening blurt.app gave two separate dock icons, two identities for one app. The documented
  first-run is literally `pipx install ... && blurt`, so the terminal path is the path that
  has to look right; "just double-click the app instead" is not an answer.
- Real fix: on macOS, `blurt` now re-launches through blurt.app via `open` (`_should_handoff`
  / `_open_app_bundle` in cli.py). LaunchServices stamps the process with blurt's bundle
  identity, so the dock shows blurt's icon and name, identical to double-clicking, and there
  is only ever one instance. The bundle re-enters the launcher with `__CFBundleIdentifier`
  set (LaunchServices), which is the signal to stop handing off and run in-process. The model
  pull / Ollama hint runs in the terminal *before* the handoff so its one-time output stays
  visible. Falls back to the in-process window if the bundle is missing (user trashed it) or
  `open` fails. `_set_dock_icon` (set the app icon image on the main thread) stays as a
  belt-and-suspenders for that fallback path.
- Bug in the first cut of the handoff: the "am I inside the bundle?" guard tested whether
  `__CFBundleIdentifier` was *set*. But terminals set it too (Terminal.app ->
  `com.apple.Terminal`), so the guard thought every terminal launch was already in-bundle and
  skipped the handoff: blurt ran in-process and died with the terminal. Fix: compare the value
  to blurt's own `BUNDLE_ID` (`com.rbsriram.blurt`). LaunchServices sets it to that only when
  blurt.app launched us; a terminal sets its own id (or nothing), so the value, not its
  presence, distinguishes the two. `BUNDLE_ID` is now a shared constant in installer.py (used
  by the plist and the guard). Existing bundles already carry that id, so a package reinstall
  is enough; the on-disk app does not need regenerating.
- Dropped the `http://127.0.0.1:7337` from the "Starting Blurt ..." and "already running"
  lines: a localhost IP printed at a non-coder reads as scary/technical for zero benefit
  (browser mode opens the URL automatically anyway).

### 51. Exact search must survive Ollama being down (owner, end-user test)
- Owner (Ollama not running on the test machine) searched a word that was literally in three
  notes and got "no matches"; the peek never appeared either. Root cause: `Retriever.query`
  computed the exact/lexical hits first but then `await embed_query(q)` for the semantic step,
  and with Ollama unreachable that line raised, so the whole request 500'd and the lexical
  hits it already had were thrown away. This violated the stated invariant that exact-text
  search is instant regardless of Ollama. `suggest` (the peek) had the same fragility: a 500
  on every keystroke instead of just showing nothing.
- Fix: the semantic step is now best-effort in both paths. `query` wraps embed+knn in
  try/except and falls back to lexical-only on failure; `suggest` returns empty. Reproduced
  with `BLURT_OLLAMA_URL=http://localhost:1` (query went 500 -> 200 with the exact hits;
  suggest 500 -> 200 empty). Added two offline regression tests (`_DeadEmbedder` stub) so a
  semantic failure can never again sink exact search. Note: the peek itself still needs Ollama
  running, that is by design (it is the semantic feature); the bug was exact search dying with it.

### 52. Surface "Ollama down" in the UI, not just the terminal (owner, end-user test)
- The deeper issue behind #51: a real user launches the app (never sees the terminal's
  "needs Ollama" line), types, and the peek silently never appears, leaving them to wonder if
  blurt is broken. Silent degradation of the core feature is the actual bug.
- Fix: one faint top banner (`#ollama-bar`), shown only while `/api/status` reports Ollama
  unreachable, reading **"peek is off until Ollama is running."** with **Ollama** as the only
  link (to ollama.com/download). Polled on boot and every 15s, so it clears itself within
  ~15s of Ollama coming up, no reload. No dismiss button: it is purely a live mirror of state,
  and vanishes the moment the state is fixed. Owner steered the design hard here: it started as
  a full-screen overlay with body copy and a "keep going" dismiss, trimmed to a single faint
  line per their call. Copy leans into blurt's own word, "peek", not generic "smart search".
  Sits at z-index 40, under the splash (50), so the brand flash still plays first.

### 53. Gate on Ollama at startup; self-heal the index; show engine state in Settings (owner)
- Owner reframed the whole thing: blurt's peek runs on a local model (Ollama + nomic-embed-text),
  exactly like a voice app leaning on a local STT model (TypeWhisper + Parakeet). A half-working
  "runs without Ollama" mode is what produced the silent failures (notes saved but never indexed),
  so make the dependency explicit instead of papering over it.
- **Startup gate (front end).** Driven by `/api/status`, the pad is blocked on first launch until
  Ollama is reachable AND the model is available (`#ollama-gate` dims the pad, the pill centers
  over it, the composer is disabled). Polls every 3s while degraded, 15s when healthy. The moment
  it is healthy the gate clears and the composer focuses. Crucially, the gate is one-shot: once
  the engine has been healthy (`everHealthy`), a later Ollama drop is NON-blocking, just the
  floating pill, so capture and exact search keep working and the peek resumes on recovery. You
  never get blocked from jotting a note because a background service hiccuped.
- **Self-heal (back end).** The indexer runs a heal loop every `reconcile_interval_s` (8s):
  `embedder.ensure_model()` pulls the model if Ollama is up but lacks it (the launcher only pulls
  at boot, so a mid-session Ollama install would otherwise never get the model), then
  `db.unindexed_active_ids()` finds active notes with no chunks (saved while Ollama was down) and
  re-enqueues them. An `_inflight` set dedupes so reconcile never double-indexes a queued note.
  Verified end to end: a note inserted with no chunks got indexed within ~6s and became peekable
  (score 0.87) against real Ollama.
- **Settings engine row.** Shows live Ollama reachability, the embedding model state
  (ready / downloading / —), and any catch-up indexing count, updated on every poll. Makes the
  local-model dependency a visible, first-class thing rather than hidden plumbing.
- Positioning: Ollama is now a hard requirement to *start* blurt, but the app stays resilient to
  mid-session drops. The earlier exact-search-survives-Ollama fix (#51) still backs that resilience.

### 54. Date-aware capture and search (owner)
- Owner's idea: type "meeting David tomorrow" and find it later by date. Built as a SEARCH
  enhancer, never a task/calendar app: dates make notes findable, never create obligations,
  reminders, or a calendar view. That line is the whole product-identity guardrail.
- **Freeze at capture, never re-resolve.** A date phrase is resolved to an absolute calendar day
  the moment the note is saved (or edited), against the local "today". "tomorrow" written June 9
  is June 10 forever, even if you reopen it in another timezone next month. This sidesteps the
  timezone problem entirely: we never store a draggable instant. Times of day ("5pm") are left as
  verbatim text, because we can only know YOUR capture timezone, not the event's, so any clever
  time translation would be confidently wrong. Dates get smart; times stay literal.
- **Pure, hand-rolled parser** (`core/dateref.py`), not a library: precision over recall is the
  rule (a missed date is invisible, a wrong one erodes trust). Recognises relative phrases
  (today/tomorrow/weekday/this-next-last week+month/in N days/N ago), explicit calendar dates
  (ISO, "Jun 1", "1st of June", "14th of this month"), and numeric dates that carry a year
  (14/2/2024, 12-12-26). Skips bare numbers and yearless slashes ("3/4 cup") to avoid false hits.
  Month-day with no year resolves to THIS year (predictable; type the year to mean another).
- **Ambiguous numeric dates (6/4) follow a user setting**, not magic auto-detection (owner
  explicitly rejected locale auto-detect as over-complex). A Settings toggle picks day-first
  (DMY, default, international) or month-first (MDY, US). Dates whose digits disambiguate (14/2)
  and spelled-out months are unaffected. Changing the setting re-freezes existing notes at once.
- **Storage:** an `entry_dates` table (active-only via the same supersede semantics as search);
  read paths attach a `dates` list. Extraction runs inline on save (pure + fast, no Ollama), so
  it is searchable instantly and works when Ollama is down. A versioned backfill freezes dates on
  pre-existing notes, anchored to each note's OWN creation date, and re-runs when the parser
  version bumps so fixes propagate.
- **Search merges date hits as high-confidence** (score 1.0, `date_match` flag) alongside lexical.
  For a PURE-date query ("tomorrow", "2nd feb") semantic search is skipped, so it returns only
  notes actually on that date plus literal-text matches, no fuzzy day-word noise. Queries with a
  topic ("meeting tomorrow") still use semantic. A search is relative to NOW; notes are absolute,
  so old notes stay findable by their literal word and their real date, not by a stale "tomorrow".
- **UI:** a deliberately subtle muted date label on the left of each note footer (owner steered it
  from an accent pill with a calendar icon down to barely-there text), kept clear of the saved-time
  on the right so the two never read as a confusing pair. Clicking it searches that exact day.

### 55. Encrypted secrets, keyboard-first delete, flat UI (owner)
- Owner jots credentials into the pad, so make that safe. Framed as "a safer place to jot a
  credential than a plaintext note," NOT a password manager (see docs/SECURITY.md threat model).
- **Crypto:** the secret VALUE is encrypted with Fernet (AES-128-CBC + HMAC); the key lives in the
  OS keychain via `keyring` (no master password, to keep it frictionless). The note's `content` is
  only the label, so the value never touches the `scratchpad.md` mirror or the embedding index, and
  the "content is verbatim" invariant holds. Value lives in a separate `secrets` table; reads attach
  an `is_secret` flag. The key is per-machine by design.
- **No `type=password`:** a real password field makes macOS iCloud Passwords pop autofill UI that
  Apple won't suppress via attributes. We mask a plain text field with CSS (`-webkit-text-security`)
  instead, plus `-webkit-appearance:none` to kill WKWebView's native field underline.
- **Lifecycle, all keyboard:** add via `Cmd/Ctrl+K` or `/secret` (one-line `key │ secret` form that
  replaces the compose box); click a saved secret to edit it in place (re-encrypts via
  `PATCH /api/secrets/{id}`); empty it + Enter to delete; click the dots to copy (clipboard
  auto-clears ~20s, best effort), "show" to reveal. Shown masked in the stream, search, and the peek.
- **Two general UX changes rode along (kept, they're improvements):** (1) a note is deleted by
  emptying it and pressing Enter, like a text pad, so the per-note `×` button was removed; (2) Esc is
  now "back to typing" from anywhere. Owner's standing direction throughout: keyboard-first with click
  as the secondary path, bare UI with no disclaimers/footnotes/panels (flat slash menu, flat secret
  form, no accent borders), and in-app help kept current with features so he isn't the QA.
- Shipped in v1.3.0. The "update now" in-app updater is parked as the next experiment (the current
  "check for updates" copies the `pipx upgrade` command).

### 56. ↑ from an empty box walks the stream itself (owner, end-user test)
- The peek (the as-you-type match popup) only appears while you're typing. From an empty box there was
  no keyboard way into your existing notes, you had to reach for the mouse to edit a recent one. That
  broke the keyboard-first promise for the most common "fix what I just wrote" case.
- **The interaction:** in an empty compose box, `↑` highlights the newest note in the stream; `↑` again
  steps older, `↓` newer (`↓` past the newest drops back to the box), `enter` opens the highlighted
  note for editing in place, `esc` returns to the box. Any typed character exits browse mode and types
  normally, so it never gets in the way of capture.
- **Why walk the stream, not a popup:** an earlier cut of this experiment surfaced recent notes in a
  separate peek-style popup. It felt like a second, redundant surface over the stream that's already on
  screen. Highlighting the real stream entries (a left accent bar, `.entry.nav-focus`) is less to build,
  less to learn, and matches how the note will look when you open it. The popup cut was dropped.
- **Coexists with the peek:** `Cmd/Ctrl+↑` still browses the peek's semantic matches while typing; bare
  `↑` is only bound when the box is empty, so the two never collide. Cheatsheet updated in the same change.
- Shipped in v1.4.0.

### 57. "Coming up": upcoming dated notes, summoned on demand (CTO, owner feedback)
- The append-only stream's one real weakness: a commitment you dated days ago sinks below newer notes and
  you never see it again unless you search. For the owner (ADHD), that's the failure mode that matters.
  The counterweight is resurfacing, not organizing.
- **First cut (rejected) showed it automatically on every open, with a week-back-to-week-ahead window.**
  Owner's feedback killed both halves: an always-on list "becomes so huge you are lost," and a backward
  window surfaces stale things ("call plumber yesterday, the damn thing is already old") which reads like
  the nagging to-do list we refuse to be. Lesson logged so we don't rebuild it that way.
- **What it is now:** summoned with the `/upcoming` command (in the `/` menu like `/secret`), never shown
  on its own. A compact "coming up" card appears just above the input (the `#today` slot) listing active
  notes dated **today or later**, soonest first, capped at 15. Keyboard-first: `↑↓` move, `enter` reveals
  the focused note in the stream (scroll + shared `focus-flash`, the stream stays the one edit surface),
  `esc` or any keystroke closes. Click also works. New `GET /api/radar` reuses `entries_in_ranges`.
- **Mostly forward, with a 2-day grace back (owner):** the list is notes dated today or later, plus a
  tight 2-day backward window so something you *just* missed (yesterday or the day before) still catches
  your eye. Older overdue stays sunk, so it can't fill with things you long passed; the cap keeps it
  short. Missed rows (dated before today) get a quiet warm tint (`--warn`) on their date so "missed"
  reads differently from "upcoming" at a glance, no alarm colors, no extra words. The window is computed
  from the server's `date.today()`, returned so the UI marks missed vs upcoming against the same day.
- **NOT a task app (holds the line from #54):** no done-state, no reminders, no notifications. It only
  re-reads dates frozen at capture and makes them visible. A recall enhancer, never a to-do list.
- **Discoverability via the `/` menu, not the cheatsheet:** it's a command, so it self-documents in the
  `/` menu exactly like `/secret` (which also isn't in the `?` cheatsheet). No cute prose blurb.
- Shipped in v1.5.0.

### 58. Monochrome ink palette + a calmer cheatsheet (owner)
- The accent was a slate blue (`#41698f`) on warm paper (`#f1ece3`). Owner never liked the pairing and
  wanted "the same colour the text is captured in, kept subtle", i.e. no hue at all.
- **Palette is now monochrome, app-wide:** `--accent` is the text ink itself (`#1a1a1a` light, `#e8e6e0`
  dark), and `--selection` / `--highlight` / the one stray glow are neutral greys. Everything that was
  blue (links, date chips, the peek, focus bars, checked boxes, cheatsheet keys) is now the same ink as
  the writing. Affordances survive without colour: links keep their underline, focus keeps its left bar,
  checked boxes keep their fill. The only sanctioned non-ink colour is a *functional status* cue (e.g. the
  "missed" date tint from #57), kept deliberately as the lone exception.
- **Cheatsheet slimmed:** it was a 900px slab (48px padding, 16px type, a four-column grid, a heavy
  shadow), too big and in-your-face. Now a ~440px single-column card with smaller type, less padding, and
  a soft shadow. Same content, a quarter of the visual weight, reads as a quiet reference, not a wall.
- Shipped in v1.5.0.
