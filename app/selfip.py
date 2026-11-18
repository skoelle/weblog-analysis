# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Resolve the user's own IP history from the dyndns-updater history API.

The API returns one entry per IP change with a local timestamp. The wall-clock
time is kept (the zone offset is dropped) because the parsed access logs also
store the server's local wall-clock time. That makes the entries comparable to
``requests.ts`` without applying a second timezone conversion.
"""

from __future__ import annotations

import ipaddress
import logging
import sqlite3
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryEntry:
    ts: str
    ip: str
    net: str
    trigger: str | None = None


@dataclass(frozen=True)
class SelfInterval:
    net: str
    start: str
    end: str | None


def _to_network(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{addr}/24", strict=False).network_address)
    return str(ipaddress.ip_network(f"{addr}/48", strict=False).network_address)


def _local_wall_clock(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None, microsecond=0).isoformat(sep=" ")


def fetch_self_history(history_url: str, timeout: float = 10.0) -> list[HistoryEntry]:
    """Fetch the IP history and return entries sorted by local wall-clock time."""
    if not history_url:
        return []
    try:
        response = httpx.get(history_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("could not read self-IP history from %s: %s", history_url, exc)
        return []

    entries: dict[str, HistoryEntry] = {}
    for item in payload.get("entries", []):
        ip = item.get("new_ip")
        raw_ts = item.get("timestamp")
        if not ip or not raw_ts:
            continue
        net = _to_network(ip)
        ts = _local_wall_clock(str(raw_ts))
        if net is None or ts is None:
            continue
        entries[ts] = HistoryEntry(ts=ts, ip=ip, net=net, trigger=item.get("trigger"))
    return [entries[ts] for ts in sorted(entries)]


def build_intervals(entries: Sequence[HistoryEntry]) -> list[SelfInterval]:
    """Turn history entries into active intervals, merging adjacent same nets."""
    ordered = sorted(entries, key=lambda entry: entry.ts)
    intervals: list[SelfInterval] = []
    for index, entry in enumerate(ordered):
        end = ordered[index + 1].ts if index + 1 < len(ordered) else None
        if intervals and intervals[-1].net == entry.net and intervals[-1].end == entry.ts:
            previous = intervals[-1]
            intervals[-1] = SelfInterval(previous.net, previous.start, end)
        else:
            intervals.append(SelfInterval(net=entry.net, start=entry.ts, end=end))
    return intervals


class SelfMatcher:
    """Decide whether a network was the user's own at a given wall-clock time."""

    def __init__(
        self,
        static_nets: Sequence[str] = (),
        intervals: Sequence[SelfInterval] = (),
        fallback_nets: Sequence[str] = (),
    ) -> None:
        self.static_nets = set(static_nets)
        self.intervals = sorted(intervals, key=lambda interval: interval.start)
        self.fallback_nets = set(fallback_nets)
        self._starts = [interval.start for interval in self.intervals]

    @property
    def known_nets(self) -> set[str]:
        nets = set(self.static_nets) | self.fallback_nets
        nets.update(interval.net for interval in self.intervals)
        return nets

    def __call__(self, net: str, ts: str) -> bool:
        if net in self.static_nets:
            return True
        if not self.intervals:
            return net in self.fallback_nets
        index = bisect_right(self._starts, ts) - 1
        if index < 0:
            return False
        interval = self.intervals[index]
        if interval.end is not None and ts >= interval.end:
            return False
        return interval.net == net


def store_history(conn: sqlite3.Connection, entries: Sequence[HistoryEntry]) -> int:
    if not entries:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO self_ip_history(ts, ip, net, trigger) VALUES(?, ?, ?, ?)",
        [(entry.ts, entry.ip, entry.net, entry.trigger) for entry in entries],
    )
    return len(entries)


def load_history(conn: sqlite3.Connection) -> list[HistoryEntry]:
    rows = conn.execute("SELECT ts, ip, net, trigger FROM self_ip_history ORDER BY ts").fetchall()
    return [
        HistoryEntry(ts=row["ts"], ip=row["ip"], net=row["net"], trigger=row["trigger"])
        for row in rows
    ]


def legacy_networks(conn: sqlite3.Connection) -> set[str]:
    """Networks from older versions; only used until timestamps are available."""
    nets = {row["net"] for row in conn.execute("SELECT net FROM self_networks")}
    for row in conn.execute("SELECT value FROM meta WHERE key = 'self_net'"):
        if row["value"]:
            nets.add(row["value"])
    return nets
