"""Run the server with `python -m blurt`.

Binds to 127.0.0.1 only. Exposing it to a network is an explicit, documented
opt-in (set BLURT_HOST) and is not safe before the v2 auth work lands.
"""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(create_app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
