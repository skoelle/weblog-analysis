# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""SQLite schema and connection helpers."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from pathlib import Path

SCHEMA_VERSION = 3
LINE_COUNTS_VERSION = 2

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
    ingested_at TEXT NOT NULL,
    max_day     TEXT
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
    asn         TEXT,
    sig         BLOB
);

CREATE TABLE IF NOT EXISTS line_counts (
    sig         BLOB NOT NULL,
    source_file TEXT NOT NULL,
    n           INTEGER NOT NULL,
    PRIMARY KEY (sig, source_file)
) WITHOUT ROWID;

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

# Columns that identify a log line. Everything else in ``requests`` is derived
# from these values, so two rows with the same signature are interchangeable.
SIG_FIELDS = ("ts", "ip", "vhost_raw", "method", "path", "status", "bytes", "referer", "ua")


def line_signature(*values: object) -> bytes:
    """Stable 16-byte fingerprint for a log line.

    Values are length-prefixed so different splits can never produce the same
    byte stream. ``None`` (a missing size field) is encoded distinctly from the
    empty string.
    """
    digest = hashlib.blake2b(digest_size=16)
    for value in values:
        if value is None:
            digest.update(b"\x00")
            continue
        data = str(value).encode("utf-8", "surrogatepass")
        digest.update(b"\x01")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.digest()


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _backfill_signatures(conn: sqlite3.Connection) -> None:
    """Compute ``sig`` for rows written before the column existed."""
    update = "UPDATE requests SET sig = ? WHERE id = ?"
    missing = conn.execute("SELECT COUNT(*) FROM requests WHERE sig IS NULL").fetchone()[0]
    if not missing:
        return
    rows = conn.execute(
        f"SELECT id, {', '.join(SIG_FIELDS)} FROM requests WHERE sig IS NULL"
    ).fetchall()
    payload: list[tuple[bytes, int]] = []
    for row in rows:
        payload.append((line_signature(*(row[name] for name in SIG_FIELDS)), row["id"]))
        if len(payload) >= 5000:
            conn.executemany(update, payload)
            payload.clear()
    if payload:
        conn.executemany(update, payload)


def _build_line_counts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO line_counts(sig, source_file, n)
        SELECT sig, source_file, COUNT(*) FROM requests
        WHERE sig IS NOT NULL
        GROUP BY sig, source_file
        """
    )


def _migrate(conn: sqlite3.Connection) -> None:
    if not _has_column(conn, "requests", "sig"):
        conn.execute("ALTER TABLE requests ADD COLUMN sig BLOB")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_sig ON requests(sig)")

    row = conn.execute("SELECT value FROM meta WHERE key = 'line_counts_version'").fetchone()
    if not (row and row["value"] == str(LINE_COUNTS_VERSION)):
        _backfill_signatures(conn)
        current = conn.execute("SELECT COUNT(*) FROM line_counts").fetchone()[0]
        if not current:
            _build_line_counts(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('line_counts_version', ?)",
            (str(LINE_COUNTS_VERSION),),
        )

    if not _has_column(conn, "ingested_files", "max_day"):
        conn.execute("ALTER TABLE ingested_files ADD COLUMN max_day TEXT")

    row = conn.execute("SELECT value FROM meta WHERE key = 'max_day_version'").fetchone()
    if not (row and row["value"] == str(SCHEMA_VERSION)):
        _backfill_max_day(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('max_day_version', ?)",
            (str(SCHEMA_VERSION),),
        )


def _backfill_max_day(conn: sqlite3.Connection) -> None:
    """Fill ``max_day`` for files tracked before the column existed.

    Files whose lines were fully deduplicated away have no rows of their own.
    Dropping their tracking row makes the next ingest read them once more, so
    the retention logic always has a real date to work with.
    """
    conn.execute(
        """
        UPDATE ingested_files SET max_day = (
            SELECT MAX(day) FROM requests WHERE requests.source_file = ingested_files.path
        )
        WHERE max_day IS NULL
        """
    )
    conn.execute("DELETE FROM ingested_files WHERE max_day IS NULL")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    _migrate(conn)
    conn.commit()


def iter_signature_chunks(signatures: Iterable[bytes], size: int = 500) -> Iterable[list[bytes]]:
    chunk: list[bytes] = []
    for sig in signatures:
        chunk.append(sig)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
