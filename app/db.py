# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""SQLite schema and connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingested_files (
    path        TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    lines       INTEGER NOT NULL,
    parsed      INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    iso_week    TEXT NOT NULL,
    ip          TEXT NOT NULL,
    net         TEXT NOT NULL,
    ip_version  TEXT NOT NULL,
    vhost_raw   TEXT NOT NULL,
    vhost       TEXT NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status      INTEGER NOT NULL,
    bytes       INTEGER,
    referer     TEXT NOT NULL,
    ua          TEXT NOT NULL,
    category    TEXT NOT NULL,
    bot_name    TEXT,
    is_scanner_path INTEGER NOT NULL DEFAULT 0,
    is_self     INTEGER NOT NULL DEFAULT 0,
    source_file TEXT NOT NULL DEFAULT '',
    country_code TEXT,
    region      TEXT,
    city        TEXT,
    asn         TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_week  ON requests(iso_week);
CREATE INDEX IF NOT EXISTS idx_requests_ts    ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_net   ON requests(net);
CREATE INDEX IF NOT EXISTS idx_requests_vhost ON requests(vhost);
CREATE INDEX IF NOT EXISTS idx_requests_cat   ON requests(category);
CREATE INDEX IF NOT EXISTS idx_requests_ip    ON requests(ip);
CREATE INDEX IF NOT EXISTS idx_requests_src   ON requests(source_file);
CREATE INDEX IF NOT EXISTS idx_requests_week_vhost ON requests(iso_week, vhost);
CREATE INDEX IF NOT EXISTS idx_requests_week_cat   ON requests(iso_week, category);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
