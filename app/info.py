# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Read-only configuration, storage and data insights for the settings page.

Secrets are deliberately excluded: the SFTP password and the pinned host key
never leave the process, only whether and how host key verification is set up.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import aggregate, queries
from .config import Config
from .config import config as default_config
from .db import connect
from .fetch import read_fetch_status
from .selfip import build_intervals, legacy_networks, load_history

LOW_FREE_BYTES = 1024**3
LOW_FREE_PERCENT = 10.0


def app_version() -> str:
    try:
        return metadata.version("weblog-analysis")
    except metadata.PackageNotFoundError:
        return "unknown"


def _timezone_name() -> str:
    configured = os.environ.get("TZ", "").strip()
    if configured:
        return configured
    try:
        return datetime.now().astimezone().tzname() or "local"
    except (OSError, ValueError):  # pragma: no cover - defensive
        return "local"


def runtime_info(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or default_config
    return {
        "version": app_version(),
        "host": cfg.host,
        "port": cfg.port,
        "timezone": _timezone_name(),
        "log_dir": cfg.log_dir,
        "db_path": cfg.db_path,
    }


def _host_key_mode(cfg: Config) -> str:
    if cfg.sftp_host_key:
        return "pinned"
    if cfg.sftp_known_hosts:
        return "known_hosts"
    return "missing"


def fetch_info(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or default_config
    return {
        "configured": bool(cfg.sftp_host),
        "host": cfg.sftp_host,
        "port": cfg.sftp_port,
        "user": cfg.sftp_user,
        "remote_dir": cfg.sftp_remote_dir,
        "host_key_mode": _host_key_mode(cfg),
        "schedule": [f"{hour:02d}:{minute:02d}" for hour, minute in cfg.fetch_schedule],
        "on_startup": cfg.fetch_on_startup,
        "retention_days": cfg.file_retention_days,
    }


def _nearest_existing(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _storage_entry(label: str, path: str) -> tuple[dict[str, Any], int | None]:
    target = Path(path)
    entry: dict[str, Any] = {
        "labels": [label],
        "paths": [path],
        "exists": target.exists(),
    }
    existing = _nearest_existing(target)
    if existing is None:
        entry.update(
            total=None,
            used=None,
            free=None,
            percent_free=None,
            warning=None,
            error="path not found",
        )
        return entry, None
    try:
        usage = shutil.disk_usage(existing)
        device = existing.stat().st_dev
    except OSError as exc:
        entry.update(
            total=None,
            used=None,
            free=None,
            percent_free=None,
            warning=None,
            error=str(exc),
        )
        return entry, None

    percent_free = round(100.0 * usage.free / usage.total, 1) if usage.total else None
    entry.update(
        total=usage.total,
        used=usage.used,
        free=usage.free,
        percent_free=percent_free,
        warning=usage.free < LOW_FREE_BYTES
        or (percent_free is not None and percent_free < LOW_FREE_PERCENT),
        error=None,
    )
    return entry, device


def storage_info(cfg: Config | None = None) -> list[dict[str, Any]]:
    """Free/used space for the configured paths, merged by filesystem."""
    cfg = cfg or default_config
    candidates = [
        ("Logs", cfg.log_dir),
        ("Database", str(Path(cfg.db_path).parent)),
    ]
    entries: list[dict[str, Any]] = []
    by_device: dict[int, dict[str, Any]] = {}
    for label, path in candidates:
        entry, device = _storage_entry(label, path)
        if device is not None and device in by_device:
            by_device[device]["labels"].append(label)
            by_device[device]["paths"].append(path)
            continue
        entries.append(entry)
        if device is not None:
            by_device[device] = entry
    return entries


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _history_host(history_url: str) -> str | None:
    """Return only the host name, never userinfo or a token in the path."""
    if not history_url:
        return None
    try:
        return urlsplit(history_url).hostname or None
    except ValueError:  # pragma: no cover - malformed URLs are user input
        return None


def _add_source(sources: dict[str, list[str]], net: str, source: str) -> None:
    values = sources.setdefault(net, [])
    if source not in values:
        values.append(source)


def _self_info(
    conn: sqlite3.Connection, cfg: Config
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config_nets = set(cfg.self_networks)
    entries = load_history(conn)
    intervals = build_intervals(entries)
    legacy = legacy_networks(conn) if not intervals else set()

    sources: dict[str, list[str]] = {}
    ranges: dict[str, list[dict[str, Any]]] = {}
    for interval in intervals:
        ranges.setdefault(interval.net, []).append({"start": interval.start, "end": interval.end})
    for net in sorted(config_nets):
        _add_source(sources, net, "config")
    for net in sorted(ranges):
        _add_source(sources, net, "history")
    for net in sorted(legacy):
        _add_source(sources, net, "cached")

    networks = [
        {"net": net, "sources": values, "ranges": ranges.get(net, [])}
        for net, values in sorted(sources.items())
    ]
    history = [
        {
            "ts": entry.ts,
            "ip": entry.ip,
            "net": entry.net,
            "trigger": entry.trigger,
            "current": index == len(entries) - 1,
        }
        for index, entry in enumerate(entries)
    ]
    return networks, history


def data_info(conn: sqlite3.Connection, cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or default_config
    summary = queries.summary(conn)
    files = conn.execute(
        """
        SELECT COUNT(*) AS files,
               COALESCE(SUM(lines), 0) AS lines,
               COALESCE(SUM(parsed), 0) AS parsed,
               COALESCE(SUM(failed), 0) AS failed,
               MIN(max_day) AS oldest_day,
               MAX(max_day) AS newest_day
        FROM ingested_files
        """
    ).fetchone()
    self_stats = conn.execute(
        "SELECT COUNT(*) AS hits, COUNT(DISTINCT net) AS nets FROM requests WHERE is_self = 1"
    ).fetchone()
    self_networks, self_history = _self_info(conn, cfg)
    db_path = Path(cfg.db_path)
    return {
        **summary,
        "tracked_files": files["files"],
        "log_lines": files["lines"],
        "parsed_lines": files["parsed"],
        "failed_lines": files["failed"],
        "oldest_day": files["oldest_day"],
        "newest_day": files["newest_day"],
        "db_size": _file_size(db_path),
        "wal_size": _file_size(Path(f"{db_path}-wal")),
        "self_network_list": self_networks,
        "self_network_count": len(self_networks),
        "self_history": self_history,
        "history_configured": bool(cfg.history_url),
        "history_host": _history_host(cfg.history_url),
        "self_hits": self_stats["hits"],
        "self_nets_observed": self_stats["nets"],
        "vhost_aliases": dict(cfg.vhost_aliases),
        "retention_days": cfg.file_retention_days,
        "aggregate": aggregate.status(conn),
    }


def settings_info(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or default_config
    conn = connect(cfg.db_path)
    try:
        data = data_info(conn, cfg)
    finally:
        conn.close()
    return {
        "runtime": runtime_info(cfg),
        "fetch": fetch_info(cfg),
        "storage": storage_info(cfg),
        "data": data,
        "last_fetch": read_fetch_status(cfg),
    }
