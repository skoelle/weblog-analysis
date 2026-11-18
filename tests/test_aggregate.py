# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Tests for the precomputed dashboard aggregate."""

from pathlib import Path

from app import aggregate, queries
from app.db import connect, init_db

BASE_ROW = {
    "ts": "2026-11-14T00:00:00",
    "day": "2026-11-14",
    "iso_week": "2026-W46",
    "ip": "192.0.2.0",
    "net": "192.0.2.0",
    "ip_version": "v4",
    "vhost_raw": "www.example.org",
    "vhost": "example.org",
    "method": "GET",
    "path": "/",
    "status": 200,
    "bytes": 100,
    "referer": "-",
    "ua": "test-agent",
    "category": "browser",
    "bot_name": None,
    "is_scanner_path": 0,
    "is_self": 0,
    "source_file": "access.log.1",
}


def _insert(conn, **overrides):
    row = {**BASE_ROW, **overrides}
    columns = ", ".join(row)
    placeholders = ", ".join("?" * len(row))
    conn.execute(
        f"INSERT INTO requests ({columns}) VALUES ({placeholders})",
        tuple(row.values()),
    )


def _seeded_conn(tmp_path: Path):
    conn = connect(tmp_path / "agg.sqlite")
    init_db(conn)
    _insert(conn)
    _insert(conn, net="198.51.100.0", category="search_bot", bot_name="Googlebot", status=404)
    _insert(conn, net="203.0.113.0", category="scanner", status=403)
    conn.commit()
    return conn


def test_build_matches_expected_values(tmp_path: Path):
    conn = _seeded_conn(tmp_path)
    try:
        result = aggregate.build(conn, trigger="test")
        assert result["ok"] is True
        assert result["rows"] == 3

        status = aggregate.status(conn)
        assert status["ok"] is True
        assert status["status"] == "ok"
        assert status["rows"] == 3
        assert status["trigger"] == "test"
        assert status["built_at"]
        assert status["error"] is None

        weeks = queries.week_overview(conn)
        assert len(weeks) == 1
        assert weeks[0]["hits"] == 3
        assert weeks[0]["unique_nets"] == 3
        assert weeks[0]["human_hits"] == 1
        assert weeks[0]["bot_hits"] == 1
        assert weeks[0]["scanner_hits"] == 1
        assert weeks[0]["status_4xx"] == 2

        nets = queries.top_nets(conn)
        assert sum(n["hits"] for n in nets) == 3
        assert nets[0]["sample_ip"] == "192.0.2.0"
    finally:
        conn.close()


def test_is_stale_until_built(tmp_path: Path):
    conn = _seeded_conn(tmp_path)
    try:
        assert aggregate.is_stale(conn) is True
        aggregate.build(conn)
        assert aggregate.is_stale(conn) is False
    finally:
        conn.close()


def test_is_stale_on_failed_build(tmp_path: Path, monkeypatch):
    conn = _seeded_conn(tmp_path)
    try:
        aggregate.build(conn)
        before = conn.execute("SELECT COUNT(*) FROM agg_net").fetchone()[0]
        assert before == 3

        monkeypatch.setattr(aggregate, "INSERT_SQL", "INSERT INTO does_not_exist SELECT 1")
        result = aggregate.build(conn)

        assert result["ok"] is False
        assert aggregate.status(conn)["status"] == "error"
        assert aggregate.is_stale(conn) is True
        # A failed rebuild must not wipe the previous aggregate.
        assert conn.execute("SELECT COUNT(*) FROM agg_net").fetchone()[0] == before
    finally:
        conn.close()
