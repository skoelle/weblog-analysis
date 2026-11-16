# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Incremental ingestion of log files into SQLite.

Log files overlap: a weekly bundle and the split daily files contain the same
lines, and a half-downloaded file can later be replaced by the complete one.
Instead of comparing file names, ingestion works on line signatures. For every
signature the database keeps the highest occurrence count seen in any single
file, so overlapping files never double count while repeated identical lines
inside one file are preserved.
"""

from __future__ import annotations

import gzip
import logging
import sqlite3
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from . import classify
from .config import Config
from .config import config as default_config
from .db import SIG_FIELDS, connect, init_db, iter_signature_chunks, line_signature
from .parser import ParsedLine, ParseError, parse_line
from .selfip import fetch_self_networks

log = logging.getLogger(__name__)

INSERT_SQL = """
INSERT INTO requests (
    ts, day, iso_week, ip, net, ip_version, vhost_raw, vhost, method, path,
    status, bytes, referer, ua, category, bot_name, is_scanner_path, is_self,
    source_file, sig
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""

LOG_SUFFIXES = (".log", ".gz", ".txt")
BATCH_SIZE = 5000


def iter_log_files(log_dir: str | Path) -> Iterator[Path]:
    root = Path(log_dir)
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("access.log") or path.suffix.lower() in LOG_SUFFIXES:
            yield path


def open_log(path: Path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_file(path: Path, aliases: dict[str, str] | None = None) -> tuple[list[ParsedLine], int]:
    parsed: list[ParsedLine] = []
    failed = 0
    with open_log(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                parsed.append(parse_line(line, aliases))
            except ParseError as exc:
                failed += 1
                if failed <= 3:
                    log.warning("%s: %s", path.name, exc)
    return parsed, failed


def _file_key(path: Path) -> str:
    """Stable identity for a log file.

    Uses the basename so the same physical file is recognised whether the logs
    are mounted at ``/logs`` (container) or read directly (host).
    """
    return path.name


def _is_unchanged(conn: sqlite3.Connection, path: Path) -> bool:
    stat = path.stat()
    row = conn.execute(
        "SELECT size, mtime FROM ingested_files WHERE path = ?", (_file_key(path),)
    ).fetchone()
    if row is None:
        return False
    return row["size"] == stat.st_size and abs(row["mtime"] - stat.st_mtime) < 1.0


def _row_tuple(item: ParsedLine, self_networks: set[str], source_file: str) -> tuple:
    cls = classify.classify(item.ua, item.path)
    is_self = 1 if item.net in self_networks else 0
    sig = line_signature(*(getattr(item, name) for name in SIG_FIELDS))
    return (
        item.ts,
        item.day,
        item.iso_week,
        item.ip,
        item.net,
        item.ip_version,
        item.vhost_raw,
        item.vhost,
        item.method,
        item.path,
        item.status,
        item.bytes,
        item.referer,
        item.ua,
        cls.category,
        cls.bot_name,
        1 if cls.is_scanner_path else 0,
        is_self,
        source_file,
        sig,
    )


def _counts_by_signature(rows: Iterable[tuple]) -> dict[bytes, int]:
    sig_index = len(INSERT_SQL.split("?")) - 2
    return dict(Counter(row[sig_index] for row in rows))


def _existing_counts(conn: sqlite3.Connection, signatures: list[bytes]) -> dict[bytes, int]:
    counts: dict[bytes, int] = {}
    for chunk in iter_signature_chunks(signatures):
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT sig, COUNT(*) AS n FROM requests WHERE sig IN ({placeholders}) GROUP BY sig",
            chunk,
        ).fetchall()
        for row in rows:
            counts[bytes(row["sig"])] = row["n"]
    return counts


def _other_counts(conn: sqlite3.Connection, signatures: list[bytes]) -> dict[bytes, int]:
    """Highest occurrence count for each signature in any other file."""
    counts: dict[bytes, int] = {}
    for chunk in iter_signature_chunks(signatures):
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT sig, MAX(n) AS n FROM line_counts WHERE sig IN ({placeholders}) GROUP BY sig",
            chunk,
        ).fetchall()
        for row in rows:
            counts[bytes(row["sig"])] = row["n"]
    return counts


def _trim_signature(conn: sqlite3.Connection, sig: bytes, excess: int) -> None:
    conn.execute(
        """
        DELETE FROM requests WHERE id IN (
            SELECT id FROM requests WHERE sig = ? ORDER BY id DESC LIMIT ?
        )
        """,
        (sig, excess),
    )


def _ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    self_networks: set[str],
    aliases: dict[str, str] | None,
) -> tuple[int, int, int]:
    """Ingest one file, adding only lines no other file has more often.

    Returns ``(inserted, failed, skipped_as_duplicate)``.
    """
    parsed, failed = parse_file(path, aliases)
    key = _file_key(path)
    rows = [_row_tuple(item, self_networks, key) for item in parsed]

    # Rows of this file are dropped first: the signature counts of the other
    # files stay authoritative, this file contributes on top of them.
    conn.execute("DELETE FROM requests WHERE source_file = ?", (key,))
    conn.execute("DELETE FROM line_counts WHERE source_file = ?", (key,))

    counts = _counts_by_signature(rows)
    by_sig: dict[bytes, list[tuple]] = {}
    for row in rows:
        by_sig.setdefault(row[-1], []).append(row)

    signatures = list(counts)
    other = _other_counts(conn, signatures)
    current = _existing_counts(conn, signatures)

    insert_rows: list[tuple] = []
    skipped = 0
    for sig in signatures:
        target = max(counts[sig], other.get(sig, 0))
        have = current.get(sig, 0)
        if target > have:
            insert_rows.extend(by_sig[sig][: target - have])
        elif target < have:
            _trim_signature(conn, sig, have - target)
        skipped += counts[sig] - (target - max(have, 0))

    for start in range(0, len(insert_rows), BATCH_SIZE):
        conn.executemany(INSERT_SQL, insert_rows[start : start + BATCH_SIZE])

    conn.executemany(
        "INSERT OR REPLACE INTO line_counts(sig, source_file, n) VALUES (?, ?, ?)",
        [(sig, key, counts[sig]) for sig in signatures],
    )

    stat = path.stat()
    # Newest log day in the file, used by retention. Taken from the parsed
    # lines, not the file mtime, because mtime changes on every download.
    max_day = max((item.day for item in parsed), default=None)
    conn.execute(
        """
        INSERT INTO ingested_files(
            path, size, mtime, lines, parsed, failed, ingested_at, max_day
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size=excluded.size, mtime=excluded.mtime, lines=excluded.lines,
            parsed=excluded.parsed, failed=excluded.failed,
            ingested_at=excluded.ingested_at, max_day=excluded.max_day
        """,
        (
            key,
            stat.st_size,
            stat.st_mtime,
            len(parsed) + failed,
            len(parsed),
            failed,
            datetime.now(UTC).isoformat(timespec="seconds"),
            max_day,
        ),
    )
    return len(insert_rows), failed, max(skipped, 0)


def _delete_file_rows(conn: sqlite3.Connection, path: Path) -> None:
    """Remove previously ingested rows for a file that is being re-read."""
    key = _file_key(path)
    conn.execute("DELETE FROM requests WHERE source_file = ?", (key,))
    conn.execute("DELETE FROM line_counts WHERE source_file = ?", (key,))
    conn.execute("DELETE FROM ingested_files WHERE path = ?", (key,))


def rebuild(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM requests")
    conn.execute("DELETE FROM line_counts")
    conn.execute("DELETE FROM ingested_files")
    conn.commit()


def ingest(
    cfg: Config | None = None,
    *,
    rebuild_first: bool = False,
    refresh_self: bool = True,
) -> dict[str, int]:
    cfg = cfg or default_config
    conn = connect(cfg.db_path)
    init_db(conn)

    if rebuild_first:
        rebuild(conn)

    self_networks = set(cfg.self_networks)
    if refresh_self:
        fetched = fetch_self_networks(cfg.history_url)
        if fetched:
            self_networks |= fetched
            conn.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                [("self_net", net) for net in sorted(fetched)],
            )
            conn.commit()

    stats = {
        "files": 0,
        "skipped": 0,
        "parsed": 0,
        "inserted": 0,
        "duplicates": 0,
        "failed": 0,
        "seconds": 0,
    }
    started = time.monotonic()
    for path in iter_log_files(cfg.log_dir):
        if _is_unchanged(conn, path):
            stats["skipped"] += 1
            continue
        inserted, failed, duplicates = _ingest_file(conn, path, self_networks, cfg.vhost_aliases)
        conn.commit()
        stats["files"] += 1
        stats["parsed"] += inserted + duplicates
        stats["inserted"] += inserted
        stats["duplicates"] += duplicates
        stats["failed"] += failed
        log.info(
            "%s: %d parsed, %d inserted, %d duplicates, %d failed",
            path.name,
            inserted + duplicates,
            inserted,
            duplicates,
            failed,
        )

    stats["seconds"] = int(time.monotonic() - started)
    conn.close()
    return stats


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest web server logs into SQLite")
    parser.add_argument("--rebuild", action="store_true", help="drop all data first")
    parser.add_argument("--log-dir", help="override LOG_DIR")
    parser.add_argument("--db", help="override DB_PATH")
    parser.add_argument("--no-self", action="store_true", help="skip self-IP lookup")
    args = parser.parse_args()

    cfg = default_config
    if args.log_dir:
        cfg.log_dir = args.log_dir
    if args.db:
        cfg.db_path = args.db

    stats = ingest(cfg, rebuild_first=args.rebuild, refresh_self=not args.no_self)
    print(
        f"files={stats['files']} skipped={stats['skipped']} "
        f"inserted={stats['inserted']} duplicates={stats['duplicates']} "
        f"failed={stats['failed']} seconds={stats['seconds']}"
    )


if __name__ == "__main__":
    main()
