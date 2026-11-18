# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Tests for the timed dyndns history and the own-traffic matcher."""

from app.selfip import HistoryEntry, SelfMatcher, build_intervals, fetch_self_history


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_fetch_self_history_keeps_wall_clock_and_sorts(monkeypatch):
    payload = {
        "entries": [
            {
                "timestamp": "2026-11-05T10:47:39.386+01:00",
                "new_ip": "203.0.113.9",
                "trigger": "webhook",
            },
            {
                "timestamp": "2026-08-15T11:02:05.484+02:00",
                "new_ip": "192.0.2.7",
                "trigger": "startup",
            },
            {
                "timestamp": "2026-08-15T11:02:05.484+02:00",
                "new_ip": "192.0.2.9",
                "trigger": "duplicate",
            },
            {"timestamp": "not-a-time", "new_ip": "198.51.100.1", "trigger": "bad"},
            {"timestamp": "2026-08-16T00:00:00+02:00", "new_ip": "bad-ip", "trigger": "bad"},
        ]
    }
    monkeypatch.setattr("app.selfip.httpx.get", lambda *args, **kwargs: FakeResponse(payload))

    entries = fetch_self_history("https://history.example.org")

    assert [entry.ts for entry in entries] == [
        "2026-08-15 11:02:05",
        "2026-11-05 10:47:39",
    ]
    assert [entry.net for entry in entries] == ["192.0.2.0", "203.0.113.0"]
    assert entries[0].trigger == "duplicate"


def test_intervals_match_only_while_a_network_is_active():
    entries = [
        HistoryEntry("2026-08-15 11:02:05", "192.0.2.7", "192.0.2.0"),
        HistoryEntry("2026-11-05 10:47:39", "203.0.113.9", "203.0.113.0"),
    ]
    intervals = build_intervals(entries)
    matcher = SelfMatcher(intervals=intervals)

    assert intervals[0].end == "2026-11-05 10:47:39"
    assert intervals[1].end is None
    assert matcher("192.0.2.0", "2026-08-15 11:02:05")
    assert matcher("192.0.2.0", "2026-11-05 10:47:38")
    assert not matcher("192.0.2.0", "2026-11-05 10:47:39")
    assert matcher("203.0.113.0", "2026-11-05 10:47:39")
    assert matcher("203.0.113.0", "2027-01-01 00:00:00")
    assert not matcher("198.51.100.0", "2026-09-01 00:00:00")


def test_adjacent_intervals_of_the_same_network_are_merged():
    entries = [
        HistoryEntry("2026-08-15 11:02:05", "192.0.2.7", "192.0.2.0"),
        HistoryEntry("2026-08-17 03:01:02", "192.0.2.8", "192.0.2.0"),
        HistoryEntry("2026-08-20 03:01:02", "203.0.113.9", "203.0.113.0"),
    ]

    intervals = build_intervals(entries)

    assert len(intervals) == 2
    assert intervals[0].start == "2026-08-15 11:02:05"
    assert intervals[0].end == "2026-08-20 03:01:02"
    assert intervals[1].net == "203.0.113.0"


def test_static_and_fallback_networks():
    entries = [HistoryEntry("2026-08-15 11:02:05", "192.0.2.7", "192.0.2.0")]
    intervals = build_intervals(entries)

    static = SelfMatcher(["198.51.100.0"], intervals)
    assert static("198.51.100.0", "1999-01-01 00:00:00")

    fallback = SelfMatcher(fallback_nets=["198.51.100.0"])
    assert fallback("198.51.100.0", "1999-01-01 00:00:00")

    timed = SelfMatcher(intervals=intervals, fallback_nets=["198.51.100.0"])
    assert not timed("198.51.100.0", "2026-09-01 00:00:00")
