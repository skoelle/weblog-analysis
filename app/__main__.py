# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Entry point for `uvicorn app.main:app` / `python -m app`."""

from __future__ import annotations

import uvicorn

from .config import config


def main() -> None:
    uvicorn.run("app.main:app", host=config.host, port=config.port)


if __name__ == "__main__":
    main()
