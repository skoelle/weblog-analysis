# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Download access logs from an SFTP server.

Every remote ``access.log*`` file is fetched; already imported lines are
dropped later by the ingest step, which compares line signatures. The manifest
only prevents downloading files that did not change since the last run.

Host keys are verified strictly: either ``SFTP_HOST_KEY`` holds the expected
fingerprint (``SHA256:...``) or ``SFTP_KNOWN_HOSTS`` points at a known_hosts
file. Without either the fetch refuses to connect.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import stat
import tempfile
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from .config import Config
from .config import config as default_config
from .db import connect
from .ingest import ingest
from .retention import cutoff_for, prune_log_files

log = logging.getLogger(__name__)

MANIFEST_NAME = ".fetch-manifest.tsv"
REMOTE_PREFIX = "access.log"
FETCH_LOCK = threading.Lock()
LAST_FETCH_KEY = "last_fetch"


class FetchError(RuntimeError):
    """Raised when the remote catalogue cannot be read or a download fails."""


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int
    mtime: int

    @property
    def signature(self) -> str:
        return f"{self.size}:{self.mtime}"


class SftpClient(Protocol):
    """Minimal SFTP surface, so tests can inject a fake client."""

    def list_files(self, remote_dir: str) -> Iterable[RemoteFile]:
        """Return the remote entries worth considering (regular files only)."""

    def download(self, remote_dir: str, name: str, target: Path) -> None:
        """Download ``name`` into ``target``."""


class ParamikoClient:
    """SFTP client backed by paramiko with strict host key handling."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._transport = None
        self._sftp = None

    def __enter__(self) -> ParamikoClient:
        self._connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _connect(self) -> None:
        import paramiko

        if not self.cfg.sftp_host:
            raise FetchError("SFTP_HOST ist nicht gesetzt")
        if not self.cfg.sftp_user:
            raise FetchError("SFTP_USER ist nicht gesetzt")
        if not self.cfg.sftp_pass:
            raise FetchError("SFTP_PASS ist nicht gesetzt")

        transport = paramiko.Transport((self.cfg.sftp_host, self.cfg.sftp_port))
        try:
            transport.start_client(timeout=30)
            self._verify_host_key(transport)
            transport.auth_password(self.cfg.sftp_user, self.cfg.sftp_pass)
            self._transport = transport
            self._sftp = paramiko.SFTPClient.from_transport(transport)
        except BaseException:
            transport.close()
            raise

    def _verify_host_key(self, transport) -> None:
        import paramiko

        key = transport.get_remote_server_key()
        fingerprint = "SHA256:" + _b64_fingerprint(key)

        if self.cfg.sftp_host_key:
            if fingerprint != self.cfg.sftp_host_key.strip():
                raise FetchError(
                    f"Host-Key von {self.cfg.sftp_host} passt nicht zu SFTP_HOST_KEY "
                    f"(erwartet {self.cfg.sftp_host_key}, erhalten {fingerprint})"
                )
            return

        if self.cfg.sftp_known_hosts:
            host_keys = paramiko.HostKeys(filename=self.cfg.sftp_known_hosts)
            entry = host_keys.lookup(self.cfg.sftp_host)
            if not entry:
                raise FetchError(f"{self.cfg.sftp_host} fehlt in {self.cfg.sftp_known_hosts}")
            if key.get_name() not in entry:
                raise FetchError(
                    f"{self.cfg.sftp_host}: kein {key.get_name()}-Key in "
                    f"{self.cfg.sftp_known_hosts}"
                )
            if entry[key.get_name()] != key:
                raise FetchError(
                    f"Host-Key von {self.cfg.sftp_host} passt nicht zu {self.cfg.sftp_known_hosts}"
                )
            return

        raise FetchError(
            "Host-Key-Prüfung nicht konfiguriert: SFTP_HOST_KEY (Fingerprint "
            f"{fingerprint}) oder SFTP_KNOWN_HOSTS setzen"
        )

    def list_files(self, remote_dir: str) -> Iterator[RemoteFile]:
        assert self._sftp is not None
        try:
            entries = self._sftp.listdir_attr(remote_dir)
        except OSError as exc:
            raise FetchError(f"SFTP-Listing von {remote_dir} fehlgeschlagen: {exc}") from exc
        for entry in entries:
            if not stat.S_ISREG(entry.st_mode):
                continue
            if not entry.filename.startswith(REMOTE_PREFIX):
                continue
            yield RemoteFile(
                name=entry.filename,
                size=int(entry.st_size or 0),
                mtime=int(entry.st_mtime or 0),
            )

    def download(self, remote_dir: str, name: str, target: Path) -> None:
        assert self._sftp is not None
        remote = f"{remote_dir.rstrip('/')}/{name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".fetch.")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                self._sftp.getfo(remote, handle)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def _b64_fingerprint(key) -> str:
    import base64
    import hashlib

    digest = hashlib.sha256(key.asbytes()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        name, _, signature = line.partition("\t")
        if name:
            entries[name] = signature
    return entries


def write_manifest(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".manifest.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            for name in sorted(entries):
                handle.write(f"{name}\t{entries[name]}\n")
        tmp.chmod(0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def manifest_path(cfg: Config) -> Path:
    return Path(cfg.db_path).parent / MANIFEST_NAME


def _download_missing(
    cfg: Config,
    client: SftpClient,
    target_dir: Path,
    known: dict[str, str],
    *,
    dry_run: bool,
    force: bool,
) -> tuple[dict[str, int], dict[str, str]]:
    stats = {"files": 0, "skipped": 0, "downloaded": 0, "retired": 0}
    seen: dict[str, str] = {}
    cut = cutoff_for(cfg.file_retention_days)
    for entry in client.list_files(cfg.sftp_remote_dir):
        seen[entry.name] = entry.signature
        target = target_dir / entry.name
        # Retention also applies to the download, otherwise pruning a file
        # locally would simply make the next fetch bring it back.
        if cut is not None and _older_than(entry.mtime, cut):
            stats["retired"] += 1
            continue
        unchanged = known.get(entry.name) == entry.signature and target.exists()
        # First run without a manifest: adopt a local file of the same size
        # instead of downloading the whole backlog again.
        adopted = entry.name not in known and _same_size(target, entry.size)
        if not force and (unchanged or adopted):
            stats["skipped"] += 1
            continue
        stats["files"] += 1
        if dry_run:
            log.info("wuerde laden: %s (%d Bytes)", entry.name, entry.size)
            continue
        try:
            client.download(cfg.sftp_remote_dir, entry.name, target)
        except Exception as exc:
            raise FetchError(f"Download von {entry.name} fehlgeschlagen: {exc}") from exc
        # The data volume is shared with the host; mkstemp creates 0600.
        with contextlib.suppress(OSError):
            target.chmod(0o644)
        stats["downloaded"] += 1
        log.info("geladen: %s (%d Bytes)", entry.name, entry.size)
    return stats, seen


def fetch_logs(
    cfg: Config | None = None,
    *,
    client: SftpClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, int]:
    """Download new or changed log files into ``LOG_DIR``."""
    cfg = cfg or default_config
    target_dir = Path(cfg.log_dir)
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = manifest_path(cfg)
    known = read_manifest(manifest_file)

    with FETCH_LOCK:
        if client is not None:
            stats, seen = _download_missing(
                cfg, client, target_dir, known, dry_run=dry_run, force=force
            )
        else:
            with ParamikoClient(cfg) as sftp:
                stats, seen = _download_missing(
                    cfg, sftp, target_dir, known, dry_run=dry_run, force=force
                )

    if not dry_run:
        write_manifest(manifest_file, seen)
    return stats


def _older_than(mtime: int, cutoff: date) -> bool:
    """Compare a remote mtime (epoch seconds) against the retention cutoff."""
    from datetime import datetime

    if not mtime:
        return False
    remote_day = datetime.fromtimestamp(mtime).date()
    return remote_day < cutoff


def _same_size(target: Path, size: int) -> bool:
    try:
        return target.is_file() and target.stat().st_size == size
    except OSError:
        return False


def _record_fetch_status(
    cfg: Config,
    *,
    ok: bool,
    result: dict[str, int | dict[str, int]],
    error: str | None,
    force: bool,
) -> None:
    """Persist the outcome of a fetch run in ``meta`` for the dashboard."""
    fetch_part = {
        key: val
        for key, val in result.items()
        if key in {"files", "skipped", "downloaded", "retired"}
    }
    ingest_part = result.get("ingest")
    payload = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ok": ok,
        "error": error,
        "force": force,
        "fetch": fetch_part,
        "ingest": ingest_part if isinstance(ingest_part, dict) else {},
    }
    conn = connect(cfg.db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            (LAST_FETCH_KEY, json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()


def _record_fetch_status_safely(
    cfg: Config,
    *,
    ok: bool,
    result: dict[str, int | dict[str, int]],
    error: str | None,
    force: bool,
) -> None:
    try:
        _record_fetch_status(cfg, ok=ok, result=result, error=error, force=force)
    except Exception:
        log.warning("Fetch-Status konnte nicht gespeichert werden", exc_info=True)


def read_fetch_status(cfg: Config | None = None) -> dict | None:
    """Return the last persisted fetch status, or ``None`` if it never ran."""
    cfg = cfg or default_config
    conn = connect(cfg.db_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (LAST_FETCH_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        return json.loads(row["value"])
    return None


def fetch_and_ingest(
    cfg: Config | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
    run_ingest: bool = True,
) -> dict[str, int | dict[str, int]]:
    cfg = cfg or default_config
    result: dict[str, int | dict[str, int]] = {}
    try:
        result.update(fetch_logs(cfg, dry_run=dry_run, force=force))
        if run_ingest and not dry_run:
            result["ingest"] = ingest(cfg, refresh_self=False)
            # Prune only after a successful ingest: a file must be in the database
            # before its raw copy is removed.
            result["retention"] = prune_log_files(cfg)
    except Exception as exc:
        if not dry_run:
            _record_fetch_status_safely(cfg, ok=False, result=result, error=str(exc), force=force)
        raise
    if not dry_run:
        _record_fetch_status_safely(cfg, ok=True, result=result, error=None, force=force)
    return result


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch access logs over SFTP")
    parser.add_argument("--dry-run", action="store_true", help="only list what would be fetched")
    parser.add_argument("--force", action="store_true", help="download even if unchanged")
    parser.add_argument("--no-ingest", action="store_true", help="skip the ingest step")
    args = parser.parse_args()

    result = fetch_and_ingest(dry_run=args.dry_run, force=args.force, run_ingest=not args.no_ingest)
    fetched = (
        f"files={result['files']} skipped={result['skipped']} "
        f"downloaded={result['downloaded']} retired={result['retired']}"
    )
    retention = result.get("retention")
    if isinstance(retention, dict):
        fetched += f" pruned={retention['pruned']}"
    if isinstance(result.get("ingest"), dict):
        ing = result["ingest"]
        print(
            f"{fetched} inserted={ing['inserted']} duplicates={ing['duplicates']} "
            f"failed={ing['failed']} seconds={ing['seconds']}"
        )
    else:
        print(fetched)


if __name__ == "__main__":
    main()
