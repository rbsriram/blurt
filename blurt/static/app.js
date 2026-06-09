"use strict";
/* Blurt frontend. Vanilla JS, no build step, no network beyond this origin.

   Layout (UX.md §1): input pinned to the bottom, stream scrolls up above it
   (column-reverse, newest nearest the input), the peek sits between them.

   The peek (UX.md §2-3) is a keyboard-browsable list of existing notes that
   resemble what you are typing. You enter it with Cmd/Ctrl+Up (or bare Up on a
   single-line input), cycle with the arrows, and act on the focused line inline
   — edit (Enter), delete (Cmd/Ctrl+Delete), copy (Cmd/Ctrl+C) — without the stream
   ever scrolling. The cursor stays in the input the whole time.

   Security: entries render through md(), which HTML-escapes the entire string
   BEFORE applying any formatting and only ever emits tags we construct
   ourselves. User content can never become live markup, so no sanitizer needed. */

// ---------------------------------------------------------------- API
const api = {
  async get(url) { const r = await fetch(url); return r.json(); },
  async post(url, body, signal) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
    return { ok: r.ok, status: r.status, data: r.ok ? await r.json() : null };
  },
  async patch(url, body) {
    const r = await fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    return { ok: r.ok, status: r.status, data: r.ok ? await r.json() : null };
  },
  async del(url) { const r = await fetch(url, { method: "DELETE" }); return { ok: r.ok, status: r.status }; },
};

// ---------------------------------------------------------------- markdown (escape-first, XSS-safe)
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function anchor(href, text) {
  // href/text are already HTML-escaped (md escapes first); only `"` needs guarding
  // for the attribute. Links open in a new tab; Cmd/Ctrl-click in the stream follows.
  return `<a href="${href.replace(/"/g, "%22")}" target="_blank" rel="noopener noreferrer" title="${MOD}-click to open">${text}</a>`;
}

function linkify(s) {
  // One pass handles, left-to-right (each URL consumed once): markdown links, bare
  // http(s) URLs, bare www. URLs, and emails. Trailing sentence punctuation is left
  // outside the link so "see https://x." doesn't swallow the period.
  return s.replace(
    /\[([^\]]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<]+)|(\bwww\.[^\s<]+)|([\w.+-]+@[\w-]+\.[\w.-]+)/gi,
    (m, mdText, mdUrl, http, www, email) => {
      if (mdUrl !== undefined) {
        return /^(https?:|mailto:|\/)/.test(mdUrl) ? anchor(mdUrl, mdText) : m;
      }
      if (http !== undefined) { const [u, t] = peelTrailingPunct(http); return anchor(u, u) + t; }
      if (www !== undefined) { const [u, t] = peelTrailingPunct(www); return anchor("http://" + u, u) + t; }
      if (email !== undefined) return anchor("mailto:" + email, email);
      return m;
    },
  );
}
function peelTrailingPunct(url) {
  const m = url.match(/^(.*?)([.,!?]+)$/);
  return m ? [m[1], m[2]] : [url, ""];
}

function inlineMd(s) {
  // Split on `code spans` and format only the non-code parts, so a URL or ** inside
  // backticks stays literal. Keeping the delimiter keeps parts alternating text/code.
  return s.split(/(`[^`]+`)/g).map((part) => {
    if (part.length >= 2 && part.startsWith("`") && part.endsWith("`")) {
      return `<code>${part.slice(1, -1)}</code>`;
    }
    part = part.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    part = part.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return linkify(part);
  }).join("");
}

function md(raw) {
  const lines = escapeHtml(raw).split("\n");
  const out = [];
  let i = 0;
  let cbIndex = 0;   // 0-based ordinal of each checkbox, top-to-bottom; the server toggles by this
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line.trim())) {
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre><code>${buf.join("\n")}</code></pre>`); continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) { const n = h[1].length; out.push(`<h${n}>${inlineMd(h[2])}</h${n}>`); i++; continue; }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { out.push("<hr/>"); i++; continue; }
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const cells = (r) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      const head = cells(line); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") { rows.push(cells(lines[i])); i++; }
      let t = "<table><thead><tr>" + head.map((c) => `<th>${inlineMd(c)}</th>`).join("") + "</tr></thead><tbody>";
      for (const r of rows) t += "<tr>" + r.map((c) => `<td>${inlineMd(c)}</td>`).join("") + "</tr>";
      out.push(t + "</tbody></table>"); continue;
    }
    if (/^&gt;\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) { buf.push(lines[i].replace(/^&gt;\s?/, "")); i++; }
      out.push(`<blockquote>${inlineMd(buf.join(" "))}</blockquote>`); continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const tag = ordered ? "ol" : "ul";
      const buf = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) { buf.push(lines[i]); i++; }
      const lis = buf.map((raw) => {
        // A `-`/`*` item whose content is `[ ]`/`[x]` becomes a clickable checkbox.
        const cb = raw.match(/^\s*[-*]\s+\[([ xX])\]\s?(.*)$/);
        if (cb) {
          const checked = cb[1].toLowerCase() === "x";
          // data-cbi must match the server's ordinal: count every checkbox we emit.
          return `<li class="task"><span class="checkbox${checked ? " checked" : ""}" `
            + `data-cbi="${cbIndex++}" role="checkbox" aria-checked="${checked}"></span>`
            + `<span class="task-text">${inlineMd(cb[2])}</span></li>`;
        }
        return `<li>${inlineMd(raw.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>`;
      });
      out.push(`<${tag}>` + lis.join("") + `</${tag}>`); continue;
    }
    if (line.trim() === "") { i++; continue; }
    const buf = [line]; i++;
    while (i < lines.length && lines[i].trim() !== "" &&
           !/^(#{1,3}\s|```|&gt;\s?|\s*([-*]|\d+\.)\s+)/.test(lines[i]) &&
           !/^(-{3,}|\*{3,})$/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
    out.push(`<p>${inlineMd(buf.join("<br/>"))}</p>`);
  }
  return out.join("\n");
}

// ---------------------------------------------------------------- time
function relTime(iso) {
  const then = new Date(iso), now = new Date();
  const s = Math.max(0, (now - then) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400 && now.getDate() === then.getDate()) return `${Math.round(s / 3600)}h ago`;
  if ((now - then) / 86400000 < 7) return then.toLocaleDateString(undefined, { weekday: "long" }).toLowerCase();
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" }).toLowerCase();
}

// A date a note refers to, frozen at capture (server-side dateref). Parse the ISO
// as a LOCAL day (not UTC) so "tomorrow" never slips a day, then label it the way
// you'd say it: today / tomorrow / a weekday name / "jun 15".
function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const when = new Date(y, m - 1, d), now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((when - today) / 86400000);
  if (diff === 0) return "today";
  if (diff === 1) return "tomorrow";
  if (diff === -1) return "yesterday";
  if (diff > 1 && diff < 7) return when.toLocaleDateString(undefined, { weekday: "short" }).toLowerCase();
  const opts = { month: "short", day: "numeric" };
  if (when.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return when.toLocaleDateString(undefined, opts).toLowerCase();
}

// A faint label showing the soonest date a note names ("+N" if it names more).
// Kept barely-there (muted, no pill) so it doesn't compete with the note; it
// brightens on hover to signal it's clickable, and clicking finds that day.
// Returns null when the note has no dates, so callers can append unconditionally.
function dateChip(dates) {
  if (!dates || !dates.length) return null;
  const chip = document.createElement("span");
  chip.className = "date-chip";
  chip.textContent = fmtDate(dates[0]) + (dates.length > 1 ? ` +${dates.length - 1}` : "");
  chip.title = dates.map(fmtDate).join(", ") + " · click to find this day";
  chip.addEventListener("click", (ev) => { ev.stopPropagation(); searchByDate(dates[0]); });
  return chip;
}

// Run search for an exact day. The query is the ISO date itself, which the date
// parser reads as that single day, so this hits notes frozen to it regardless of
// the format they were written in. Searching the soonest date when a note has more.
function searchByDate(iso) {
  openSearch();
  el.searchInput.value = iso;
  runSearch();
}

// ---------------------------------------------------------------- search-term highlight (safe: text nodes only)
function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function highlightTerms(root, query) {
  if (!root || !query) return;
  const terms = query.toLowerCase().split(/\s+/).filter((t) => t.length >= 2).map(escapeRegex);
  if (!terms.length) return;
  const re = new RegExp("(" + terms.join("|") + ")", "gi");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    re.lastIndex = 0;
    if (!re.test(text)) continue;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const mark = document.createElement("mark");
      mark.textContent = m[0];
      frag.appendChild(mark);
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
  }
}

// ---------------------------------------------------------------- elements & state
const el = {
  app: document.getElementById("app"),
  compose: document.getElementById("compose"),
  inputHint: document.getElementById("inputhint"),
  stream: document.getElementById("stream"),
  peek: document.getElementById("peek"),
  searchOverlay: document.getElementById("search-overlay"),
  searchInput: document.getElementById("search-input"),
  searchStatus: document.getElementById("search-status"),
  searchResults: document.getElementById("search-results"),
  cheatsheet: document.getElementById("cheatsheet"),
  settings: document.getElementById("settings"),
  slashmenu: document.getElementById("slashmenu"),
  composeRow: document.getElementById("compose-row"),
  secretForm: document.getElementById("secret-form"),
  welcome: document.getElementById("welcome"),
  splash: document.getElementById("splash"),
  erase: document.getElementById("erase"),
  ollamaGate: document.getElementById("ollama-gate"),
  ollamaBar: document.getElementById("ollama-bar"),
};

const state = {
  offset: 0, limit: 50, end: false, loading: false,
  entries: new Map(),
  cancelEdit: null, undoFn: null,
  // the peek: matches is the ranked active list; focus is -1 (visible, unfocused)
  // or an index into matches (browsing). Editing happens in the stream, not here.
  peek: { matches: [], focus: -1, query: "" },
  search: { items: [], focus: -1, query: "" },
  // slash menu: open when the current line is "/<query>"; items is the filtered list.
  slash: { open: false, items: [], focus: 0, lineStart: 0 },
};

let secretsAvailable = false;   // set from /api/status; gates the lock button and /secret

const DRAFT_KEY = "blurt-draft";
const THEME_KEY = "blurt-theme";
const GHOST_DEBOUNCE = 120;       // UX.md §8: peek ~120ms
const GHOST_MIN_WORDS = 2;        // UX.md §2: 2-word floor; one word is noise
const SEARCH_DEBOUNCE = 100;      // UX.md §8: search ~100ms
const PEEK_SNIPPET = 100;
const SEARCH_MIN_SCORE = 0.55;    // below this, semantic hits are noise → "no matches"
const IS_MAC = /Mac/i.test(navigator.platform || "");
const MOD = IS_MAC ? "⌘" : "ctrl";

// ---------------------------------------------------------------- stream
// Only active notes are ever rendered: retired notes are hidden (restorable with
// ⌘Z right after retiring, or via the durable history), there is no toggle.
function entryNode(e) {
  const node = document.createElement("div");
  node.className = "entry";
  node.dataset.id = e.id;
  const body = document.createElement("div");
  body.className = "entry-body";
  if (e.is_secret) {
    // A secret note, one line: label | masked value | show. Plain-text label (no
    // markdown block, which would force a second line). Not inline-editable.
    body.classList.add("secret-note");
    const lbl = document.createElement("span");
    lbl.className = "secret-label";
    lbl.textContent = e.content;
    const div = document.createElement("span");
    div.className = "sec-div";
    body.append(lbl, div, secretControl(e));
    // Click the label/row to edit in place (the value's copy/show controls stop
    // propagation, so clicking those acts on the value, not the editor).
    body.addEventListener("click", () => openSecretEditor(node, e));
  } else {
    body.innerHTML = md(e.content);
    // Checkbox → tick it. Link → ⌘/Ctrl-click opens it (a bare click edits, so links
    // never hijack editing). Anywhere else → edit the note.
    body.addEventListener("click", (ev) => {
      const cb = ev.target.closest(".checkbox");
      if (cb) { ev.stopPropagation(); toggleCheckbox(node, e, cb); return; }
      const a = ev.target.closest("a");
      if (a) {
        ev.preventDefault();
        if (ev.metaKey || ev.ctrlKey) window.open(a.href, "_blank", "noopener");
        else openEditor(node, e);
        return;
      }
      openEditor(node, e);
    });
  }

  // Footer under the note. No delete button: a note is deleted by emptying it
  // (⌘A, delete) and pressing Enter, like any text pad. The date chip sits on the
  // left (it's about the content); the faint saved-time stays at the far right.
  const foot = document.createElement("div");
  foot.className = "entry-foot";
  const time = document.createElement("div");
  time.className = "entry-time";
  time.textContent = relTime(e.created_at);
  const chip = dateChip(e.dates);
  if (chip) foot.append(chip);
  foot.append(time);

  node.append(body, foot);
  state.entries.set(String(e.id), e);
  return node;
}

// Tick a checkbox in place: optimistic flip, then persist by ordinal. The server
// updates the note's content without superseding it, so the note keeps its id and
// place; we mirror the new content into the cached entry for a later inline edit.
async function toggleCheckbox(node, e, cbEl) {
  const index = Number(cbEl.dataset.cbi);
  const checked = !cbEl.classList.contains("checked");
  cbEl.classList.toggle("checked", checked);
  cbEl.setAttribute("aria-checked", String(checked));
  const res = await api.patch(`/api/entries/${e.id}/checkbox`, { index, checked });
  if (res.ok) {
    e.content = res.data.content;
    state.entries.set(String(e.id), e);
  } else {                                   // revert the optimistic flip on failure
    cbEl.classList.toggle("checked", !checked);
    cbEl.setAttribute("aria-checked", String(!checked));
  }
}

// Newest nearest the input: in a column-reverse stream the first DOM child
// renders at the bottom, so a brand-new note goes in as firstChild.
function prependEntry(e) {
  const hint = document.getElementById("first-hint");
  if (hint) hint.remove();
  el.stream.insertBefore(entryNode(e), el.stream.firstChild);
  state.offset += 1;
}

// ---------------------------------------------------------------- secrets
// Decrypt a secret on demand (server holds the key in the OS keychain). Returns the
// value or null on failure. Fetched only when you reveal/copy, never sent with the list.
async function fetchSecret(id) {
  const res = await api.post(`/api/secrets/${id}/reveal`, {});
  return res.ok && res.data ? res.data.value : null;
}

// The masked control under a secret note. Click the value to COPY it (the common
// action; clipboard auto-clears after 20s, best-effort). The small "show" toggles
// reveal/hide (auto re-masks after 15s). Both stop propagation.
function secretControl(e) {
  const wrap = document.createElement("div");
  wrap.className = "secret";
  const mask = document.createElement("code");
  mask.className = "secret-mask";
  mask.textContent = "••••••••";
  mask.title = "click to copy";
  const toggle = document.createElement("span");
  toggle.className = "secret-toggle";
  toggle.textContent = "show";
  wrap.append(mask, toggle);

  let shown = false, hideTimer = null;
  const hide = () => { if (shown) mask.textContent = "••••••••"; toggle.textContent = "show"; shown = false; clearTimeout(hideTimer); };
  toggle.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (shown) return hide();
    const v = await fetchSecret(e.id);
    if (v == null) { flashHint("couldn't unlock that secret"); return; }
    mask.textContent = v; toggle.textContent = "hide"; shown = true;
    hideTimer = setTimeout(hide, 15000);
  });
  mask.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const v = await fetchSecret(e.id);
    if (v == null) { flashHint("couldn't unlock that secret"); return; }
    try {
      await navigator.clipboard.writeText(v);
      flashHint("copied · clipboard clears in 20s");
      // Best-effort auto-clear: only wipe if the clipboard still holds our value.
      setTimeout(async () => {
        try { if ((await navigator.clipboard.readText()) === v) await navigator.clipboard.writeText(""); } catch { /* not focused / denied */ }
      }, 20000);
    } catch { flashHint("couldn't copy"); }
  });
  return wrap;
}

// The "store a secret" form: a label (visible, searchable) + the value (encrypted).
// Keyboard only: enter saves, esc cancels, enter on the label jumps to the value.
// Build the one-line "key │ secret  show" editing row into `container`, pre-filled
// from opts.key/opts.val. Shared by the bottom create form and the inline editor.
// enter on the key jumps to the value; enter on the value calls onSubmit(key, val);
// esc calls onCancel. Returns the two input elements so the caller can focus one.
//
// Not type=password on purpose: a real password field makes macOS iCloud Passwords
// pop its autofill UI (which Apple won't suppress via attributes). We mask a plain
// text field with CSS (-webkit-text-security) instead; "show" toggles that.
function wireSecretFields(container, { key = "", val = "", onSubmit, onCancel }) {
  // data-*-ignore + autocomplete keep password managers from injecting autofill icons.
  const NOFILL = 'autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" '
    + 'data-1p-ignore="true" data-lpignore="true" data-bwignore="true" data-form-type="other"';
  container.innerHTML = `
    <input class="sec-key" placeholder="key" ${NOFILL} />
    <span class="sec-div"></span>
    <input class="sec-val" placeholder="secret" ${NOFILL} />
    <span class="sec-show secret-toggle" hidden>show</span>`;
  const keyEl = container.querySelector(".sec-key");
  const valEl = container.querySelector(".sec-val");
  const show = container.querySelector(".sec-show");
  keyEl.value = key;
  valEl.value = val;
  show.hidden = !valEl.value;
  valEl.addEventListener("input", () => { show.hidden = !valEl.value; });
  show.addEventListener("click", () => {
    const revealed = valEl.classList.toggle("revealed");
    show.textContent = revealed ? "hide" : "show";
    valEl.focus();
  });
  const onKey = (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); onCancel(); return; }
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    if (ev.target === keyEl && keyEl.value.trim()) { valEl.focus(); return; }  // key -> value
    onSubmit(keyEl.value.trim(), valEl.value);
  };
  keyEl.addEventListener("keydown", onKey);
  valEl.addEventListener("keydown", onKey);
  return { keyEl, valEl };
}

// Create a new secret: the bottom form replaces the compose box while open.
function openSecretForm() {
  if (!el.secretForm.hidden) return closeSecretForm();
  el.secretForm.hidden = false;
  el.composeRow.hidden = true;
  const { keyEl } = wireSecretFields(el.secretForm, {
    onCancel: closeSecretForm,
    onSubmit: async (label, value) => {
      if (!label || !value) { flashHint("need both a key and a secret"); return; }
      const res = await api.post("/api/secrets", { label, value });
      if (res.ok && res.data) { prependEntry(res.data); closeSecretForm(); flashHint("secret saved, encrypted"); }
      else { flashHint(res.status === 503 ? "no keychain available for secrets" : "couldn't save secret"); }
    },
  });
  keyEl.focus();
}

function closeSecretForm() {
  el.secretForm.hidden = true;
  el.secretForm.innerHTML = "";
  el.composeRow.hidden = false;
  focusComposeEnd();
}

// Edit a secret in place: the note row becomes the same key │ value editor, pre-filled
// with the decrypted value. Enter saves (re-encrypts); emptying the key or value and
// pressing Enter deletes the whole secret (recoverable); Esc reverts.
async function openSecretEditor(node, e) {
  const body = node.querySelector(".entry-body");
  if (!body || body.classList.contains("editing-secret")) return;
  const cur = await fetchSecret(e.id);
  if (cur == null) { flashHint("couldn't unlock that secret"); return; }
  if (state.cancelEdit) state.cancelEdit();
  el.stream.classList.add("editing");
  body.className = "entry-body secret-fields editing-secret";
  const stop = () => { state.cancelEdit = null; el.stream.classList.remove("editing"); };
  state.cancelEdit = () => { stop(); node.replaceWith(entryNode(e)); focusComposeEnd(); };
  const { valEl } = wireSecretFields(body, {
    key: e.content,
    val: cur,
    onCancel: () => state.cancelEdit && state.cancelEdit(),
    onSubmit: async (label, value) => {
      if (!label || !value) { stop(); retireEntry(e.id); focusComposeEnd(); return; }  // emptied -> delete
      const res = await api.patch(`/api/secrets/${e.id}`, { label, value });
      if (res.ok && res.data) { stop(); node.replaceWith(entryNode(res.data)); focusComposeEnd(); }
      else { flashHint("couldn't save secret"); }
    },
  });
  valEl.focus();
}

async function loadStream(reset = true) {
  if (state.loading) return;
  state.loading = true;
  if (reset) { state.offset = 0; state.end = false; el.stream.innerHTML = ""; state.entries.clear(); }
  const data = await api.get(`/api/entries?limit=${state.limit}&offset=${state.offset}`);
  const items = data.entries || [];
  if (items.length < state.limit) state.end = true;
  state.offset += items.length;
  // Newest-first from the API; appended in order they stack older-upward in the
  // column-reverse stream (older pages land above what is already shown).
  // Retired notes are never shown.
  for (const e of items) {
    if (e.is_superseded) continue;
    el.stream.appendChild(entryNode(e));
  }
  if (reset && el.stream.childElementCount === 0) {
    const hint = document.createElement("div");
    hint.id = "first-hint";
    hint.textContent = `empty. type something below and press enter.`;
    el.stream.appendChild(hint);
  }
  state.loading = false;
}

// Infinite scroll toward older notes. In a column-reverse container scrollTop is
// 0 at the bottom and grows negative as you scroll up, so the top (oldest) is
// reached as |scrollTop| approaches the max scroll distance.
el.stream.addEventListener("scroll", () => {
  if (state.end || state.loading) return;
  const distFromTop = el.stream.scrollHeight - el.stream.clientHeight - Math.abs(el.stream.scrollTop);
  if (distFromTop < 600) loadStream(false);
});

// ---------------------------------------------------------------- capture
function autoGrow() {
  el.compose.style.height = "auto";
  el.compose.style.height = el.compose.scrollHeight + "px";  // CSS max-height caps + scrolls
}
function focusComposeEnd() {
  el.compose.focus();
  const n = el.compose.value.length;
  el.compose.setSelectionRange(n, n);
}
function isSingleLine() { return !el.compose.value.includes("\n"); }

// Enter inside a list continues it: repeat the marker (`- `, `* `, next number,
// or a fresh `- [ ] `). Enter on an empty item exits the list (drops the marker).
// Uses setRangeText/execCommand so the browser's native undo still works. Returns
// true if it handled the key (caller preventDefaults), false to allow a plain newline.
function maybeContinueList(ta = el.compose) {
  if (ta.selectionStart !== ta.selectionEnd) return false;   // a selection: just break
  const pos = ta.selectionStart;
  const lineStart = ta.value.lastIndexOf("\n", pos - 1) + 1;
  const line = ta.value.slice(lineStart, pos);
  const m = line.match(/^(\s*)([-*]\s+\[[ xX]\]\s|[-*]\s+|\d+\.\s+)(.*)$/);
  if (!m) return false;
  const [, indent, marker, rest] = m;
  if (rest.trim() === "") {                 // empty item -> exit the list, clear the marker
    ta.setRangeText("", lineStart, pos, "end");
  } else {                                  // continue with the next marker
    const ordered = marker.match(/^(\d+)\.\s+$/);
    const checkbox = /^[-*]\s+\[[ xX]\]\s$/.test(marker);
    const next = checkbox ? marker.replace(/\[[ xX]\]/, "[ ]")
      : ordered ? `${parseInt(ordered[1], 10) + 1}. `
      : marker;
    ta.setRangeText("\n" + indent + next, pos, pos, "end");
  }
  ta.dispatchEvent(new Event("input"));     // keep draft/autogrow/ghost in sync
  return true;
}

// ---------------------------------------------------------------- slash menu
// Type "/" at the start of a line to insert a format without remembering the
// markdown (esp. the fiddly `- [ ]`). The markdown still works by hand; this is
// just the easy on-ramp. Each item replaces the typed "/query" with its syntax.
const SLASH_ITEMS = [
  { label: "to-do",      hint: "- [ ]", keys: "todo to-do check task checkbox done", insert: "- [ ] " },
  { label: "bullet",     hint: "-",     keys: "bullet list dash point ul",           insert: "- " },
  { label: "numbered",   hint: "1.",    keys: "numbered number ordered ol",          insert: "1. " },
  { label: "heading",    hint: "#",     keys: "heading head h1 title big",           insert: "# " },
  { label: "subheading", hint: "##",    keys: "subheading subhead h2",               insert: "## " },
  { label: "quote",      hint: ">",     keys: "quote blockquote",                    insert: "> " },
  { label: "code block", hint: "```",   keys: "code codeblock pre block",            insert: "```\n\n```\n", caret: 4 },
  { label: "divider",    hint: "---",   keys: "divider rule line hr separator",      insert: "---\n" },
  { label: "secret",     hint: "encrypted", keys: "secret password credential pwd pin key lock", action: "secret" },
];

function updateSlashMenu() {
  const ta = el.compose;
  if (ta.selectionStart !== ta.selectionEnd) { closeSlash(); return; }
  const pos = ta.selectionStart;
  const lineStart = ta.value.lastIndexOf("\n", pos - 1) + 1;
  // Only when the current line is exactly "/<letters>" with the cursor at its end,
  // so a slash inside text or a URL never triggers it.
  const m = ta.value.slice(lineStart, pos).match(/^\/([\w-]*)$/);
  if (!m) { closeSlash(); return; }
  const q = m[1].toLowerCase();
  let items = q ? SLASH_ITEMS.filter((it) => it.keys.split(" ").some((k) => k.startsWith(q)))
                : SLASH_ITEMS.slice();
  if (!secretsAvailable) items = items.filter((it) => it.action !== "secret");  // no keychain
  if (!items.length) { closeSlash(); return; }
  state.slash = { open: true, items, focus: 0, lineStart };
  renderSlash();
}

function renderSlash() {
  const s = state.slash;
  if (!s.open) { el.slashmenu.hidden = true; el.slashmenu.innerHTML = ""; return; }
  el.slashmenu.innerHTML = "";
  s.items.forEach((it, i) => {
    const row = document.createElement("div");
    row.className = "slash-item" + (i === s.focus ? " focused" : "");
    const label = document.createElement("span");
    label.className = "slash-label";
    label.textContent = it.label;
    const syn = document.createElement("span");
    syn.className = "slash-syntax";
    syn.textContent = it.hint;
    row.append(label, syn);
    // mousedown (not click) + preventDefault so the textarea keeps focus
    row.addEventListener("mousedown", (ev) => { ev.preventDefault(); chooseSlash(i); });
    el.slashmenu.appendChild(row);
  });
  el.slashmenu.hidden = false;
}

function chooseSlash(i) {
  const it = state.slash.items[i];
  if (!it) return;
  const ta = el.compose;
  if (it.action === "secret") {                 // drop the "/secret" and open the form
    ta.setRangeText("", state.slash.lineStart, ta.selectionStart, "end");
    closeSlash();
    autoGrow();
    localStorage.setItem(DRAFT_KEY, ta.value);
    openSecretForm();
    return;
  }
  ta.setRangeText(it.insert, state.slash.lineStart, ta.selectionStart, "end");
  if (it.caret != null) {                       // park the cursor inside (e.g. a code block)
    const c = state.slash.lineStart + it.caret;
    ta.setSelectionRange(c, c);
  }
  closeSlash();
  ta.focus();
  localStorage.setItem(DRAFT_KEY, ta.value);
  autoGrow();
}

function closeSlash() {
  if (!state.slash.open) return;
  state.slash.open = false;
  el.slashmenu.hidden = true;
  el.slashmenu.innerHTML = "";
}

async function saveEntry() {
  const content = el.compose.value;
  if (!content.trim()) return;
  closeSlash();
  dismissWelcome();
  el.compose.value = "";               // instant: the write is sub-ms server-side
  autoGrow();
  localStorage.removeItem(DRAFT_KEY);
  clearPeek();
  el.compose.focus();
  const res = await api.post("/api/entries", { content });
  if (res.ok) { prependEntry(res.data); offerSaveUndo(res.data, content); }
}

// Quick undo of the just-saved note: ⌘/Ctrl+Z (consistent with delete/edit undo) or
// the brief inline "undo" link removes it. If the pad is still empty (nothing typed
// since), the note's text returns to the input so it's exactly as if never saved.
function offerSaveUndo(entry, content) {
  let done = false;
  const undo = async () => {
    if (done) return;
    done = true;
    if (state.undoFn === undo) state.undoFn = null;
    await api.del(`/api/entries/${entry.id}`);
    const node = el.stream.querySelector(`.entry[data-id="${entry.id}"]`);
    if (node) { node.remove(); state.offset = Math.max(0, state.offset - 1); }
    if (!el.compose.value.trim()) {
      el.compose.value = content;
      autoGrow();
      localStorage.setItem(DRAFT_KEY, content);
      focusComposeEnd();
      scheduleGhost();                          // bring the peek back as it was pre-save
    }
    if (el.inputHint.querySelector(".undo-link")) el.inputHint.textContent = "";
  };
  state.undoFn = undo;                         // most-recent action is the keyboard-undo target
  showUndoHint("saved", undo);
}

// A transient "<label> · undo (⌘Z)" line in the input hint. The link fades after a
// few seconds, but ⌘Z keeps working until the next undoable action replaces it.
function showUndoHint(label, undoFn) {
  el.inputHint.innerHTML = `${label} · <span class="undo-link">undo (${MOD}Z)</span>`;
  el.inputHint.querySelector(".undo-link").onclick = undoFn;
  if (hintTimer) clearTimeout(hintTimer);
  hintTimer = setTimeout(() => {
    if (state.peek.matches.length) renderPeek(); else el.inputHint.textContent = "";
  }, 5000);
}

// ---------------------------------------------------------------- inline edit (stream)
// Edit a note in place in the stream. `hooks.onSaved` fires after a successful save.
function openEditor(node, e, hooks = {}) {
  if (node.querySelector(".entry-edit")) return;
  if (state.cancelEdit) state.cancelEdit();
  clearPeek();                          // never leave a peek behind the editor: one Esc exits
  el.stream.classList.add("editing");   // calm the other notes (no hover highlight while editing)
  const body = node.querySelector(".entry-body");
  const ta = document.createElement("textarea");
  ta.className = "entry-edit";
  ta.value = e.content;
  ta.rows = 1;
  // Grow to fit the whole note (no fixed height); CSS caps at ~viewport then scrolls.
  const grow = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
  ta.addEventListener("input", grow);
  body.replaceWith(ta);
  grow();
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);

  const stopEditing = () => { state.cancelEdit = null; el.stream.classList.remove("editing"); };
  state.cancelEdit = () => {
    stopEditing();
    node.replaceWith(entryNode(e));
    focusComposeEnd();
  };

  ta.addEventListener("keydown", async (ev) => {
    // Same model as the compose box: Enter (or ⌘/Ctrl+Enter) saves the edit;
    // Shift+Enter is a new line and continues a list.
    if (ev.key === "Enter" && ev.shiftKey) {
      if (maybeContinueList(ta)) ev.preventDefault();
      return;
    }
    if (ev.key === "Enter") {
      ev.preventDefault();
      // Emptied an existing note, then confirmed: delete it (recoverable via the
      // undo stub / ⌘Z). Matches outliner/notes-app muscle memory. Focus returns to
      // the input box so the keyboard flow never strands you.
      if (!ta.value.trim()) { stopEditing(); retireEntry(e.id); focusComposeEnd(); return; }
      const res = await api.patch(`/api/entries/${e.id}`, { content: ta.value });
      if (res.ok) {
        stopEditing();
        applyEdit(node, e, res.data);
        hooks.onSaved?.();
        focusComposeEnd();
      }
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      if (state.cancelEdit) state.cancelEdit();
    }
  });
}

// An edit updates the note IN PLACE — same id, same spot, no "deleted" stub and no
// jump to the bottom. Undo restores the previous text (another in-place edit).
function applyEdit(node, oldEntry, updatedEntry) {
  const id = updatedEntry.id, prevContent = oldEntry.content;
  node.replaceWith(entryNode(updatedEntry));
  const undo = async () => {
    if (state.undoFn === undo) state.undoFn = null;
    const r = await api.patch(`/api/entries/${id}`, { content: prevContent });
    const n = el.stream.querySelector(`.entry[data-id="${id}"]`);
    if (r.ok && n) n.replaceWith(entryNode(r.data));
    if (el.inputHint.querySelector(".undo-link")) el.inputHint.textContent = "";
  };
  state.undoFn = undo;
  showUndoHint("updated", undo);
  focusComposeEnd();
}

// ---------------------------------------------------------------- the peek
let ghostTimer = null, ghostSeq = 0, ghostAbort = null;

function clearPeek() {
  state.peek = { matches: [], focus: -1, query: "" };
  el.peek.innerHTML = "";
  el.inputHint.textContent = "";
}

function setPeek(matches, query) {
  state.peek.matches = matches;
  state.peek.focus = -1;
  state.peek.query = query;
  if (!matches.length) { el.peek.innerHTML = ""; el.inputHint.textContent = ""; return; }
  renderPeek();
}

function renderPeek() {
  const p = state.peek;
  el.peek.innerHTML = "";
  if (!p.matches.length) { el.inputHint.textContent = ""; return; }

  // DOM order is line_0..line_n then the count label. column-reverse flips it,
  // so the most-relevant line sits nearest the input and the count rides on top.
  p.matches.forEach((m, i) => {
    const line = document.createElement("div");
    line.className = "peek-line" + (i === p.focus ? " focused" : "");
    line.style.animationDelay = (i * 30) + "ms";

    const time = document.createElement("span");
    time.className = "peek-time";
    time.textContent = relTime(m.created_at);
    const txt = document.createElement("span");
    txt.textContent = (i === p.focus)
      ? m.content.replace(/\s+/g, " ")
      : m.content.replace(/\s+/g, " ").slice(0, PEEK_SNIPPET);
    line.append(time, txt);
    highlightTerms(txt, p.query);
    line.addEventListener("click", () => setFocus(i));
    el.peek.appendChild(line);
  });

  if (p.matches.length > 1) {
    const count = document.createElement("div");
    count.id = "peek-count";
    count.innerHTML = `${p.matches.length} matches` + (p.focus < 0 ? ` · <b>↑</b> to browse` : "");
    el.peek.appendChild(count);
  }

  if (p.focus >= 0) {
    const f = el.peek.querySelector(".peek-line.focused");
    if (f) f.scrollIntoView({ block: "nearest" });
    el.inputHint.textContent = `enter edit · ${MOD}+delete delete · ${MOD}+c copy · esc done`;
  } else {
    el.inputHint.textContent = p.matches.length ? `${MOD}+↑ to browse` : "";
  }
}

function setFocus(i) { state.peek.focus = i; renderPeek(); }

function enterPeek() {
  if (!state.peek.matches.length) return;
  state.peek.focus = 0;
  renderPeek();
}
function exitPeekToInput() {           // ↓ past the newest: unfocus, peek stays visible
  state.peek.focus = -1;
  renderPeek();
  focusComposeEnd();
}
function closePeek() {                 // esc: dismiss the peek entirely
  clearPeek();
  focusComposeEnd();
}

// Enter on a peek match edits that note IN PLACE in the stream (no second editor,
// no duplication). The peek is cleared so there's nothing to dismiss afterward —
// one Esc backs out cleanly, and ↑ re-summons the peek for the current draft.
async function editPeekFocused() {
  const m = state.peek.matches[state.peek.focus];
  if (!m) return;
  // The match is an active note, so it lives somewhere in the stream. Page older
  // notes in until its node is on screen (bounded by the end of the stream).
  let node = el.stream.querySelector(`.entry[data-id="${m.id}"]`);
  while (!node && !state.end) {
    await loadStream(false);
    node = el.stream.querySelector(`.entry[data-id="${m.id}"]`);
  }
  if (!node) return;                          // gone (raced with a delete) — bail quietly
  const e = state.entries.get(String(m.id)) || m;
  node.scrollIntoView({ block: "center" });
  if (e.is_secret) { clearPeek(); flash(node); return; }  // secrets aren't inline-editable; just locate
  // Stepping into an existing note from the peek: the draft was only the search
  // trigger, so clear it now (no leftover duplicate in the input box) and return
  // focus there afterward via the editor's own save/cancel/delete paths.
  el.compose.value = "";
  localStorage.removeItem(DRAFT_KEY);
  autoGrow();
  openEditor(node, e);
}

async function supersedePeekFocused() {
  const p = state.peek;
  const m = p.matches[p.focus];
  if (!m) return;
  await api.del(`/api/entries/${m.id}`);
  p.matches.splice(p.focus, 1);
  const node = el.stream.querySelector(`.entry[data-id="${m.id}"]`);
  if (node) node.replaceWith(undoStub(m, async () => {
    const r = await api.patch(`/api/entries/${m.id}/restore`);
    return r.ok ? r.data : null;
  }));
  if (!p.matches.length) { closePeek(); return; }
  if (p.focus >= p.matches.length) p.focus = p.matches.length - 1;
  renderPeek();
}

function copyPeekFocused() {
  const m = state.peek.matches[state.peek.focus];
  if (m && navigator.clipboard) navigator.clipboard.writeText(m.content);
}

// enterAfter: when ↑ summons the peek for the current draft, step into it on arrival.
async function fireGhost(enterAfter = false) {
  const text = el.compose.value;
  if (text.trim().split(/\s+/).filter(Boolean).length < GHOST_MIN_WORDS) { clearPeek(); return; }
  const seq = ++ghostSeq;
  if (ghostAbort) ghostAbort.abort();        // abort-stale: never land out of order
  ghostAbort = new AbortController();
  let res;
  try { res = await api.post("/api/suggest", { text }, ghostAbort.signal); }
  catch { return; }                          // aborted by a newer keystroke
  if (seq !== ghostSeq) return;
  if (!res.ok || !res.data) { clearPeek(); return; }
  setPeek(res.data.matches || [], text.trim());
  if (enterAfter) enterPeek();
}

function hasGhostableText() {
  return el.compose.value.trim().split(/\s+/).filter(Boolean).length >= GHOST_MIN_WORDS;
}

function scheduleGhost() {
  if (ghostTimer) clearTimeout(ghostTimer);
  ghostTimer = setTimeout(fireGhost, GHOST_DEBOUNCE);
}

// ---------------------------------------------------------------- retire / restore / undo
function snippet(e, n = 42) { return e ? e.content.replace(/\s+/g, " ").slice(0, n) : "note"; }

function flash(node) {
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("focus-flash");
  setTimeout(() => node.classList.remove("focus-flash"), 1200);
}

// A retired note collapses into a subtle inline "retired ... · undo" line in
// place. No popup. It fades out after a while; undo (⌘Z or click) restores it.
function undoStub(entryForLabel, restoreFn) {
  const stub = document.createElement("div");
  stub.className = "retired-stub";
  const text = document.createElement("span");
  text.textContent = `deleted "${snippet(entryForLabel)}"`;
  const link = document.createElement("span");
  link.className = "undo-link";
  link.textContent = `undo (${MOD}Z)`;
  stub.append(text, link);
  let done = false;
  const undo = async () => {
    if (done) return;
    done = true;
    if (state.undoFn === undo) state.undoFn = null;
    const restored = await restoreFn();
    if (restored) stub.replaceWith(entryNode(restored));
    else stub.remove();
  };
  link.onclick = undo;
  state.undoFn = undo;   // most-recent action is the keyboard-undo target
  setTimeout(() => {
    if (done) return;
    if (state.undoFn === undo) state.undoFn = null;
    stub.remove();
  }, 8000);
  return stub;
}

async function retireEntry(id) {
  const entry = state.entries.get(String(id));
  await api.del(`/api/entries/${id}`);
  const node = el.stream.querySelector(`.entry[data-id="${id}"]`);
  const restoreFn = async () => {
    const r = await api.patch(`/api/entries/${id}/restore`);
    return r.ok ? r.data : null;
  };
  if (node) node.replaceWith(undoStub(entry, restoreFn));
}

// ---------------------------------------------------------------- search overlay (arrow-navigable)
let searchTimer = null, searchSeq = 0, searchAbort = null;

function openSearch() {
  el.searchOverlay.hidden = false;
  el.searchInput.focus();
  el.searchInput.select();
}
function closeSearch() {
  el.searchOverlay.hidden = true;
  el.searchInput.value = "";
  el.searchStatus.textContent = "";
  el.searchResults.innerHTML = "";
  state.search = { items: [], focus: -1, query: "" };
  el.compose.focus();
}

function resultNode(e, query, i) {
  const node = document.createElement("div");
  node.className = "result";
  node.dataset.idx = i;
  const time = document.createElement("div");
  time.className = "result-time";
  time.textContent = relTime(e.created_at);
  const chip = dateChip(e.dates);
  if (chip) { chip.classList.add("result-date"); time.appendChild(chip); }
  const body = document.createElement("div");
  body.className = "result-body";
  body.innerHTML = md(e.content);
  highlightTerms(body, query);
  node.append(time, body);
  node.addEventListener("click", () => locateEntry(e.id));
  return node;
}

function renderSearchFocus() {
  const nodes = el.searchResults.children;
  for (let i = 0; i < nodes.length; i++) nodes[i].classList.toggle("focused", i === state.search.focus);
  const f = nodes[state.search.focus];
  if (f) f.scrollIntoView({ block: "nearest" });
}

function locateEntry(id) {
  closeSearch();
  const node = el.stream.querySelector(`.entry[data-id="${id}"]`);
  if (node) flash(node);
  else loadStream(true).then(() => {
    const n = el.stream.querySelector(`.entry[data-id="${id}"]`);
    if (n) flash(n);
  });
}

async function runSearch() {
  const q = el.searchInput.value.trim();
  if (!q) { el.searchStatus.textContent = ""; el.searchResults.innerHTML = ""; state.search = { items: [], focus: -1, query: "" }; return; }
  const seq = ++searchSeq;
  if (searchAbort) searchAbort.abort();
  searchAbort = new AbortController();
  let res;
  try { res = await api.post("/api/query", { query: q }, searchAbort.signal); }
  catch { return; }
  if (seq !== searchSeq) return;
  // Relevance floor: below it, semantic hits are nearest-noise. Exact (lexical)
  // matches score 1.0 and always pass.
  const items = ((res.data && res.data.entries) || []).filter((e) => e.score >= SEARCH_MIN_SCORE);
  state.search = { items, focus: -1, query: q };
  el.searchStatus.innerHTML = (items.length
    ? `${items.length} result${items.length > 1 ? "s" : ""} · ↑↓ to move, enter to jump`
    : "no matches") + `  ·  <span id="search-clear">clear</span>`;
  const clearBtn = document.getElementById("search-clear");
  if (clearBtn) clearBtn.onclick = () => { el.searchInput.value = ""; el.searchInput.focus(); runSearch(); };
  el.searchResults.innerHTML = "";
  items.forEach((e, i) => el.searchResults.appendChild(resultNode(e, q, i)));
}

// ---------------------------------------------------------------- export
// Flash a transient line in the input hint, then restore whatever the peek wants.
let hintTimer = null;
function flashHint(msg) {
  el.inputHint.textContent = msg;
  if (hintTimer) clearTimeout(hintTimer);
  hintTimer = setTimeout(() => {
    if (state.peek.matches.length) renderPeek(); else el.inputHint.textContent = "";
  }, 1600);
}

// Note: there is intentionally no "export". Your notes are always mirrored to a plain
// scratchpad.md on disk (see the File menu in the desktop app); that file IS the copy.

// ---------------------------------------------------------------- cheatsheet
// One key list, two presentations: written inline into the pad on first load
// (#welcome), and a summoned floating panel afterward (#cheatsheet via `?`).
function keyListHtml() {
  const rows = [
    [`enter`, "save the note"],
    [`shift+enter`, "new line"],
    [`/`, "formatting menu (at line start)"],
    [`${MOD}+k`, "store a secret (encrypted)"],
    [`${MOD}+↑`, "browse matches in the peek"],
    [`↑ / ↓`, "move through the peek"],
    [`enter`, "edit the focused match"],
    [`${MOD}+delete`, "delete the focused match"],
    [`${MOD}+c`, "copy the focused match"],
    [`esc`, "close the peek"],
    [`${MOD}+f`, "search"],
    [`ctrl+shift+d`, "dark / light"],
    [`?`, "this cheatsheet"],
  ];
  return rowsToDl(rows);
}

// "Other types of input" are markdown you TYPE at the start of a line (no toolbar,
// no shortcut) — this makes them discoverable. Lists auto-continue on Shift+Enter.
function formatListHtml() {
  const rows = [
    [`- text`, "bullet list"],
    [`1. text`, "numbered list"],
    [`- [ ] text`, "checklist — click the box to tick"],
    [`# text`, "heading"],
    [`**text**`, "bold"],
    [`*text*`, "italic"],
    ["`text`", "code"],
    [`> text`, "quote"],
    [`[text](url)`, "link"],
  ];
  return rowsToDl(rows);
}
function rowsToDl(rows) {
  return `<dl>` +
    rows.map(([k, v]) => `<dt><kbd>${escapeHtml(k)}</kbd></dt><dd>${escapeHtml(v)}</dd>`).join("") +
    `</dl>`;
}
function onCheatsheetOutside(ev) {
  if (!el.cheatsheet.contains(ev.target)) hideCheatsheet();
}
function showCheatsheet() {
  el.cheatsheet.innerHTML =
    `<h4>keys</h4>` + keyListHtml() + `<h4>formatting · just type it</h4>` + formatListHtml();
  el.cheatsheet.hidden = false;
  // click anywhere outside the panel to dismiss (defer so the opening keypress/click clears)
  setTimeout(() => document.addEventListener("mousedown", onCheatsheetOutside), 0);
}
function hideCheatsheet() {
  el.cheatsheet.hidden = true;
  document.removeEventListener("mousedown", onCheatsheetOutside);
}
function toggleCheatsheet() { el.cheatsheet.hidden ? showCheatsheet() : hideCheatsheet(); }

// First-load welcome: the keys, jotted into the empty pad just above the input.
function showWelcome() {
  const hint = document.getElementById("first-hint");
  if (hint) hint.remove();
  el.welcome.innerHTML = `<h4>keys</h4>` + keyListHtml();
  el.welcome.hidden = false;
}
function dismissWelcome() {
  if (el.welcome.hidden) return false;
  el.welcome.hidden = true;
  el.welcome.innerHTML = "";
  return true;
}

// ---------------------------------------------------------------- settings
// A small panel: where the scratchpad.md notes file lives (changeable), an update
// check, and about. Folder picking needs the native dialog, so it goes through the
// desktop app's pywebview bridge; in a plain browser that control is disabled.
function settingsHtml(d) {
  const hasPicker = !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);
  const changeBtn = hasPicker
    ? `<button id="set-change">Change…</button>`
    : `<button id="set-change" disabled title="available in the desktop app">Change…</button>`;
  return `
    <h3>settings</h3>
    <div class="row">
      <div class="label">notes folder</div>
      <div class="value">
        <span class="path" id="set-path">${escapeHtml(d.scratchpad_path || "")}</span>
        ${changeBtn}
      </div>
      <div class="note">Your notes are mirrored here as plain Markdown, always up to date.
        Point it at any folder (e.g. inside an Obsidian or Dropbox folder) to keep them there.</div>
    </div>
    <div class="row">
      <div class="label">date format</div>
      <div class="value">
        <div class="seg" id="set-dateorder">
          <button data-order="DMY" class="${d.date_order === "MDY" ? "" : "on"}">day / month / year</button>
          <button data-order="MDY" class="${d.date_order === "MDY" ? "on" : ""}">month / day / year</button>
        </div>
      </div>
      <div class="note">How Blurt reads ambiguous numeric dates like 6/4. Spelled-out
        months (6 Jun) are never ambiguous and always work.</div>
    </div>
    <div class="row">
      <div class="label">smart search engine</div>
      <div class="note" id="set-engine">${engineStatusHtml(lastStatus)}</div>
    </div>
    <div class="row">
      <div class="label">updates</div>
      <div class="value">
        <span class="path">blurt ${escapeHtml(d.version || "")}</span>
        <button id="set-update">Check for updates</button>
      </div>
      <div class="note" id="set-update-result"></div>
    </div>
    <div class="row">
      <div class="label">about</div>
      <div class="note">A local-first scratchpad. MIT licensed.
        <a href="https://github.com/rbsriram/blurt" target="_blank" rel="noopener noreferrer">github.com/rbsriram/blurt</a></div>
    </div>`;
}

async function openSettings() {
  let d = { scratchpad_path: "", version: "", date_order: "DMY" };
  try { d = await api.get("/api/settings"); } catch { /* show blanks */ }
  el.settings.innerHTML = settingsHtml(d);
  el.settings.hidden = false;
  document.getElementById("set-change").onclick = changeNotesFolder;
  document.getElementById("set-update").onclick = checkUpdates;
  el.settings.querySelectorAll("#set-dateorder button").forEach((b) => {
    b.onclick = () => setDateOrder(b.dataset.order);
  });
  setTimeout(() => document.addEventListener("mousedown", onSettingsOutside), 0);
}

// Flip how ambiguous numeric dates read. The server re-freezes existing notes, so
// the change shows everywhere at once; reload the stream to reflect new chips.
async function setDateOrder(order) {
  const group = document.getElementById("set-dateorder");
  const current = group.querySelector("button.on");
  if (current && current.dataset.order === order) return;   // no-op if already active
  const res = await api.post("/api/date-format", { order });
  if (res.ok) {
    group.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.order === order));
    flashHint("date format updated");
    loadStream(true);
  } else {
    flashHint("couldn't change the date format");
  }
}
function onSettingsOutside(ev) { if (!el.settings.contains(ev.target)) closeSettings(); }
function closeSettings() {
  el.settings.hidden = true;
  el.settings.innerHTML = "";
  document.removeEventListener("mousedown", onSettingsOutside);
}
function toggleSettings() { el.settings.hidden ? openSettings() : closeSettings(); }

async function changeNotesFolder() {
  const folder = await window.pywebview.api.pick_folder();   // native dialog; null if cancelled
  if (!folder) return;
  const res = await api.post("/api/notes-dir", { path: folder });
  if (res.ok && res.data) {
    document.getElementById("set-path").textContent = res.data.scratchpad_path;
    flashHint("notes folder updated");
  } else {
    flashHint("couldn't change that folder");
  }
}

async function checkUpdates() {
  const out = document.getElementById("set-update-result");
  out.textContent = "checking…";
  let d;
  try { d = await api.get("/api/update-check"); } catch { out.textContent = "Couldn't reach GitHub."; return; }
  if (d.error) { out.textContent = d.error; return; }
  if (d.update_available) {
    out.innerHTML = `Update available: <strong>${escapeHtml(d.latest)}</strong>` +
      `<div class="cmd"><code>${escapeHtml(d.command)}</code><button id="set-copy">Copy</button></div>`;
    document.getElementById("set-copy").onclick = () => {
      navigator.clipboard && navigator.clipboard.writeText(d.command);
      flashHint("command copied");
    };
  } else {
    out.textContent = `You're on the latest version (${escapeHtml(d.current)}).`;
  }
}

// ---------------------------------------------------------------- erase (test-only)
// Two clicks: the first arms ("erase — sure?"), the second wipes. Avoids nuking
// data on a stray click. Only wired up when the server reports test mode.
let eraseArmed = false, eraseTimer = null;
function disarmErase() {
  eraseArmed = false;
  if (eraseTimer) { clearTimeout(eraseTimer); eraseTimer = null; }
  el.erase.classList.remove("armed");
  el.erase.textContent = "erase";
}
async function onErase() {
  if (!eraseArmed) {
    eraseArmed = true;
    el.erase.classList.add("armed");
    el.erase.textContent = "erase — sure?";
    eraseTimer = setTimeout(disarmErase, 3000);
    return;
  }
  disarmErase();
  const res = await api.del("/api/test/reset");
  if (!res.ok) return;
  // Back to a blank pad: clear the draft, the peek, and the stream.
  localStorage.removeItem(DRAFT_KEY);
  el.compose.value = "";
  autoGrow();
  clearPeek();
  state.offset = 0; state.end = false;
  await loadStream(true);
  focusComposeEnd();
  newPadIntro();        // wiped clean → replay the new-notepad intro
}

// --------------------------------------------------- smart-search (Ollama) health
// The peek runs on a local model (Ollama + nomic-embed-text), like a voice app leaning on
// a local STT model. So the pad is GATED on first launch until that engine is ready, which
// avoids saving notes that can't be indexed. Once healthy, an Ollama drop is non-blocking:
// capture and exact search carry on, the peek resumes (and the backlog re-indexes) when it
// returns. /api/status is the single source of truth; the indexer self-heals the backend.
let everHealthy = false;
let lastHealthHtml = "";
let healthTimer = null;
let lastStatus = null;     // most recent /api/status, so Settings can show the engine state

const OLLAMA_LINK =
  '<a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">Ollama</a>';

function healthMessage(status) {
  // Reads right whether Ollama is missing, stopped, or just starting; the link goes to the
  // install either way (we can't tell "not installed" from "not running" — both unreachable).
  if (!status || !status.ollama_connected) return `blurt needs ${OLLAMA_LINK} to think.`;
  return "setting up peek (downloading the model, one time)…";  // Ollama up, model still landing
}

function applyHealthUI(status) {
  const healthy = !!(status && status.ollama_connected && status.embed_model_available);
  if (healthy) {
    everHealthy = true;
    el.ollamaGate.hidden = true;
    el.ollamaBar.hidden = true;
    el.ollamaBar.classList.remove("gated");
    if (el.compose.disabled) { el.compose.disabled = false; focusComposeEnd(); }
    renderEngineStatus(status);
    return;
  }
  const html = healthMessage(status);
  if (html !== lastHealthHtml) { el.ollamaBar.innerHTML = html; lastHealthHtml = html; }
  const blocking = !everHealthy;            // hard gate only before the engine is first ready
  el.ollamaGate.hidden = !blocking;
  el.ollamaBar.classList.toggle("gated", blocking);
  el.ollamaBar.hidden = false;
  el.compose.disabled = blocking;           // can't type into a pad whose notes couldn't index
  renderEngineStatus(status);
}

async function refreshSemanticStatus() {
  let status = null;
  try { status = await api.get("/api/status"); } catch { /* server momentarily unreachable */ }
  if (status) applyHealthUI(status);
  // Poll briskly while degraded so the gate clears fast once Ollama is up; relax when healthy.
  clearTimeout(healthTimer);
  const healthy = status && status.ollama_connected && status.embed_model_available;
  healthTimer = setTimeout(refreshSemanticStatus, healthy ? 15000 : 3000);
}

// The engine readout in Settings: Ollama reachability, the embedding model, and any
// catch-up indexing. Updated live on every poll if the Settings panel is open.
function engineStatusHtml(status) {
  const ollama = status && status.ollama_connected
    ? "<b>Ollama</b> running"
    : `<b>Ollama</b> not running &middot; ${OLLAMA_LINK}`;
  let model;
  if (!status || !status.ollama_connected) model = "model <b>nomic-embed-text</b> —";
  else if (status.embed_model_available) model = "model <b>nomic-embed-text</b> ready";
  else model = "model <b>nomic-embed-text</b> downloading…";
  const pending = status && status.indexing_pending
    ? ` &middot; ${status.indexing_pending} note(s) catching up` : "";
  return `${ollama}<br>${model}${pending}`;
}

function renderEngineStatus(status) {
  lastStatus = status;
  const node = document.getElementById("set-engine");
  if (node) node.innerHTML = engineStatusHtml(status);
  // Enables ⌘K / the /secret command only when there's a keychain to hold the key.
  secretsAvailable = !!status.secrets_available;
}

async function initErase() {
  try {
    const status = await api.get("/api/status");
    if (status && status.testing) {
      el.erase.hidden = false;
      el.erase.addEventListener("click", onErase);
    }
  } catch { /* status unreachable: leave the control hidden */ }
}

// ---------------------------------------------------------------- theme
function applyTheme(t) { document.body.classList.toggle("dark", t === "dark"); }
applyTheme(localStorage.getItem(THEME_KEY) || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
function toggleTheme() {
  const next = document.body.classList.contains("dark") ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

// ---------------------------------------------------------------- wiring: compose
el.compose.addEventListener("input", () => {
  dismissWelcome();                 // first keystroke clears the inline welcome
  localStorage.setItem(DRAFT_KEY, el.compose.value);
  autoGrow();
  updateSlashMenu();                // "/" at line start opens the formatting menu
  if (state.peek.focus >= 0) { state.peek.focus = -1; renderPeek(); }  // typing resets peek focus
  scheduleGhost();
});
// Clicking away closes the slash menu (its items use mousedown+preventDefault, so
// picking one doesn't blur and won't be lost).
el.compose.addEventListener("blur", closeSlash);

el.compose.addEventListener("keydown", (ev) => {
  // Slash menu owns the arrows/Enter/Esc while it's open.
  if (state.slash.open) {
    const s = state.slash;
    if (ev.key === "ArrowDown") { ev.preventDefault(); s.focus = (s.focus + 1) % s.items.length; renderSlash(); return; }
    if (ev.key === "ArrowUp") { ev.preventDefault(); s.focus = (s.focus - 1 + s.items.length) % s.items.length; renderSlash(); return; }
    if (ev.key === "Enter" || ev.key === "Tab") { ev.preventDefault(); chooseSlash(s.focus); return; }
    if (ev.key === "Escape") { ev.preventDefault(); closeSlash(); return; }
  }

  const p = state.peek;
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") { ev.preventDefault(); saveEntry(); return; }

  const inPeek = p.focus >= 0;

  if (inPeek && (ev.metaKey || ev.ctrlKey) && (ev.key === "c" || ev.key === "C")) {
    ev.preventDefault(); copyPeekFocused(); return;
  }
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "ArrowUp") {
    if (p.matches.length) { ev.preventDefault(); enterPeek(); }
    return;
  }

  if (inPeek) {
    if (ev.key === "ArrowUp") { ev.preventDefault(); setFocus(Math.min(p.focus + 1, p.matches.length - 1)); return; }
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      if (p.focus === 0) exitPeekToInput();          // ↓ past the newest → back to input
      else setFocus(p.focus - 1);
      return;
    }
    if (ev.key === "Enter") { ev.preventDefault(); editPeekFocused(); return; }
    // Deleting a whole note needs one deliberate ⌘/Ctrl+Delete; bare Backspace falls
    // through to the textarea to edit the draft letter-by-letter (its `input` handler
    // then exits the peek).
    if ((ev.metaKey || ev.ctrlKey) && (ev.key === "Backspace" || ev.key === "Delete")) {
      ev.preventDefault(); supersedePeekFocused(); return;
    }
    if (ev.key === "Escape") { ev.preventDefault(); closePeek(); return; }
    return;   // any other key (incl. bare Backspace) falls through to edit the draft
  }

  // not browsing the peek
  if (ev.key === "ArrowUp" && isSingleLine()) {
    if (p.matches.length) { ev.preventDefault(); enterPeek(); return; }
    // peek not currently up (e.g. just backed out of an edit): re-summon it for the draft
    if (hasGhostableText()) { ev.preventDefault(); fireGhost(true); return; }
  }
  if (ev.key === "Escape" && p.matches.length) { ev.preventDefault(); closePeek(); return; }
  if (ev.key === "Enter") {
    // Chat-app model: Enter saves; Shift+Enter is a new line (and auto-continues a
    // bullet/checkbox if you're in one). (⌘/Ctrl+Enter above also saves.)
    if (ev.shiftKey) { if (maybeContinueList()) ev.preventDefault(); return; }
    ev.preventDefault(); saveEntry();
  }
});

// ---------------------------------------------------------------- wiring: search (keyboard-only, ⌘/Ctrl+F)
el.searchInput.addEventListener("input", () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, SEARCH_DEBOUNCE);
});
el.searchInput.addEventListener("keydown", (ev) => {
  const s = state.search;
  if (ev.key === "ArrowDown") { ev.preventDefault(); s.focus = Math.min(s.focus + 1, s.items.length - 1); renderSearchFocus(); }
  else if (ev.key === "ArrowUp") { ev.preventDefault(); s.focus = Math.max(s.focus - 1, 0); renderSearchFocus(); }
  else if (ev.key === "Enter") {
    ev.preventDefault();
    if (searchTimer) clearTimeout(searchTimer);
    const target = s.items[s.focus >= 0 ? s.focus : 0];
    if (target) locateEntry(target.id);
    else runSearch();
  } else if (ev.key === "Escape") { ev.preventDefault(); closeSearch(); }
});
el.searchOverlay.addEventListener("click", (ev) => { if (ev.target === el.searchOverlay) closeSearch(); });

// ---------------------------------------------------------------- wiring: global
window.addEventListener("keydown", (ev) => {
  if ((ev.metaKey || ev.ctrlKey) && (ev.key === "z" || ev.key === "Z") && state.undoFn) {
    ev.preventDefault(); state.undoFn(); return;
  }
  if ((ev.metaKey || ev.ctrlKey) && (ev.key === "f" || ev.key === "F")) {
    ev.preventDefault(); el.searchOverlay.hidden ? openSearch() : closeSearch(); return;
  }
  // Cmd/Ctrl+K: store a secret. Keyboard-first; the lock button is the click path.
  // Only when secrets are available (the button is shown) and nothing modal is open.
  if ((ev.metaKey || ev.ctrlKey) && (ev.key === "k" || ev.key === "K")) {
    if (secretsAvailable && el.searchOverlay.hidden && el.settings.hidden) {
      ev.preventDefault(); openSecretForm(); return;
    }
  }
  // Swallow Cmd/Ctrl+S so the browser's "save page" dialog never appears. There is no
  // export to trigger: notes are continuously mirrored to scratchpad.md already.
  if ((ev.metaKey || ev.ctrlKey) && (ev.key === "s" || ev.key === "S")) { ev.preventDefault(); return; }
  if (ev.ctrlKey && ev.shiftKey && (ev.key === "d" || ev.key === "D")) { ev.preventDefault(); toggleTheme(); return; }
  // `?` summons the cheatsheet — but only when not mid-note (so you can still
  // type a literal "?") and not while searching. If the inline welcome is up, `?`
  // just clears it.
  if (ev.key === "?" && el.searchOverlay.hidden && !el.compose.value) {
    ev.preventDefault();
    if (!dismissWelcome()) toggleCheatsheet();
    return;
  }
  if (ev.key === "Escape") {
    // Esc closes search no matter where focus is (not just when the search input
    // holds it) — every action is a keystroke away.
    if (!el.searchOverlay.hidden) { ev.preventDefault(); closeSearch(); return; }
    if (!el.secretForm.hidden) { ev.preventDefault(); closeSecretForm(); return; }
    if (!el.settings.hidden) { ev.preventDefault(); closeSettings(); return; }
    if (dismissWelcome()) { ev.preventDefault(); return; }
    if (!el.cheatsheet.hidden) { ev.preventDefault(); hideCheatsheet(); return; }
    if (state.cancelEdit) { ev.preventDefault(); state.cancelEdit(); return; }
  }
  // Cmd/Ctrl+, opens Settings — the standard shortcut, and the path for a plain
  // browser where there is no native menu to trigger it.
  if ((ev.metaKey || ev.ctrlKey) && ev.key === ",") { ev.preventDefault(); toggleSettings(); return; }
});

// ---------------------------------------------------------------- boot
// The brand moment: "blurt" swells in the center, then fades to the UI. It plays on
// EVERY load so each launch feels like opening the app. Two flavors:
//   - blank pad (first run, or after erase): the full swell, then the keys are
//     written into the pad as a one-time tutorial.
//   - any notes present: a quick flash only, no tutorial, so a returning user is
//     not made to wait or re-read the keys.
function runSplash(then, { quick = false } = {}) {
  el.splash.hidden = false;
  el.splash.classList.toggle("quick", quick);
  requestAnimationFrame(() => el.splash.classList.add("run"));
  setTimeout(() => {
    el.splash.hidden = true;
    el.splash.classList.remove("run", "quick");   // reset so it can replay (e.g. after erase)
    if (then) then();
  }, quick ? 950 : 1850);
}
function newPadIntro() { runSplash(() => showWelcome()); }   // splash + keys, blank pad
function brandFlash() { runSplash(null, { quick: true }); }  // splash only, returning load

// Hooks for the native macOS menu (Help, View) so it drives the app's own features
// rather than duplicating them.
window.__blurtHelp = () => showCheatsheet();
window.__blurtTheme = () => toggleTheme();
window.__blurtSettings = () => toggleSettings();

el.compose.value = localStorage.getItem(DRAFT_KEY) || "";
autoGrow();
focusComposeEnd();
initErase();
refreshSemanticStatus();   // self-schedules its next poll (brisk while degraded, relaxed when healthy)
loadStream(true).then(() => {
  const blank = !el.stream.querySelector(".entry") && !el.compose.value.trim();
  blank ? newPadIntro() : brandFlash();
});
