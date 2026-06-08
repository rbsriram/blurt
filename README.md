# Blurt

A notepad that remembers so you don't have to.

You just type. One endless stream, newest at the bottom, no folders, no tags, no
"where did I put that." The twist: as you write, Blurt is quietly reading your
own past notes back to itself, and the moment you start writing something you
already wrote once, it surfaces the old one right above your cursor. Update it,
or ignore it. Your call. It all runs on your machine. Nothing leaves.

```
   ┌─ the peek: notes that match what you're typing ───────────────┐
   │  raj's number is 050-2222                                     │
   └───────────────────────────────────────────────────────────────┘
   gate code for raj's building|                       <- you, typing
```

It is semantic, not search-by-keyword. Type "the passcode for the building
entrance" and it pulls up the note that says "gate code is 44321." You never
typed the same words. It just gets it.

## Why I built this

I have always been a scratchpad person. One note, one stream, everything dumped
in: meeting notes, passwords, phone numbers, random thoughts, account numbers,
to-dos, all of it. No folders, no tags, no organization. Just a running log of my
brain. The problem was never the input. The problem was always getting things back
out. Searching meant scrolling, guessing keywords, connecting dots by hand, and
half the time the thing I needed had been quietly overwritten three entries ago
with no way to know which version was current.

I built Blurt for myself. I wanted something that looked like the dumbest notepad
you have ever seen but was smart enough to notice when I was writing something that
already existed, surface it quietly without interrupting me, and give me exactly
what I asked for when I searched. No cloud, no subscription, no AI API I pay for
every month. Just a local tool that stays out of my way and does its job.

If this is how your brain works too, give it a try. And if you build something cool
on top of it, I would love to hear about it.

## What makes it not annoying

- **The peek.** It surfaces what you already wrote, as you write. Quiet until
  useful, then exactly useful.
- **Capture is dumb fast.** Type, hit Enter, saved. Saving never waits on
  anything. No spinners, no "syncing."
- **It is yours, completely.** Local SQLite plus local embeddings (Ollama). No
  cloud, no account, no API key, no telemetry. The internet is not invited.
- **It is just text.** Markdown in, Markdown out. A plain `scratchpad.md` stays
  in sync next to your data at all times, so even if you set Blurt on fire
  tomorrow, your notes open in any editor on earth.
- **Keyboard first.** A `/` menu for formatting (to-do, headings, code, all of
  it), lists that continue themselves, URLs that auto-link. No toolbars.

## Install

First, [Ollama](https://ollama.com/download) (the local brain). Then Blurt itself,
the easy way, with [pipx](https://pipx.pypa.io) and Python 3.11+:

```bash
pipx install git+https://github.com/rbsriram/blurt
blurt
```

`blurt` opens it in your browser. The first run sets things up and pulls the
embedding model (about 270MB, once). That is it.

No pipx? Any of these also work:

```bash
pip install --user git+https://github.com/rbsriram/blurt && blurt   # plain pip

# or a one-line installer (it just downloads a release and writes a launcher,
# no build step; read it first, that is why it is short):
curl -fsSL https://raw.githubusercontent.com/rbsriram/blurt/main/install.sh | bash

# or from a clone:
git clone https://github.com/rbsriram/blurt && cd blurt && ./setup.sh
```

Your notes live in `~/.local/share/blurt/` (a SQLite file and a plain
`scratchpad.md`), never in the cloud. Press `?` in the app for the keys.

## The keys (short version)

| Key | Does |
| --- | --- |
| `Enter` | save the note |
| `Shift+Enter` | new line (and continues a list) |
| `/` | formatting menu, at the start of a line |
| `Up` | peek at matching notes, then edit one in place |
| `Cmd/Ctrl+F` | search |
| `Cmd/Ctrl+S` | download everything as Markdown |
| `Cmd/Ctrl+Z` | undo the last thing |
| `?` | the full cheatsheet |

## Under the hood (for the curious)

SQLite holds the notes and their embeddings. A background worker embeds new notes
without ever slowing down your typing. Search is hybrid: exact text matches the
instant you save, semantic catches up a beat later. The vector index only ever
holds your live notes, so deleting one removes it from search for free. The server
binds to localhost, and that is the security model: it is on your computer, and it
stays there. The full threat model, including one destructive test-only endpoint
that stays disabled unless you opt in, is written up in
[`docs/SECURITY.md`](docs/SECURITY.md).

Want the full map? See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Want every
place the plan and the build disagreed, and why? See
[`docs/DECISIONS.md`](docs/DECISIONS.md). It is an honest log.

## Contributing

Issues and pull requests are welcome. See
[`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md) to get set up. The rule of
thumb: capture stays instant, the UI stays quiet, and nothing phones home.

## Built by

Sriram ([@rbsriram](https://github.com/rbsriram)) and
[Claude Code](https://claude.com/claude-code). Sriram had the idea and made every
call. Claude did a lot of the typing. Good team.

MIT licensed. Take it, fork it, make it weirder.
