# SCRATCHPAD — Product Requirements Document
**Version:** 1.0  
**Status:** Ready for Build  
**Target:** Claude Code autonomous build  
**Goal:** Ship MVP in 2 days

---

## 1. What This Is

A local-first, open-source personal scratchpad with intelligent retrieval. One continuous append-only stream of text — no folders, no tags, no organization required. The intelligence lives entirely in the retrieval layer, not in how you input.

The single UX innovation: **as you type, the system silently detects if you're writing something that conflicts with or updates existing information, and surfaces the existing entry as a faint ghost above your input area — keyboard dismissible, zero mouse required.**

---

## 2. Core Principles

1. **Input is always frictionless** — never interrupt the user while typing
2. **No LLM API required** — everything runs locally, zero ongoing cost
3. **Keyboard first** — no mandatory mouse interactions
4. **Single stream** — one append log, forever, no threads or folders
5. **Open source** — MIT license, GitHub-ready from day one

---

## 3. Tech Stack

### Required (all free, all local, all open source)
- **Runtime:** Python 3.11+
- **Web UI:** FastAPI backend + plain HTML/CSS/JS frontend (no React, no build step)
- **Storage:** SQLite (single `.db` file, portable, zero config)
- **Embeddings:** `nomic-embed-text` via Ollama (runs locally, free after install)
- **Vector search:** `sqlite-vec` extension (vector search inside SQLite, no separate vector DB)
- **Semantic search at query time:** pure retrieval, no LLM needed
- **Temporal conflict resolution at query time:** optional, route to local Ollama model (llama3.2:3b or similar small model) — user configurable, defaults OFF

### Optional (user can enable)
- **Voice input:** `whisper.cpp` local transcription — user installs separately, documented in README
- **Cloud LLM fallback:** Anthropic/Gemini API for query synthesis — user provides own key, never required

### What to explicitly NOT use
- No cloud vector DBs (Pinecone, Weaviate, etc.)
- No mandatory OpenAI/Anthropic API
- No Electron (keep it browser-based localhost)
- No React/Vue/build pipeline for MVP
- No Docker requirement (optional compose file is fine, not mandatory)

---

## 4. Application Architecture

```
scratchpad/
├── main.py                  # FastAPI app entry point
├── config.py                # User config (ollama URL, model names, thresholds)
├── db/
│   ├── schema.sql           # SQLite schema
│   └── database.py          # DB connection + queries
├── core/
│   ├── embedder.py          # Ollama embedding calls
│   ├── indexer.py           # Chunk, embed, store on entry save
│   ├── retriever.py         # Semantic search + ghost suggestion logic
│   └── synthesizer.py       # Optional: LLM synthesis for query answers
├── api/
│   └── routes.py            # All FastAPI endpoints
├── static/
│   ├── index.html           # Single page app
│   ├── style.css
│   └── app.js
├── requirements.txt
├── setup.sh                 # One-command setup script
└── README.md
```

---

## 5. Database Schema

```sql
-- Main entries table
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_superseded INTEGER DEFAULT 0,  -- 0 = active, 1 = marked old
    superseded_by INTEGER REFERENCES entries(id),
    superseded_at DATETIME
);

-- Chunks for vector search (one entry = multiple chunks)
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER REFERENCES entries(id),
    chunk_text TEXT NOT NULL,
    chunk_index INTEGER,  -- position within entry
    embedding BLOB,       -- stored as sqlite-vec float32 vector
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast entry lookup
CREATE INDEX idx_entries_created ON entries(created_at);
CREATE INDEX idx_entries_active ON entries(is_superseded);
CREATE INDEX idx_chunks_entry ON chunks(entry_id);
```

---

## 6. Core Features — Detailed Spec

---

### 6.1 The Scratchpad Input Area

**UI layout (top to bottom):**
```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  [ghost suggestion zone — appears/disappears]       │
│                                                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Your text input area (large, minimal)             │
│                                                     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Input area behavior:**
- Full-width textarea, takes ~60% of viewport height
- Monospace or clean sans-serif font, comfortable reading size (16px+)
- No placeholder text beyond a single subtle hint on first launch
- `Ctrl+Enter` or `Cmd+Enter` to save entry
- `Enter` = newline (this is a scratchpad, multi-line entries are normal)
- Auto-saves draft to localStorage so nothing is lost on accidental close

**On save:**
1. Entry written to SQLite with timestamp
2. Entry chunked (see §6.3) and embedded asynchronously in background
3. Input area clears
4. Saved entry appears in the stream below immediately

---

### 6.2 Ghost Suggestion System (The Core Feature)

**Trigger condition:**
- User has typed ≥ 5 words in the input area
- A debounce of 400ms has elapsed since last keystroke
- Background semantic search fires against active (non-superseded) entries
- Top result similarity score exceeds threshold (default: 0.78, configurable in config.py)

**What appears:**
- Above the input area, in a dedicated ghost zone
- Faint text — opacity 0.35, slightly smaller font than input
- Shows: `[timestamp] · [first 120 chars of matching entry]`
- No border, no card, no shadow — just text sitting there
- Fades in over 150ms, non-jarring

**Example:**
```
  tue mar 4 · raj's number is 050-1111...          ← ghost text, faint
──────────────────────────────────────────────────
  Raj's number is 050-2|                           ← user typing
```

**Keyboard interactions with ghost:**
- **Keep typing** → ghost auto-dismisses after 3 seconds of continued typing
- **`↑` arrow key** → focus jumps to ghost entry in the stream below for inline edit
- **`Esc`** → dismisses ghost immediately, user continues typing
- **`Ctrl+U` (configurable)** → marks existing entry as superseded, user continues typing new entry which will replace it on save

**Ghost rules:**
- Maximum ONE ghost shown at a time — never a list
- Only surfaces entries with `is_superseded = 0`
- If the top match is the entry the user is currently editing (inline edit mode), skip it
- Ghost does NOT fire when: user is in query mode, user is doing inline edit, input is < 5 words

**Confidence threshold behavior:**
- Score 0.78–0.85: show ghost at opacity 0.30 (low confidence, very subtle)
- Score 0.85–0.95: show ghost at opacity 0.40 (medium confidence)
- Score > 0.95: show ghost at opacity 0.50 + thin left border accent (high confidence — something very similar exists)
- User can adjust base threshold in settings

---

### 6.3 Chunking Strategy

Each saved entry is chunked before embedding:

- If entry is ≤ 100 words: treat as single chunk
- If entry is > 100 words: sliding window chunks of 80 words, 20-word overlap
- Each chunk stores its `entry_id` so retrieval maps back to the full entry
- Embeddings generated via `nomic-embed-text` through Ollama API (`POST /api/embeddings`)
- Embedding happens asynchronously after save — UI does not wait for it

---

### 6.4 The Stream View

Below the input area, all entries shown in reverse chronological order (newest first):

- Each entry shows: relative timestamp ("2 hours ago", "Tuesday", "Mar 4") + full content
- Superseded entries: shown with strikethrough + muted color (still visible for history, but clearly inactive)
- Clicking any entry → inline edit mode (contenteditable, saves on `Ctrl+Enter`)
- On inline edit save: old entry marked `is_superseded=1`, new entry created with same content + edits
- Entries are paginated: load 50 at a time, infinite scroll upward

---

### 6.5 Query / Retrieval

**UI:** A search bar at the top of the page, always visible. Distinct from the scratchpad input — different visual treatment.

**Query flow:**
1. User types natural language query, hits `Enter`
2. Query is embedded via Ollama
3. Vector search against `chunks` table (only from active entries where `is_superseded=0`)
4. Top 5 chunks retrieved, deduped back to their parent entries
5. Results shown inline below search bar with relevance-ordered entries highlighted in stream

**Result display:**
- Matched entries highlighted in the stream with a subtle accent
- Matching text within entry bolded
- Timestamp shown prominently
- If multiple chunks from same entry matched, shown once (not duplicated)

**Optional LLM synthesis (disabled by default):**
- If user has Ollama running with a chat model (llama3.2:3b minimum)
- A "Synthesize answer" button appears below results
- Sends top 5 chunks + query to local model
- Returns a synthesized answer in natural language
- Handles temporal conflicts: prompt instructs model to prefer most recent information
- Never calls external API unless user explicitly configures one in config.py

---

### 6.6 Supersede / State Management

**Three ways an entry gets marked superseded:**

1. **Manual inline edit** — user edits entry directly in stream, old version auto-superseded
2. **Keyboard shortcut during ghost** — `Ctrl+U` while ghost is showing marks the ghosted entry superseded, new entry will replace it
3. **Explicit command** — user types `//supersede [search term]` in input to manually find and mark an entry

**Superseded entries:**
- Remain in stream (history is preserved)
- Visually distinguished (strikethrough, muted)
- Excluded from ghost suggestions and search results
- Can be un-superseded by clicking a restore option on the entry

---

### 6.7 Voice Input (Optional, documented but not default)

- Requires user to install `whisper.cpp` separately
- If binary detected at configured path, a microphone icon appears in input area
- Hold `Ctrl+Shift+V` to record, release to transcribe
- Transcribed text appended to current input area content
- Whisper model: `base.en` by default (fast, accurate enough for notes)

---

## 7. API Endpoints

```
POST   /api/entries          # Save new entry
GET    /api/entries          # Get paginated stream (params: limit, offset)
PATCH  /api/entries/{id}     # Inline edit
DELETE /api/entries/{id}     # Soft delete (marks superseded)

POST   /api/suggest          # Ghost suggestion — body: {text: string}
                             # Returns: {match: Entry|null, score: float}

POST   /api/query            # Search — body: {query: string}
                             # Returns: {entries: Entry[], chunks: Chunk[]}

POST   /api/synthesize       # Optional LLM synthesis — body: {query, entry_ids[]}
                             # Returns: {answer: string} or 503 if no LLM configured

GET    /api/status           # Health check — ollama status, entry count, index status
```

---

## 8. Configuration (config.py)

```python
# Ollama settings
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "llama3.2:3b"  # optional, for synthesis
CHAT_ENABLED = False  # user must explicitly enable

# Ghost suggestion settings
GHOST_SIMILARITY_THRESHOLD = 0.78
GHOST_DEBOUNCE_MS = 400
GHOST_MIN_WORDS = 5
GHOST_MAX_CHARS_SHOWN = 120

# Chunking
CHUNK_SIZE_WORDS = 80
CHUNK_OVERLAP_WORDS = 20

# App
DB_PATH = "./scratchpad.db"
PORT = 7337
```

---

## 9. Setup & Installation

### setup.sh should do:
```bash
# 1. Check Python 3.11+
# 2. Create virtualenv
# 3. pip install requirements
# 4. Check Ollama is installed (warn if not, provide install link)
# 5. Pull nomic-embed-text model via ollama pull
# 6. Initialize SQLite DB from schema.sql
# 7. Load sqlite-vec extension
# 8. Print: "Run: python main.py — then open http://localhost:7337"
```

### README must cover:
- Prerequisites: Python 3.11+, Ollama
- One-command install: `./setup.sh`
- Optional: enabling voice (whisper.cpp path)
- Optional: enabling LLM synthesis (ollama chat model or API key)
- How to back up (just copy `scratchpad.db`)
- How to contribute

---

## 10. Non-Functional Requirements

- **Ghost suggestion latency:** < 500ms from debounce trigger to display (on M1/M2/M4 Mac with Ollama running)
- **Save latency:** < 50ms (write to DB is instant; embedding is async background)
- **Query latency:** < 1s for semantic search (no LLM), < 5s with local LLM synthesis
- **DB size:** sqlite-vec keeps things compact; 10,000 entries with embeddings ≈ ~200MB
- **Memory footprint:** App itself < 100MB RAM (Ollama is separate process, user's responsibility)
- **No telemetry, no analytics, no network calls** except to localhost Ollama

---

## 11. MVP Scope — What's In v1.0

| Feature | In v1 |
|---|---|
| Append-only scratchpad input | ✅ |
| Ghost suggestion (semantic, keyboard-only) | ✅ |
| Stream view with timestamps | ✅ |
| Semantic search / query | ✅ |
| Inline edit with auto-supersede | ✅ |
| Superseded entry visual treatment | ✅ |
| Local embeddings via Ollama | ✅ |
| SQLite + sqlite-vec storage | ✅ |
| Optional local LLM synthesis | ✅ |
| setup.sh one-command install | ✅ |
| Voice input | ❌ v2 |
| Mobile UI | ❌ v2 |
| Daily digest / reminders | ❌ v2 |
| Export to markdown | ❌ v1.1 |
| Multi-device sync | ❌ v3 |
| Browser extension capture | ❌ v2 |
| Image paste + OCR search | ❌ v2 |
| Markdown rendering in stream | ✅ v1 |
| Table support (markdown syntax) | ✅ v1 |

---

## 12. Feature Roadmap

### v1.0 — The Scratchpad (MVP, 2 days)
Core loop only. Capture, ghost suggestion, semantic search, inline edit, supersede. Markdown + table rendering in stream. Nothing else.

### v1.1 — Polish & Export
- **Export to Markdown** — single `Cmd+E` exports full stream as `.md` file:
  ```markdown
  ## 2026-03-04 14:32
  Raj's number is 050-2222

  ## 2026-03-03 09:11
  | Item | Status |
  | --- | --- |
  | Project Alpha | Delayed 2 weeks |
  ```
- **Export filtered** — export only results from a search query as markdown
- **Configurable ghost threshold** — minimal settings panel (`Cmd+,`)
- **Keyboard shortcut overlay** — `?` key shows hotkey reference, `Esc` dismisses

### v2.0 — Capture Everywhere
- **Voice input** — local `whisper.cpp`. Hold hotkey, speak, transcribed text appended to input. Model: `base.en`. No cloud, no cost.
- **Image paste + OCR** — paste or drag image into scratchpad. Local OCR via `tesseract` extracts text, stored as searchable content alongside image blob. Ghost suggestion and search work on OCR'd text.
- **Mobile web UI** — responsive layout for phone browser pointing at your home server. Capture-optimized: big input, minimal chrome.
- **Browser extension** — highlight text on any webpage, hit extension button, dumps to scratchpad with source URL as metadata. Chrome + Firefox.

### v2.1 — Intelligence Layer
- **Daily digest** — morning cron synthesizes past 24hrs entries. Surfaces anything with a date/deadline. Output: terminal, optional Telegram message (KIRAN integration hook), or `/digest` page.
- **Deadline detection** — passive scan for date patterns ("by Friday", "July 15th"). Faint date indicator on flagged entries in stream. No reminders, just visibility.
- **Entity timeline view** — query an entity ("Raj", "Project Alpha") → see all related entries chronologically including superseded ones.

### v3.0 — Connected & Synced
- **Multi-device sync** — two modes:
  - Self-hosted via Tailscale (Mac Mini as server, phone/laptop as clients — natural fit for your setup)
  - Git-backed: `scratchpad.db` synced via private git repo
- **REST API with token auth** — lets other tools append to scratchpad (KIRAN, Telegram bot, etc.)
- **Telegram bot capture** — send message to private bot → appends to scratchpad. Zero friction mobile capture.
- **Import** — bulk import from plain text, Notion export, Apple Notes, Obsidian vault. Strips all structure, flattens to stream.

### v4.0 — Ecosystem
- **Plugin hooks** — `on_save`, `on_query`, `on_supersede`. Community builds integrations without touching core.
- **MCP server** — expose scratchpad as MCP tool so AI assistants can read/write directly.
- **Self-hostable Docker** — one-command deploy on any VPS.
- **Community themes** — purely CSS overrides, core stays untouched.

### Features Explicitly Never Planned
Closed as feature requests to protect product vision:
- Folders / categories / tags
- Rich text / WYSIWYG editor
- Collaboration / shared scratchpads
- Cloud storage by default
- Native mobile app (mobile web is enough)

---

## 12. Open Source Setup

- License: MIT
- Repo name suggestion: `scratchpad` or `dumpster` (working title)
- `.github/` folder with: `CONTRIBUTING.md`, issue templates (bug, feature), PR template
- GitHub Actions: simple CI that runs on push — lints Python, checks imports, no test suite required for v1
- README badge: "runs locally · zero API cost · MIT license"

---

## 13. Security

### V1 Threat Model
This is a localhost-only personal tool. The threats are real but bounded:
- Someone on your local network accessing the app
- Malicious content injected via paste (XSS)
- Your scratchpad DB being readable if your machine is compromised

### What Must Be Implemented in V1

**Localhost binding — non-negotiable**
- FastAPI binds to `127.0.0.1` only, never `0.0.0.0`
- If user tries to expose it externally, README explicitly warns them

**XSS prevention**
- All entry content HTML-escaped before rendering in stream
- Markdown renderer must use a sanitizing library — use `DOMPurify` on the frontend before injecting any rendered HTML
- Never use `innerHTML` with raw user content — always sanitize first

**No arbitrary code execution**
- Markdown renderer: allow only safe tags (bold, italic, tables, code blocks, links)
- Explicitly strip: `<script>`, `<iframe>`, `<object>`, event handlers (`onclick` etc.)

**DB file permissions**
- `setup.sh` sets `scratchpad.db` to `chmod 600` on creation — owner read/write only
- Document this in README

### V2 (when you expose beyond localhost)
- Token-based auth — single static token in config, passed as header
- HTTPS only via self-signed cert or Tailscale's built-in TLS
- Rate limiting on all endpoints
- This is mandatory before Tailscale/multi-device exposure — do not skip

### What V1 Explicitly Does Not Need
- User accounts / passwords (single user, localhost)
- CSRF protection (no cookies, no sessions)
- Encryption at rest (out of scope for v1 — user's disk encryption handles this)

---

## 14. UI / UX Design Spec

### The Philosophy
Looks like Notepad. Feels like magic. Every piece of UI that isn't absolutely necessary does not exist. The intelligence is invisible until it needs to surface.

### Visual Design

**Default (Light mode):**
- Background: `#F9F7F4` — off-white, like actual paper. Not pure white.
- Text: `#1A1A1A` — near black
- Font: `iA Writer Mono` or `JetBrains Mono` or `Courier Prime` — monospace, feels like a typewriter
- Font size: 17px, generous line height (1.7)
- No borders anywhere. No boxes. No cards. No shadows.
- No sidebar. No toolbar. No icons unless absolutely necessary.
- Cursor: blinking, normal text cursor. Nothing fancy.

**Dark mode:**
- Background: `#0F0F0F` — almost black, not pure black
- Text: `#E8E6E0` — warm off-white
- Toggle: `Ctrl+Shift+D` — no button visible, just the hotkey. Or a single tiny moon/sun icon in the far bottom corner at 30% opacity.

**Layout — the entire page:**
```
┌─────────────────────────────────────────────────────┐
│                                                     │
│                                                     │
│   [ghost zone — empty unless suggestion active]     │
│                                                     │
│  ─────────────────────────────────────────────────  │
│                                                     │
│   Your text here. Just type.                       │
│   |                                                 │
│                                                     │
│                                                     │
│  ─────────────────────────────────────────────────  │
│                                                     │
│   tue mar 4 · raj's number is 050-1111             │
│                                                     │
│   mon mar 3 · met with accountant. tax deadline    │
│   july 15th. need to send Q4 statements.           │
│                                                     │
│   sat mar 1 · gate code is 44321                   │
│                                                     │
└──────────────────────── / ──────────────────────────┘
```

That's it. That's the whole UI.

### What Is Never Visible By Default
- No app name / logo on screen
- No menu bar
- No save button (auto-saves, Cmd+Enter to commit)
- No word count, no character count
- No formatting toolbar
- No tags, no folders, no categories
- No settings gear (settings live in config.py)
- No loading spinners (operations are fast enough to not need them)

### What Appears Only When Needed

**Ghost suggestion** (see §6.2):
- Appears above the divider line, faint, monospace, same font smaller
- Format: `[relative time] · [first 120 chars]...`
- Opacity: 0.30–0.50 depending on confidence
- No animation except a 150ms fade-in
- Disappears silently — no animation out, just gone

**Search bar:**
- Hidden by default
- `Ctrl+F` or `Cmd+F` → a single minimal input slides down from the top (or appears inline at top)
- Same font as everything else
- `Esc` to close
- Results highlighted in the stream below — no separate results panel

**Entry hover state:**
- On hover over a stream entry: timestamp becomes slightly more visible, a faint underline appears
- Click → inline edit mode, entry becomes editable in place
- No edit icon, no delete button visible until hover

### The Divider
A single `1px` hairline divider separates the input area from the stream. That is the only structural element in the entire UI. Color: `rgba(0,0,0,0.08)` light / `rgba(255,255,255,0.08)` dark.

### Superseded Entries in Stream
- Strikethrough text
- Opacity reduced to 0.40
- Still readable if you look, but clearly inactive
- No badge, no label — just the visual treatment

### Scrolling
- Stream scrolls naturally
- Input area stays fixed — it does not scroll with the stream
- New entries appear at the top of the stream (newest first), no animation, just there

### The One Allowed Indulgence
A single small `/` character centered at the very bottom of the viewport, `opacity: 0.15`. Acts as a subtle visual anchor. Nothing functional. Just a quiet signature. Can be removed if user hates it.

### Font Loading
Load from Google Fonts or bundle locally:
- Primary: `JetBrains Mono` (free, open source, excellent readability)
- Fallback: `'Courier New', monospace`

### Keyboard Shortcut Reference (not shown in UI — only in README)
| Key | Action |
|---|---|
| `Cmd/Ctrl + Enter` | Save current entry |
| `↑` | When ghost visible: focus matching entry |
| `Esc` | Dismiss ghost / exit search / cancel edit |
| `Ctrl+U` | Mark ghosted entry as superseded |
| `Cmd/Ctrl + F` | Open search |
| `Ctrl+Shift+D` | Toggle dark/light mode |
| `Ctrl+Shift+V` | Voice input (if configured) |

---

## 14. Claude Code Instructions

**Build order:**
1. `schema.sql` + `database.py` — get storage working first
2. `embedder.py` — verify Ollama connection and embedding roundtrip
3. `indexer.py` + `retriever.py` — chunking, storing, searching
4. FastAPI routes — all endpoints working, test with curl
5. Frontend `index.html` + `style.css` + `app.js` — build UI last
6. Ghost suggestion frontend logic — debounce, opacity levels, keyboard handling
7. `setup.sh` + `README.md`
8. End-to-end test: save 10 entries, query them, verify ghost fires correctly

**Critical paths to get right:**
- sqlite-vec extension loading (it's a loadable extension, not a pip package — handle this carefully)
- Async embedding after save (must not block the UI response)
- Ghost debounce (400ms, cancel previous request if new keystroke arrives)
- Keyboard event handling in JS (↑ key must not scroll page when ghost is active)

**Do not over-engineer:**
- No authentication
- No multi-user
- No plugin system
- No settings UI in v1 — config.py is enough
- No test suite required for v1 — correctness over coverage for now
