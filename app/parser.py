# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Parse Apache/nginx combined log lines with a vhost field.

Example line (the provider appends the vhost as field 11)::

    192.0.2.0 - - [14/Nov/2026:00:00:02 +0100] "GET /shop/ HTTP/1.1" 200
    24348 www.example.org "-" "Mozilla/5.0 ..." "-"

The client IP is anonymised by the provider: IPv4 ends in ``.0`` and IPv6 is
truncated to its /48 prefix.  We therefore keep both the raw IP and a network
key (``/24`` for IPv4, ``/48`` for IPv6) which is used for visitor counting.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_QUOTED = r'(?:[^"\\]|\\.)*'
LINE_RE = re.compile(
    rf"^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "
    rf'"(?P<method>\S+) (?P<path>\S+)(?: [^"]*)?" '
    rf"(?P<status>\d{{3}}) (?P<bytes>\S+) "
    rf"(?P<vhost>\S+) "
    rf'"(?P<referer>{_QUOTED})" '
    rf'"(?P<ua>{_QUOTED})"'
    rf"(?P<trailing>.*)$"
)

TS_RE = re.compile(
    r"^(?P<day>\d{2})/(?P<mon>[A-Za-z]{3})/(?P<year>\d{4}):"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}) "
    r"(?P<tz>[+-]\d{4})$"
)

# Extra aliases are configured per deployment via VHOST_ALIASES; the built-in
# behaviour is only stripping a leading ``www.``.
DEFAULT_ALIASES: dict[str, str] = {}


class ParseError(ValueError):
    """Raised when a log line cannot be parsed."""


@dataclass(frozen=True)
class ParsedLine:
    ts: str
    day: str
    iso_week: str
    ip: str
    net: str
    ip_version: str
    vhost_raw: str
    vhost: str
    method: str
    path: str
    status: int
    bytes: int | None
    referer: str
    ua: str


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _to_local_naive(parts: dict[str, str]) -> datetime:
    """Build the logged local wall-clock time as a naive datetime.

    Days and weeks are grouped by the server's local time, which is what the
    operator expects.  The zone offset is matched by the timestamp regex but
    deliberately not applied.
    """
    month = MONTHS.get(parts["mon"].lower())
    if month is None:
        raise ParseError(f"unknown month: {parts['mon']!r}")
    return datetime(
        int(parts["year"]),
        month,
        int(parts["day"]),
        int(parts["hour"]),
        int(parts["minute"]),
        int(parts["second"]),
    )


def network_key(ip: str) -> tuple[str, str]:
    """Return ``(network, ip_version)`` for visitor counting.

    The provider already masks IPv4 to ``/24`` and IPv6 to ``/48`` but we mask
    defensively in case a future log contains full addresses.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip, "unknown"
    if addr.version == 4:
        network = ipaddress.ip_network(f"{addr}/24", strict=False)
        return str(network.network_address), "v4"
    network = ipaddress.ip_network(f"{addr}/48", strict=False)
    return str(network.network_address), "v6"


def normalize_vhost(vhost: str, aliases: dict[str, str] | None = None) -> str:
    value = vhost.strip().lower().rstrip(".")
    if ":" in value:
        value = value.rsplit(":", 1)[0]
    if value.startswith("www."):
        value = value[4:]
    mapping = {**DEFAULT_ALIASES, **aliases} if aliases else DEFAULT_ALIASES
    return mapping.get(value, value)


def parse_line(line: str, aliases: dict[str, str] | None = None) -> ParsedLine:
    match = LINE_RE.match(line.rstrip("\n"))
    if not match:
        raise ParseError(f"line does not match combined format: {line[:120]!r}")
    groups = match.groupdict()

    ts_match = TS_RE.match(groups["ts"].strip())
    if not ts_match:
        raise ParseError(f"unrecognised timestamp: {groups['ts']!r}")
    local_dt = _to_local_naive(ts_match.groupdict())

    try:
        status = int(groups["status"])
    except ValueError as exc:
        raise ParseError(f"bad status: {groups['status']!r}") from exc
    bytes_raw = groups["bytes"]
    size = None if bytes_raw in {"-", ""} else int(bytes_raw)

    ip = groups["ip"]
    net, version = network_key(ip)
    iso = local_dt.isocalendar()

    return ParsedLine(
        ts=local_dt.isoformat(sep=" "),
        day=local_dt.strftime("%Y-%m-%d"),
        iso_week=f"{iso[0]}-W{iso[1]:02d}",
        ip=ip,
        net=net,
        ip_version=version,
        vhost_raw=groups["vhost"],
        vhost=normalize_vhost(groups["vhost"], aliases),
        method=groups["method"].upper(),
        path=groups["path"],
        status=status,
        bytes=size,
        referer=_unescape(groups["referer"]),
        ua=_unescape(groups["ua"]),
    )
