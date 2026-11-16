# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
import asyncio
import contextlib
from datetime import datetime

from app import scheduler as scheduler_module
from app.config import Config


def test_parse_schedule_ignores_invalid_entries():
    from app.config import _parse_schedule

    assert _parse_schedule("01:30") == [(1, 30)]
    assert _parse_schedule("13:30, 01:30") == [(1, 30), (13, 30)]
    assert _parse_schedule("24:00,12:99,abc,:,12") == []
    assert _parse_schedule("") == []


def test_scheduler_runs_each_slot_once(monkeypatch):
    calls: list[datetime] = []

    def fake_fetch(cfg):
        calls.append(datetime.now())
        return {"files": 0, "skipped": 0, "downloaded": 0, "ingest": {}}

    monkeypatch.setattr(scheduler_module, "fetch_and_ingest", fake_fetch)
    monkeypatch.setattr(scheduler_module, "CHECK_INTERVAL", 0.01)

    now = datetime.now()
    cfg = Config(fetch_schedule=[(now.hour, now.minute)])

    async def run() -> None:
        task = asyncio.create_task(scheduler_module.scheduler_loop(cfg))
        await asyncio.sleep(0.15)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) == 1


def test_scheduler_keeps_running_after_an_error(monkeypatch):
    calls = []

    def failing_fetch(cfg):
        calls.append(1)
        raise RuntimeError("sftp down")

    monkeypatch.setattr(scheduler_module, "fetch_and_ingest", failing_fetch)
    monkeypatch.setattr(scheduler_module, "CHECK_INTERVAL", 0.01)

    now = datetime.now()
    cfg = Config(fetch_schedule=[(now.hour, now.minute)])

    async def run() -> None:
        task = asyncio.create_task(scheduler_module.scheduler_loop(cfg))
        await asyncio.sleep(0.15)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    # The failing slot is recorded, so it is not retried in the same minute,
    # but the loop itself survives.
    assert len(calls) == 1


def test_scheduler_without_schedule_exits_immediately():
    async def run() -> None:
        await asyncio.wait_for(scheduler_module.scheduler_loop(Config(fetch_schedule=[])), 1)

    asyncio.run(run())
