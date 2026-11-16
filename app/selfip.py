# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Resolve the user's own IP networks from the dyndns-updater history API."""

from __future__ import annotations

import ipaddress
import logging

import httpx

log = logging.getLogger(__name__)


def _to_network(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{addr}/24", strict=False).network_address)
    return str(ipaddress.ip_network(f"{addr}/48", strict=False).network_address)


def fetch_self_networks(history_url: str, timeout: float = 10.0) -> set[str]:
    """Fetch the IP history and return the set of local network keys (/24, /48)."""
    if not history_url:
        return set()
    try:
        response = httpx.get(history_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("could not read self-IP history from %s: %s", history_url, exc)
        return set()

    networks: set[str] = set()
    for entry in payload.get("entries", []):
        for key in ("new_ip", "old_ip"):
            value = entry.get(key)
            if not value:
                continue
            network = _to_network(value)
            if network:
                networks.add(network)
    return networks
