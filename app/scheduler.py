# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Background scheduler that runs the SFTP fetch at fixed local times."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .config import Config
from .fetch import fetch_and_ingest

log = logging.getLogger(__name__)

CHECK_INTERVAL = 30.0


async def scheduler_loop(cfg: Config) -> None:
    """Run ``fetch_and_ingest`` once per configured slot.

    Slots are local wall-clock times like ``01:30``. Each slot runs at most
    once per day; a tick every 30 seconds keeps drift negligible.
    """
    slots = set(cfg.fetch_schedule)
    if not slots:
        return
    done: set[tuple[str, tuple[int, int]]] = set()
    log.info("SFTP-Fetch geplant für: %s", ", ".join(f"{h:02d}:{m:02d}" for h, m in slots))

    while True:
        now = datetime.now()
        slot = (now.hour, now.minute)
        key = (now.date().isoformat(), slot)
        if slot in slots and key not in done:
            done = {entry for entry in done if entry[0] == now.date().isoformat()}
            done.add(key)
            log.info("SFTP-Fetch startet (%02d:%02d)", *slot)
            try:
                result = await asyncio.to_thread(fetch_and_ingest, cfg)
                log.info("SFTP-Fetch fertig: %s", result)
            except Exception:
                log.exception("SFTP-Fetch fehlgeschlagen")
        await asyncio.sleep(CHECK_INTERVAL)
