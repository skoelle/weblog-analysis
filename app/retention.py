# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Retention for the raw log files in ``LOG_DIR``.

Only files are removed, never database rows: the weekly history in SQLite is
the product, the raw logs are just the (re-fetchable) input. The age of a file
comes from its newest log line, not from the file mtime, because the mtime is
reset on every download.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .config import Config
from .config import config as default_config
from .db import connect, init_db
from .ingest import iter_log_files

log = logging.getLogger(__name__)


def cutoff_for(days: int, today: date | None = None) -> date | None:
    """Oldest log day that is still kept; ``None`` when retention is off."""
    if days <= 0:
        return None
    return (today or date.today()) - timedelta(days=days)


def prune_log_files(
    cfg: Config | None = None,
    *,
    days: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete log files whose newest line is older than ``days``.

    Returns counters: ``checked``, ``pruned`` and ``kept``. A file is only
    removed when it is tracked in ``ingested_files``, so files that were never
    imported are left alone.
    """
    cfg = cfg or default_config
    limit = cfg.file_retention_days if days is None else days
    stats = {"checked": 0, "pruned": 0, "kept": 0}
    cutoff = cutoff_for(limit)
    if cutoff is None:
        return stats

    conn = connect(cfg.db_path)
    init_db(conn)
    try:
        tracked = {
            row["path"]: row["max_day"]
            for row in conn.execute("SELECT path, max_day FROM ingested_files")
        }
    finally:
        conn.close()

    for path in iter_log_files(cfg.log_dir):
        stats["checked"] += 1
        max_day = tracked.get(path.name)
        if not max_day:
            stats["kept"] += 1
            continue
        try:
            newest = date.fromisoformat(max_day)
        except ValueError:
            stats["kept"] += 1
            continue
        if newest >= cutoff:
            stats["kept"] += 1
            continue
        if dry_run:
            log.info("wuerde loeschen: %s (letzter Tag %s)", path.name, max_day)
        else:
            try:
                path.unlink()
            except OSError as exc:
                log.warning("konnte %s nicht loeschen: %s", path.name, exc)
                stats["kept"] += 1
                continue
            log.info("geloescht: %s (letzter Tag %s)", path.name, max_day)
        stats["pruned"] += 1

    return stats


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Delete log files older than FILE_RETENTION_DAYS")
    parser.add_argument("--days", type=int, help="override FILE_RETENTION_DAYS")
    parser.add_argument("--dry-run", action="store_true", help="only list candidates")
    parser.add_argument("--log-dir", help="override LOG_DIR")
    parser.add_argument("--db", help="override DB_PATH")
    args = parser.parse_args()

    cfg = default_config
    if args.log_dir:
        cfg.log_dir = args.log_dir
    if args.db:
        cfg.db_path = args.db

    stats = prune_log_files(cfg, days=args.days, dry_run=args.dry_run)
    print(f"checked={stats['checked']} pruned={stats['pruned']} kept={stats['kept']}")


if __name__ == "__main__":
    main()
