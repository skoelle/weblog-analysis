# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_schedule(raw: str) -> list[tuple[int, int]]:
    """Parse ``01:30,13:30`` into a sorted list of (hour, minute) slots."""
    slots: set[tuple[int, int]] = set()
    for item in _split_csv(raw):
        if ":" not in item:
            continue
        hour, _, minute = item.partition(":")
        try:
            h, m = int(hour), int(minute)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            slots.add((h, m))
    return sorted(slots)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    log_dir: str = field(default_factory=lambda: os.environ.get("LOG_DIR", "/app/data/logs"))
    db_path: str = field(
        default_factory=lambda: os.environ.get("DB_PATH", "/app/data/weblog.sqlite")
    )
    history_url: str = field(default_factory=lambda: os.environ.get("HISTORY_URL", ""))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8095")))
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    vhost_aliases: dict[str, str] = field(
        default_factory=lambda: _parse_aliases(os.environ.get("VHOST_ALIASES", ""))
    )
    self_networks: list[str] = field(
        default_factory=lambda: _split_csv(os.environ.get("SELF_NETWORKS", ""))
    )

    # SFTP source for ``python -m app.fetch`` / the "Fetch" button / scheduler.
    sftp_host: str = field(default_factory=lambda: os.environ.get("SFTP_HOST", ""))
    sftp_port: int = field(default_factory=lambda: int(os.environ.get("SFTP_PORT", "22")))
    sftp_user: str = field(default_factory=lambda: os.environ.get("SFTP_USER", ""))
    sftp_pass: str = field(default_factory=lambda: os.environ.get("SFTP_PASS", ""))
    sftp_host_key: str = field(default_factory=lambda: os.environ.get("SFTP_HOST_KEY", ""))
    sftp_known_hosts: str = field(default_factory=lambda: os.environ.get("SFTP_KNOWN_HOSTS", ""))
    sftp_remote_dir: str = field(default_factory=lambda: os.environ.get("SFTP_REMOTE_DIR", "/logs"))
    fetch_on_startup: bool = field(default_factory=lambda: _bool_env("FETCH_ON_STARTUP"))
    file_retention_days: int = field(
        default_factory=lambda: int(os.environ.get("FILE_RETENTION_DAYS", "400") or 400)
    )
    fetch_schedule: list[tuple[int, int]] = field(
        default_factory=lambda: _parse_schedule(os.environ.get("FETCH_SCHEDULE", ""))
    )


def _parse_aliases(raw: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for pair in _split_csv(raw):
        if "=" not in pair:
            continue
        source, target = pair.split("=", 1)
        aliases[source.strip().lower()] = target.strip().lower()
    return aliases


config = Config()
