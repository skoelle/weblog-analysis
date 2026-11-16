# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Entry point for `uvicorn app.main:app` / `python -m app`."""

from __future__ import annotations

import logging

import uvicorn

from .config import config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    for noisy in ("paramiko", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    uvicorn.run("app.main:app", host=config.host, port=config.port)


if __name__ == "__main__":
    main()
