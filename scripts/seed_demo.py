"""Seed the demo server (port 7343) with a relatable 'dumped everything' pile, wait for
the semantic index to catch up, then print what the ghost surfaces for a few candidate
queries so we can pick the cleanest reveal for the demo GIF. Throwaway server only."""

import time

import httpx

BASE = "http://127.0.0.1:7343"

# A believable dump: meetings, links, checklists, a table, life admin, random thoughts.
NOTES = [
    "ping sarah about the project alpha design review",
    "project alpha kickoff moved to next thursday, pushed to friday 3pm",
    "project alpha still needs the budget approved before we start",
    "good local models live at https://ollama.com/library",
    "weekend\n- [x] book the campsite\n- [ ] borrow the tent from dan\n- [ ] actually pack",
    "laptop options\n| model | price | notes |\n| --- | --- | --- |\n"
    "| air m3 | 1099 | light, enough |\n| pro 14 | 1599 | probably overkill |",
    "mike the plumber: 555 0182, only does tuesdays",
    "wifi at the cafe downtown is actually fast",
    "movie night\n- [x] the conversation\n- [ ] paris, texas\n- [ ] perfect days",
    "spare key is under the blue flowerpot by the back door",
    "book idea: the one about the lighthouse keeper",
    "remember to actually take a break today",
]

CANDIDATE_QUERIES = [
    "what is going on with project alpha",
    "where did i hide the spare key",
    "how do i get in if i am locked out",
    "who is the plumber and when does he come",
    "what was that movie i wanted to watch",
]


def main():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        for n in NOTES:
            c.post("/api/entries", json={"content": n})
        # Wait for background embedding so the semantic ghost (not just exact match) works.
        for _ in range(60):
            st = c.get("/api/status").json()
            if st.get("indexing_pending", 1) == 0:
                break
            time.sleep(1)
        print("indexing_pending:", c.get("/api/status").json().get("indexing_pending"))
        print()
        for q in CANDIDATE_QUERIES:
            r = c.post("/api/suggest", json={"text": q}).json()
            ms = r.get("matches", [])
            print(f"QUERY: {q!r}  -> {len(ms)} matches")
            for m in ms[:4]:
                print(f"    {m.get('score', 0):.2f}  {m['content'][:60]!r}")
            print()


if __name__ == "__main__":
    main()
