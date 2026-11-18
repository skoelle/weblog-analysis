# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Tests for the category filter in the breakdown queries."""

from pathlib import Path

from app import queries
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
    conn = connect(tmp_path / "q.sqlite")
    init_db(conn)
    _insert(conn, category="browser", status=200, referer="https://a.example.org")
    _insert(
        conn,
        category="search_bot",
        bot_name="Googlebot",
        status=404,
        referer="https://b.example.org",
        net="198.51.100.0",
    )
    _insert(
        conn,
        category="scanner",
        status=403,
        net="203.0.113.0",
    )
    _insert(
        conn,
        category="ai_bot",
        bot_name="GPTBot",
        status=200,
        referer="https://c.example.org",
    )
    conn.commit()
    return conn


def test_bot_breakdown_honours_category(tmp_path: Path):
    conn = _seeded_conn(tmp_path)
    try:
        names = {row["name"] for row in queries.bot_breakdown(conn)}
        assert names == {"Googlebot", "scanner", "GPTBot"}

        scanner = queries.bot_breakdown(conn, category="scanner")
        assert [row["name"] for row in scanner] == ["scanner"]
        assert scanner[0]["category"] == "scanner"
        assert scanner[0]["hits"] == 1

        assert queries.bot_breakdown(conn, category="browser") == []
    finally:
        conn.close()


def test_status_breakdown_honours_category(tmp_path: Path):
    conn = _seeded_conn(tmp_path)
    try:
        rows = queries.status_breakdown(conn, category="search_bot")
        assert [(row["status"], row["hits"]) for row in rows] == [(404, 1)]
    finally:
        conn.close()


def test_referer_breakdown_honours_category(tmp_path: Path):
    conn = _seeded_conn(tmp_path)
    try:
        rows = queries.referer_breakdown(conn, category="ai_bot")
        assert [row["referer"] for row in rows] == ["https://c.example.org"]

        all_refs = {row["referer"] for row in queries.referer_breakdown(conn)}
        assert all_refs == {
            "https://a.example.org",
            "https://b.example.org",
            "https://c.example.org",
        }
    finally:
        conn.close()
