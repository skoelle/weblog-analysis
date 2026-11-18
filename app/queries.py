# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Aggregation queries for the dashboard."""

from __future__ import annotations

import sqlite3
from typing import Any

from .classify import CATEGORIES

BOT_CATEGORIES = ("search_bot", "ai_bot", "monitoring", "scraper")

STATUS_CLASSES: dict[str, tuple[int, int]] = {
    "2xx": (200, 299),
    "3xx": (300, 399),
    "4xx": (400, 499),
    "5xx": (500, 599),
}


def _status_clause(status_class: str | None) -> str | None:
    if not status_class:
        return None
    bounds = STATUS_CLASSES.get(status_class.lower())
    if bounds is None:
        return None
    return f"status BETWEEN {bounds[0]} AND {bounds[1]}"


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def week_overview(
    conn: sqlite3.Connection,
    *,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    if vhost:
        clauses.append("vhost = ?")
    if category:
        clauses.append("category = ?")
    if exclude_self:
        clauses.append("is_self = 0")
    status_clause = _status_clause(status_class)
    if status_clause:
        clauses.append(status_clause)
    where = " AND ".join(clauses)
    sql = f"""
    SELECT
        iso_week,
        COUNT(*) AS hits,
        COUNT(DISTINCT net) AS unique_nets,
        SUM(CASE WHEN category = 'browser' THEN 1 ELSE 0 END) AS human_hits,
        SUM(CASE WHEN category IN ({_placeholders(BOT_CATEGORIES)}) THEN 1 ELSE 0 END)
            AS bot_hits,
        SUM(CASE WHEN category = 'scanner' THEN 1 ELSE 0 END) AS scanner_hits,
        SUM(CASE WHEN category IN ('other', 'empty') THEN 1 ELSE 0 END) AS other_hits,
        SUM(CASE WHEN status BETWEEN 200 AND 299 THEN 1 ELSE 0 END) AS status_2xx,
        SUM(CASE WHEN status BETWEEN 300 AND 399 THEN 1 ELSE 0 END) AS status_3xx,
        SUM(CASE WHEN status BETWEEN 400 AND 499 THEN 1 ELSE 0 END) AS status_4xx,
        SUM(CASE WHEN status BETWEEN 500 AND 599 THEN 1 ELSE 0 END) AS status_5xx
    FROM requests
    WHERE {where}
    GROUP BY iso_week
    ORDER BY iso_week
    """
    params: list[Any] = [*BOT_CATEGORIES]
    if vhost:
        params.append(vhost)
    if category:
        params.append(category)
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _filters(
    iso_week: str | None,
    vhost: str | None,
    category: str | None,
    exclude_self: bool,
    status_class: str | None = None,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if iso_week:
        clauses.append("iso_week = ?")
        params.append(iso_week)
    if vhost:
        clauses.append("vhost = ?")
        params.append(vhost)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if exclude_self:
        clauses.append("is_self = 0")
    status_clause = _status_clause(status_class)
    if status_clause:
        clauses.append(status_clause)
    return " AND ".join(clauses), params


def top_paths(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, vhost, category, exclude_self, status_class)
    sql = f"""
    SELECT path, vhost,
           COUNT(*) AS hits,
           COUNT(DISTINCT net) AS unique_nets,
           SUM(CASE WHEN category = 'browser' THEN 1 ELSE 0 END) AS human_hits
    FROM requests
    WHERE {where}
    GROUP BY path, vhost
    ORDER BY hits DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (*params, limit)).fetchall()
    return [dict(row) for row in rows]


def top_nets(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    vhost: str | None = None,
    category: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, vhost, category, exclude_self, status_class)
    sql = f"""
    SELECT net, ip_version,
           MIN(ip) AS sample_ip,
           COUNT(*) AS hits,
           COUNT(DISTINCT vhost) AS vhosts,
           MAX(bot_name) AS bot_name,
           MAX(category) AS category,
           MAX(is_self) AS is_self
    FROM requests
    WHERE {where}
    GROUP BY net
    ORDER BY hits DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (*params, limit)).fetchall()
    return [dict(row) for row in rows]


def net_detail(
    conn: sqlite3.Connection,
    net: str,
    *,
    iso_week: str | None = None,
    status_class: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    clauses = ["net = ?"]
    params: list[Any] = [net]
    if iso_week:
        clauses.append("iso_week = ?")
        params.append(iso_week)
    status_clause = _status_clause(status_class)
    if status_clause:
        clauses.append(status_clause)
    where = " AND ".join(clauses)

    summary = conn.execute(
        f"""
        SELECT net, ip_version, MIN(ip) AS sample_ip, COUNT(*) AS hits,
               MIN(ts) AS first_seen, MAX(ts) AS last_seen,
               SUM(CASE WHEN category = 'browser' THEN 1 ELSE 0 END) AS human_hits,
               MAX(is_self) AS is_self
        FROM requests WHERE {where}
        GROUP BY net
        """,
        params,
    ).fetchone()
    if summary is None:
        return {"summary": None, "categories": [], "paths": [], "requests": []}

    categories = conn.execute(
        f"""
        SELECT category, COUNT(*) AS hits, MAX(bot_name) AS bot_name
        FROM requests WHERE {where}
        GROUP BY category ORDER BY hits DESC
        """,
        params,
    ).fetchall()
    paths = conn.execute(
        f"""
        SELECT path, vhost, COUNT(*) AS hits
        FROM requests WHERE {where}
        GROUP BY path, vhost ORDER BY hits DESC LIMIT 25
        """,
        params,
    ).fetchall()
    requests = conn.execute(
        f"""
        SELECT ts, method, path, status, vhost, category, bot_name, referer, ua
        FROM requests WHERE {where}
        ORDER BY ts DESC LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return {
        "summary": dict(summary),
        "categories": [dict(row) for row in categories],
        "paths": [dict(row) for row in paths],
        "requests": [dict(row) for row in requests],
    }


def category_breakdown(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    vhost: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, vhost, None, exclude_self, status_class)
    sql = f"""
    SELECT category,
           COUNT(*) AS hits,
           COUNT(DISTINCT net) AS unique_nets,
           MAX(bot_name) AS bot_name
    FROM requests
    WHERE {where}
    GROUP BY category
    ORDER BY hits DESC
    """
    rows = conn.execute(sql, params).fetchall()
    result = [dict(row) for row in rows]
    result.sort(
        key=lambda r: CATEGORIES.index(r["category"]) if r["category"] in CATEGORIES else 99
    )
    return result


def bot_breakdown(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    vhost: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, vhost, None, exclude_self, status_class)
    sql = f"""
    SELECT COALESCE(bot_name, category) AS name,
           category,
           COUNT(*) AS hits,
           COUNT(DISTINCT net) AS unique_nets
    FROM requests
    WHERE {where} AND category NOT IN ('browser', 'empty')
    GROUP BY name, category
    ORDER BY hits DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (*params, limit)).fetchall()
    return [dict(row) for row in rows]


def vhost_breakdown(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, None, None, exclude_self, status_class)
    sql = f"""
    SELECT vhost,
           COUNT(*) AS hits,
           COUNT(DISTINCT net) AS unique_nets,
           SUM(CASE WHEN category = 'browser' THEN 1 ELSE 0 END) AS human_hits,
           SUM(CASE WHEN category = 'scanner' THEN 1 ELSE 0 END) AS scanner_hits
    FROM requests
    WHERE {where}
    GROUP BY vhost
    ORDER BY hits DESC
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def status_breakdown(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    vhost: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, vhost, None, exclude_self, status_class)
    sql = f"""
    SELECT status, COUNT(*) AS hits
    FROM requests
    WHERE {where}
    GROUP BY status
    ORDER BY hits DESC
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def referer_breakdown(
    conn: sqlite3.Connection,
    *,
    iso_week: str | None = None,
    vhost: str | None = None,
    exclude_self: bool = False,
    status_class: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    where, params = _filters(iso_week, vhost, None, exclude_self, status_class)
    sql = f"""
    SELECT referer, COUNT(*) AS hits
    FROM requests
    WHERE {where} AND referer NOT IN ('-', '')
    GROUP BY referer
    ORDER BY hits DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (*params, limit)).fetchall()
    return [dict(row) for row in rows]


def weeks_list(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT iso_week FROM requests ORDER BY iso_week").fetchall()
    return [row["iso_week"] for row in rows]


def vhosts_list(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT vhost FROM requests ORDER BY vhost").fetchall()
    return [row["vhost"] for row in rows]


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total_requests,
               COUNT(DISTINCT net) AS total_nets,
               MIN(day) AS first_day, MAX(day) AS last_day,
               COUNT(DISTINCT iso_week) AS weeks
        FROM requests
        """
    ).fetchone()
    return dict(row) if row else {}
