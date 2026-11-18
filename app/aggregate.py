# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Precomputed dashboard aggregate.

The dashboard only ever changes when data is imported, so the heavy
aggregations are materialised once per ingest instead of being recomputed on
every page load. ``agg_net`` keeps one row per network and dimension slice, so
``COUNT(DISTINCT net)`` stays exact while the dashboard scans a table that is
roughly an order of magnitude smaller than ``requests``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from .config import Config
from .config import config as default_config
from .db import connect, init_db

log = logging.getLogger(__name__)

AGG_VERSION = 1

INSERT_SQL = """
INSERT INTO agg_net (
    iso_week, vhost, category, is_self, status, net, ip_version, sample_ip,
    bot_name, hits
)
SELECT iso_week, vhost, category, is_self, status, net, ip_version,
       MIN(ip) AS sample_ip, MAX(bot_name) AS bot_name, COUNT(*) AS hits
FROM requests
GROUP BY iso_week, vhost, category, is_self, status, net
"""


def _write_meta(conn: sqlite3.Connection, **values: Any) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        [(key, str(value)) for key, value in values.items()],
    )


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def build(conn: sqlite3.Connection, *, trigger: str = "manual") -> dict[str, Any]:
    """Rebuild ``agg_net`` from ``requests`` in a single transaction.

    Never raises: a failed build is recorded in ``meta`` and the previous
    aggregate (if any) stays in place, so ingests and the server keep running.
    """
    if conn.in_transaction:
        conn.commit()
    started = time.monotonic()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM agg_net")
        conn.execute(INSERT_SQL)
        rows = conn.execute("SELECT COUNT(*) FROM agg_net").fetchone()[0]
        elapsed = int((time.monotonic() - started) * 1000)
        _write_meta(
            conn,
            agg_version=AGG_VERSION,
            agg_status="ok",
            agg_built_at=datetime.now(UTC).isoformat(timespec="seconds"),
            agg_rows=rows,
            agg_ms=elapsed,
            agg_trigger=trigger,
            agg_error="",
        )
        conn.commit()
        log.info("Dashboard-Aggregat neu gebaut: %d Zeilen in %d ms (%s)", rows, elapsed, trigger)
        return {
            "ok": True,
            "built_at": _get_meta(conn, "agg_built_at"),
            "rows": rows,
            "ms": elapsed,
            "trigger": trigger,
            "error": "",
        }
    except Exception as exc:
        conn.rollback()
        elapsed = int((time.monotonic() - started) * 1000)
        try:
            _write_meta(
                conn,
                agg_status="error",
                agg_built_at=datetime.now(UTC).isoformat(timespec="seconds"),
                agg_ms=elapsed,
                agg_trigger=trigger,
                agg_error=str(exc),
            )
            conn.commit()
        except sqlite3.Error:
            log.warning("Aggregat-Fehlerstatus konnte nicht gespeichert werden", exc_info=True)
        log.warning("Dashboard-Aggregat fehlgeschlagen (%s)", trigger, exc_info=True)
        return {
            "ok": False,
            "built_at": _get_meta(conn, "agg_built_at"),
            "rows": None,
            "ms": elapsed,
            "trigger": trigger,
            "error": str(exc),
        }


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the outcome of the last aggregate build."""
    status_value = _get_meta(conn, "agg_status")
    rows = _get_meta(conn, "agg_rows")
    ms = _get_meta(conn, "agg_ms")
    return {
        "ok": status_value == "ok",
        "status": status_value or "unknown",
        "built_at": _get_meta(conn, "agg_built_at"),
        "rows": int(rows) if rows is not None else None,
        "ms": int(ms) if ms is not None else None,
        "trigger": _get_meta(conn, "agg_trigger"),
        "error": _get_meta(conn, "agg_error") or None,
    }


def is_stale(conn: sqlite3.Connection) -> bool:
    """True when the aggregate is missing, outdated or the last build failed."""
    if _get_meta(conn, "agg_version") != str(AGG_VERSION):
        return True
    if _get_meta(conn, "agg_status") != "ok":
        return True
    has_agg = conn.execute("SELECT 1 FROM agg_net LIMIT 1").fetchone() is not None
    if not has_agg:
        return conn.execute("SELECT 1 FROM requests LIMIT 1").fetchone() is not None
    return False


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Rebuild the dashboard aggregate")
    parser.add_argument("--db", help="override DB_PATH")
    args = parser.parse_args()

    cfg: Config = default_config
    if args.db:
        cfg.db_path = args.db

    conn = connect(cfg.db_path)
    init_db(conn)
    result = build(conn, trigger="manual")
    conn.close()
    if result["ok"]:
        print(f"ok rows={result['rows']} ms={result['ms']}")
    else:
        print(f"failed error={result['error']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
