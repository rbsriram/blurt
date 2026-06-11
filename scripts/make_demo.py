"""Drive the real Blurt UI headless and record the core loop into a GIF.

The story: you start writing a note, the ghost surfaces the few existing notes you already
wrote about it, you step into the best match and edit it in place instead of adding a
duplicate. Assumes a seeded demo server is running (scripts/seed_demo.py, port 7343).
Dev-only (needs playwright).

    ./.venv/bin/python scripts/make_demo.py --theme light --out docs/demo.gif
"""

import argparse
import io

from PIL import Image
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:7343"
# A note you're about to jot; it matches the existing "...still needs the budget
# approved..." note, which you then edit to say it's done.
NOTE = "project alpha budget got approved"
EDIT = "project alpha is approved, we can start"
W, H, SCALE = 900, 620, 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
    ap.add_argument("--out", default="/tmp/demo.gif")
    args = ap.parse_args()

    frames, durations = [], []

    def grab(page, ms):
        frames.append(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))
        durations.append(ms)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": W, "height": H}, device_scale_factor=SCALE
        )
        ctx.add_init_script(f"localStorage.setItem('blurt-theme', '{args.theme}')")
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector(".entry", timeout=10000)
        page.wait_for_timeout(2300)        # let the open splash finish

        grab(page, 1200)                   # rest on the pile

        # 1. start writing a note
        page.click("#compose")
        for ch in NOTE:
            page.keyboard.type(ch)
            page.wait_for_timeout(40)
            grab(page, 55)

        # 2. the ghost surfaces the notes you already wrote
        page.wait_for_selector("#peek .peek-line", timeout=8000)
        for _ in range(4):
            page.wait_for_timeout(110)
            grab(page, 110)
        grab(page, 900)

        # 3. step into the best match (↑ focuses the top one)
        page.keyboard.press("ArrowUp")
        page.wait_for_timeout(140)
        grab(page, 1300)                   # the selected match, expanded

        # 4. open it in place and edit it instead of duplicating
        page.keyboard.press("Enter")
        page.wait_for_selector(".entry-edit", timeout=8000)
        page.wait_for_timeout(160)
        grab(page, 1000)
        page.keyboard.press("ControlOrMeta+a")
        page.wait_for_timeout(120)
        grab(page, 350)
        for ch in EDIT:
            page.keyboard.type(ch)
            page.wait_for_timeout(40)
            grab(page, 55)
        grab(page, 700)

        # 5. save; the note updates in place
        page.keyboard.press("Enter")
        page.wait_for_selector(".entry-edit", state="detached", timeout=8000)
        page.wait_for_timeout(180)
        grab(page, 3200)                   # rest on the updated note
        browser.close()

    # Downscale and use a small shared palette: the UI is near-monochrome, so 64 colours
    # is plenty and keeps the GIF small enough to load fast in a README.
    fw = 760
    fh = round(fw * H / W)
    frames = [f.resize((fw, fh), Image.LANCZOS) for f in frames]
    pal = frames[len(frames) // 2].quantize(colors=64, method=Image.MAXCOVERAGE)
    frames = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]
    frames[0].save(
        args.out, save_all=True, append_images=frames[1:], duration=durations,
        loop=0, optimize=True, disposal=2,
    )
    print(f"wrote {args.out}  ({len(frames)} frames)")


if __name__ == "__main__":
    main()
