# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""FastAPI application serving the dashboard and JSON API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .config import config
from .db import connect, init_db
from .fetch import fetch_and_ingest, read_fetch_status
from .info import settings_info
from .ingest import ingest
from .scheduler import scheduler_loop

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

StatusClass = Literal["2xx", "3xx", "4xx", "5xx"]


def get_conn():
    conn = connect(config.db_path)
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_conn()
    init_db(conn)
    conn.close()

    tasks: list[asyncio.Task] = []
    if config.fetch_on_startup:
        tasks.append(asyncio.create_task(asyncio.to_thread(fetch_and_ingest, config)))
    if config.fetch_schedule:
        tasks.append(asyncio.create_task(scheduler_loop(config)))

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Weblog Analysis", lifespan=lifespan)


def _read(fn, *args, **kwargs):
    conn = get_conn()
    try:
        return fn(conn, *args, **kwargs)
    finally:
        conn.close()


@app.get("/api/health")
def health() -> dict:
    try:
        data = _read(queries.summary)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok", **data}


@app.get("/api/weeks")
def weeks(
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
) -> list[dict]:
    return _read(
        queries.week_overview,
        vhost=vhost,
        category=category,
        exclude_self=exclude_self,
        status_class=status_class,
    )


@app.get("/api/filters")
def filters() -> dict:
    return {
        "weeks": _read(queries.weeks_list),
        "vhosts": _read(queries.vhosts_list),
    }


@app.get("/api/vhosts")
def vhosts(
    week: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
) -> list[dict]:
    return _read(
        queries.vhost_breakdown,
        iso_week=week,
        exclude_self=exclude_self,
        status_class=status_class,
    )


@app.get("/api/categories")
def categories(
    week: str | None = None,
    vhost: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
) -> list[dict]:
    return _read(
        queries.category_breakdown,
        iso_week=week,
        vhost=vhost,
        exclude_self=exclude_self,
        status_class=status_class,
    )


@app.get("/api/bots")
def bots(
    week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
    limit: int = Query(25, ge=1, le=200),
) -> list[dict]:
    return _read(
        queries.bot_breakdown,
        iso_week=week,
        vhost=vhost,
        category=category,
        exclude_self=exclude_self,
        status_class=status_class,
        limit=limit,
    )


@app.get("/api/paths")
def paths(
    week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
    limit: int = Query(25, ge=1, le=500),
) -> list[dict]:
    return _read(
        queries.top_paths,
        iso_week=week,
        vhost=vhost,
        category=category,
        exclude_self=exclude_self,
        status_class=status_class,
        limit=limit,
    )


@app.get("/api/nets")
def nets(
    week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    return _read(
        queries.top_nets,
        iso_week=week,
        vhost=vhost,
        category=category,
        exclude_self=exclude_self,
        status_class=status_class,
        limit=limit,
    )


@app.get("/api/nets/{net:path}")
def net_detail(
    net: str,
    week: str | None = None,
    status_class: StatusClass | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> dict:
    data = _read(queries.net_detail, net, iso_week=week, status_class=status_class, limit=limit)
    if data["summary"] is None:
        raise HTTPException(status_code=404, detail="network not found")
    return data


@app.get("/api/status")
def status(
    week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
) -> list[dict]:
    return _read(
        queries.status_breakdown,
        iso_week=week,
        vhost=vhost,
        category=category,
        exclude_self=exclude_self,
        status_class=status_class,
    )


@app.get("/api/referers")
def referers(
    week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: StatusClass | None = None,
    limit: int = Query(25, ge=1, le=200),
) -> list[dict]:
    return _read(
        queries.referer_breakdown,
        iso_week=week,
        vhost=vhost,
        category=category,
        exclude_self=exclude_self,
        status_class=status_class,
        limit=limit,
    )


@app.post("/api/ingest")
def trigger_ingest(rebuild: bool = False) -> dict:
    stats = ingest(rebuild_first=rebuild)
    return stats


@app.get("/api/fetch/status")
def fetch_status() -> dict:
    return {"last": read_fetch_status(config)}


@app.get("/api/settings")
def settings() -> dict:
    try:
        return settings_info(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/fetch")
def trigger_fetch(force: bool = False) -> dict:
    try:
        return fetch_and_ingest(config, force=force)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
