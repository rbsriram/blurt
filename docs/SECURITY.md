# SECURITY

Blurt is a single-user, localhost-only personal tool. The threat model is
bounded but real.

## Threats considered (v1)

- Someone on your local network reaching the app.
- Malicious content pasted into a note (stored XSS when rendered).
- Your notes being readable if your machine/account is compromised.
- Injection via the API (SQL injection, oversized payloads, malformed input).

## What v1 implements

**Localhost binding.** The server binds `127.0.0.1` only (`settings.host`).
Exposing it (`BLURT_HOST=0.0.0.0`) is an explicit, documented, discouraged
opt-in. There is no auth yet, so do not do this on an untrusted network.

**Host-header validation (anti-DNS-rebinding).** Even bound to localhost, a
browser-based attack could try to reach the local API: a malicious page resolves
its own domain to `127.0.0.1` and makes background requests. A middleware rejects
(`403`) any request whose `Host` header is not a known localhost name (or the host
the server was deliberately told to bind). The attacker's page sends its own
domain as `Host`, so it is refused and cannot touch your notes. This is the main
defense for a no-auth local web app.

**XSS-safe rendering.** Note content is stored raw (verbatim) but rendered
escape-first: the UI HTML-escapes the entire string before applying Markdown
formatting, and only emits tags it constructs itself. Pasted `<script>`,
`<img onerror=…>`, etc. become inert text. No `innerHTML` of raw user content,
no sanitizer dependency needed. Links are restricted to `http(s):`, `mailto:`,
and root-relative URLs.

**SQL injection.** All queries are parameterized. `LIKE` patterns escape `%`,
`_`, and `\`. Verified by the integration suite (`'; DROP TABLE entries; --`
stored and DB intact).

**Input limits.** Empty/whitespace-only/null-byte content is rejected (422).
Oversize content is rejected (413, `max_content_chars`, default 1 MB). Malformed
JSON and missing fields are rejected (422).

**Database file permissions.** `Database` sets the SQLite file to `chmod 600`
(owner read/write only) on open.

**No telemetry, no analytics, no outbound calls** except to the local Ollama
endpoint.

**Destructive test mode is gated and off by default.** There is one destructive
endpoint, `DELETE /api/test/reset`, which wipes ALL notes, and a matching in-UI
"erase" control. Both exist only for the test suite and local development. They
are controlled by a single flag, `BLURT_TESTING` (default off):

- With the flag off (the default, a normal install): `/api/test/reset` returns
  `404`, the `erase` button stays hidden and is never wired up. The feature is
  entirely inert and unreachable.
- With `BLURT_TESTING=1`: the endpoint works and the button appears.

Never set `BLURT_TESTING=1` on a server reachable by anyone but you. Because v1
has no auth, combining test mode with a non-localhost bind would let anyone on
that network erase your notes. Use it only for `pytest` and local work.

## What v1 explicitly does NOT do

- No user accounts / passwords (single user, localhost).
- No CSRF protection (no cookies, no sessions).
- No encryption at rest (rely on your disk encryption).

## Before exposing beyond localhost (v2, mandatory)

- Token-based auth on every endpoint.
- HTTPS (self-signed or Tailscale TLS).
- Rate limiting.

Do not skip these. The current build is safe only as a localhost tool.
