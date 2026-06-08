"""Blurt entry point.

    python main.py

Binds to 127.0.0.1 only. Exposing this to a network is an explicit, documented
opt-in (set BLURT_HOST) and is not safe before the v2 auth work lands.
"""

from __future__ import annotations

import logging

import uvicorn

from blurt.app import create_app
from blurt.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
