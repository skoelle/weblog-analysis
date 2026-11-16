# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Config:
    log_dir: str = field(default_factory=lambda: os.environ.get("LOG_DIR", "/logs"))
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


def _parse_aliases(raw: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for pair in _split_csv(raw):
        if "=" not in pair:
            continue
        source, target = pair.split("=", 1)
        aliases[source.strip().lower()] = target.strip().lower()
    return aliases


config = Config()
