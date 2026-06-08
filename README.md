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

I am not a coder. I am a person with a brain that leaks.

I have tried every notes app. They are all junk drawers. You dump things in, and
three weeks later you are scrolling like a maniac looking for the wifi password
you definitely wrote down, and you give up and write it down again, and now there
are four wifi passwords and you trust none of them.

I did not want a better junk drawer. I wanted a notepad that taps me on the
shoulder and says "hey, you already wrote this, want to fix the old one?" before
I make a fifth copy. That is the whole product. Everything else exists so that
one tap on the shoulder feels instant, stays private, and never gets in the way.

So I built it. I had a lot of help. But it is mine and I love it.

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

## Run it

You need Python 3.11+ and [Ollama](https://ollama.com/download) for the local
embedding model (about 270MB, downloaded once).

```bash
git clone https://github.com/rbsriram/blurt && cd blurt
./setup.sh                  # venv, deps, and pulls the embed model
./.venv/bin/python main.py  # then open http://localhost:7337
```

Open the page, start typing, press `?` any time for the keys.

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

Sriram ([@rbsriram](https://github.com/rbsriram)), who still cannot code and is
delighted about it.

MIT licensed. Take it, fork it, make it weirder.
