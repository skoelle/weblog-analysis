# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
import gzip
from pathlib import Path

import pytest

from app.parser import ParseError, network_key, normalize_vhost, parse_line

V4_LINE = (
    '192.0.2.0 - - [14/Nov/2026:00:00:02 +0100] "GET /shop/ HTTP/1.1" 200 '
    '24348 www.example.org "-" "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/148.0.7778.166 Mobile/15E148 Safari/604.1" "-"'
)

V6_LINE = (
    '2001:db8:abcd:: - - [14/Nov/2026:00:00:09 +0100] "HEAD / HTTP/1.1" 200 - '
    'www.example.net "https://www.example.net" "Mozilla/5.0 (compatible; UptimeRobot/2.0; '
    'http://www.uptimerobot.com/)" "-"'
)

ESCAPED_REFERER = (
    '198.51.100.0 - - [11/Nov/2026:21:36:59 +0100] "GET /docs/guide/ HTTP/1.1" 200 7897 '
    'www.example.org "https://www.google.com/\'.,\\"()abcd" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" "-"'
)


def test_parse_ipv4_line():
    item = parse_line(V4_LINE)
    assert item.ip == "192.0.2.0"
    assert item.net == "192.0.2.0"
    assert item.ip_version == "v4"
    assert item.vhost == "example.org"
    assert item.method == "GET"
    assert item.path == "/shop/"
    assert item.status == 200
    assert item.bytes == 24348
    assert item.iso_week == "2026-W46"


def test_parse_ipv6_line():
    item = parse_line(V6_LINE)
    assert item.ip == "2001:db8:abcd::"
    assert item.ip_version == "v6"
    assert item.net == "2001:db8:abcd::"
    assert item.bytes is None


def test_parse_escaped_referer():
    item = parse_line(ESCAPED_REFERER)
    assert item.path == "/docs/guide/"
    assert item.referer == "https://www.google.com/'.,\"()abcd"


def test_parse_rejects_garbage():
    with pytest.raises(ParseError):
        parse_line("not a log line")


def test_network_key_masks_full_ip():
    assert network_key("192.0.2.77")[0] == "192.0.2.0"
    assert network_key("2001:db8:abcd:1234::1")[0] == "2001:db8:abcd::"


def test_normalize_vhost_strips_www_and_port():
    assert normalize_vhost("www.example.org") == "example.org"
    assert normalize_vhost("WWW.Example.NET:443") == "example.net"
    assert normalize_vhost("example.org.") == "example.org"


def test_normalize_vhost_applies_configured_aliases():
    aliases = {"shop.example": "example.org"}
    assert normalize_vhost("shop.example", aliases) == "example.org"
    assert normalize_vhost("www.shop.example", aliases) == "example.org"
    assert normalize_vhost("other.example", aliases) == "other.example"


def test_parse_gzip(tmp_path: Path):
    from app.ingest import parse_file

    path = tmp_path / "access.log.1.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(V4_LINE + "\n" + V6_LINE + "\n")
    parsed, failed = parse_file(path)
    assert len(parsed) == 2
    assert failed == 0


def test_parse_counts_failures(tmp_path: Path):
    from app.ingest import parse_file

    path = tmp_path / "access.log"
    path.write_text(V4_LINE + "\nbroken line\n")
    parsed, failed = parse_file(path)
    assert len(parsed) == 1
    assert failed == 1


def test_timestamp_keeps_local_wall_clock():
    item = parse_line(V4_LINE)
    assert item.ts.startswith("2026-11-14 00:00:02")
    assert item.day == "2026-11-14"
