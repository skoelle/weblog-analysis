# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from pathlib import Path

import pytest

from app.config import Config
from app.fetch import (
    MANIFEST_NAME,
    FetchError,
    RemoteFile,
    fetch_and_ingest,
    fetch_logs,
    read_manifest,
)
from app.ingest import ingest

LINE = (
    '192.0.2.0 - - [14/Nov/2026:00:00:02 +0100] "GET /shop/ HTTP/1.1" 200 100 '
    'www.example.org "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36" "-"'
)


class FakeClient:
    def __init__(self, files: dict[str, tuple[bytes, int]]) -> None:
        self.files = files
        self.downloads: list[str] = []

    def list_files(self, remote_dir: str):
        for name, (_, mtime) in sorted(self.files.items()):
            yield RemoteFile(name=name, size=len(self.files[name][0]), mtime=mtime)

    def download(self, remote_dir: str, name: str, target: Path) -> None:
        self.downloads.append(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[name][0])


def make_config(tmp_path: Path, retention_days: int = 0) -> Config:
    logs = tmp_path / "logs"
    return Config(
        log_dir=str(logs),
        db_path=str(tmp_path / "db.sqlite"),
        file_retention_days=retention_days,
    )


def test_downloads_every_access_log_file(tmp_path: Path):
    cfg = make_config(tmp_path)
    payload = (LINE + "\n").encode()
    client = FakeClient(
        {
            "access.log.46.7.gz": (payload, 1000),
            "access.log.47.1": (payload, 2000),
        }
    )

    stats = fetch_logs(cfg, client=client)
    assert stats["downloaded"] == 2
    assert sorted(client.downloads) == ["access.log.46.7.gz", "access.log.47.1"]
    assert (Path(cfg.log_dir) / "access.log.47.1").exists()


def test_unchanged_files_are_skipped_via_manifest(tmp_path: Path):
    cfg = make_config(tmp_path)
    client = FakeClient({"access.log.47.1": ((LINE + "\n").encode(), 2000)})

    assert fetch_logs(cfg, client=client)["downloaded"] == 1
    assert fetch_logs(cfg, client=client)["skipped"] == 1
    assert client.downloads == ["access.log.47.1"]


def test_changed_file_is_downloaded_again(tmp_path: Path):
    cfg = make_config(tmp_path)
    client = FakeClient({"access.log.47.1": ((LINE + "\n").encode(), 2000)})
    fetch_logs(cfg, client=client)

    client.files["access.log.47.1"] = ((LINE + "\n" + LINE + "\n").encode(), 3000)
    assert fetch_logs(cfg, client=client)["downloaded"] == 1
    assert client.downloads == ["access.log.47.1", "access.log.47.1"]


def test_force_downloads_unchanged_files(tmp_path: Path):
    cfg = make_config(tmp_path)
    client = FakeClient({"access.log.47.1": ((LINE + "\n").encode(), 2000)})
    fetch_logs(cfg, client=client)

    assert fetch_logs(cfg, client=client, force=True)["downloaded"] == 1


def test_dry_run_writes_nothing(tmp_path: Path):
    cfg = make_config(tmp_path)
    client = FakeClient({"access.log.47.1": ((LINE + "\n").encode(), 2000)})

    stats = fetch_logs(cfg, client=client, dry_run=True)
    assert stats["files"] == 1
    assert stats["downloaded"] == 0
    assert client.downloads == []
    assert not (tmp_path / "logs").exists() or not any((tmp_path / "logs").iterdir())
    assert not (tmp_path / MANIFEST_NAME).exists()


def test_first_run_adopts_local_file_of_same_size(tmp_path: Path):
    cfg = make_config(tmp_path)
    logs = Path(cfg.log_dir)
    logs.mkdir()
    payload = (LINE + "\n").encode()
    (logs / "access.log.47.1").write_bytes(payload)

    client = FakeClient({"access.log.47.1": (payload, 2000)})
    stats = fetch_logs(cfg, client=client)
    assert stats["skipped"] == 1
    assert client.downloads == []


def test_manifest_records_all_remote_files(tmp_path: Path):
    cfg = make_config(tmp_path)
    client = FakeClient(
        {
            "access.log.46.7.gz": (b"a\n", 1000),
            "access.log.47.1": (b"b\n", 2000),
        }
    )
    fetch_logs(cfg, client=client)

    manifest = read_manifest(tmp_path / MANIFEST_NAME)
    assert set(manifest) == {"access.log.46.7.gz", "access.log.47.1"}
    assert manifest["access.log.47.1"] == "2:2000"


def test_download_failure_is_wrapped(tmp_path: Path):
    cfg = make_config(tmp_path)

    class Broken(FakeClient):
        def download(self, remote_dir: str, name: str, target: Path) -> None:
            raise OSError("connection reset")

    client = Broken({"access.log.47.1": (b"x\n", 1)})
    with pytest.raises(FetchError, match=r"access\.log\.47\.1"):
        fetch_logs(cfg, client=client)


def test_fetch_then_ingest_overlapping_files_counts_once(tmp_path: Path):
    """Bundle and daily file both arrive; ingest deduplicates by line."""
    cfg = make_config(tmp_path)
    day = (LINE + "\n").encode()
    client = FakeClient(
        {
            "access.log.45.1": (day, 1000),
            "access.log.45.2": (day, 1001),
            "access.log.45": (day + day, 1002),
        }
    )

    fetch_logs(cfg, client=client)
    ingest(cfg, refresh_self=False)

    from app.db import connect

    conn = connect(cfg.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
    finally:
        conn.close()


def test_fetch_and_ingest_runs_both_steps(tmp_path: Path, monkeypatch):
    import app.fetch as fetch_module

    cfg = make_config(tmp_path)
    monkeypatch.setattr(
        fetch_module,
        "fetch_logs",
        lambda *a, **kw: {"files": 1, "skipped": 0, "downloaded": 1},
    )
    result = fetch_and_ingest(cfg, run_ingest=True)
    assert result["downloaded"] == 1
    assert isinstance(result["ingest"], dict)


def test_fetch_and_ingest_can_skip_ingest(tmp_path: Path, monkeypatch):
    import app.fetch as fetch_module

    cfg = make_config(tmp_path)
    monkeypatch.setattr(
        fetch_module,
        "fetch_logs",
        lambda *a, **kw: {"files": 0, "skipped": 2, "downloaded": 0},
    )
    result = fetch_and_ingest(cfg, run_ingest=False)
    assert "ingest" not in result


def test_host_key_verification_requires_configuration(tmp_path: Path, monkeypatch):
    """Without SFTP_HOST_KEY or SFTP_KNOWN_HOSTS the client must refuse."""
    import paramiko

    from app.fetch import ParamikoClient

    class FakeKey:
        def asbytes(self):
            return b"test-key"

        def get_name(self):
            return "ssh-ed25519"

    class FakeTransport:
        def __init__(self, addr):
            self.addr = addr

        def start_client(self, timeout=None):
            return None

        def get_remote_server_key(self):
            return FakeKey()

        def close(self):
            return None

    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    cfg = Config(
        sftp_host="sftp.example.org",
        sftp_user="user",
        sftp_pass="pass",
    )
    client = ParamikoClient(cfg)
    with pytest.raises(FetchError, match="Host-Key"):
        client._connect()


def test_host_key_mismatch_is_rejected(tmp_path: Path, monkeypatch):
    import paramiko

    from app.fetch import ParamikoClient

    class FakeKey:
        def asbytes(self):
            return b"test-key"

        def get_name(self):
            return "ssh-ed25519"

    class FakeTransport:
        def __init__(self, addr):
            pass

        def start_client(self, timeout=None):
            return None

        def get_remote_server_key(self):
            return FakeKey()

        def close(self):
            return None

    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    cfg = Config(
        sftp_host="sftp.example.org",
        sftp_user="user",
        sftp_pass="pass",
        sftp_host_key="SHA256:wrongfingerprint",
    )
    with pytest.raises(FetchError, match="passt nicht"):
        ParamikoClient(cfg)._connect()


def test_host_key_match_allows_connect(tmp_path: Path, monkeypatch):
    import paramiko

    from app.fetch import ParamikoClient, _b64_fingerprint

    class FakeKey:
        def asbytes(self):
            return b"test-key"

        def get_name(self):
            return "ssh-ed25519"

    class FakeSftp:
        def __init__(self, transport):
            self.transport = transport

        def close(self):
            return None

    class FakeTransport:
        def __init__(self, addr):
            self.authed = False

        def start_client(self, timeout=None):
            return None

        def get_remote_server_key(self):
            return FakeKey()

        def auth_password(self, user, password):
            self.authed = True

        def close(self):
            return None

    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    monkeypatch.setattr(
        paramiko,
        "SFTPClient",
        type("S", (), {"from_transport": staticmethod(lambda t: FakeSftp(t))}),
    )

    fingerprint = "SHA256:" + _b64_fingerprint(FakeKey())
    cfg = Config(
        sftp_host="sftp.example.org",
        sftp_user="user",
        sftp_pass="pass",
        sftp_host_key=fingerprint,
    )
    client = ParamikoClient(cfg)
    client._connect()
    assert client._transport is not None
    client.close()


def test_downloaded_files_are_world_readable(tmp_path: Path):
    """The data volume is shared with the host, so 0600 from mkstemp is wrong."""
    cfg = make_config(tmp_path)
    client = FakeClient({"access.log.47.1": ((LINE + "\n").encode(), 2000)})
    fetch_logs(cfg, client=client)

    target = Path(cfg.log_dir) / "access.log.47.1"
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    assert oct((tmp_path / MANIFEST_NAME).stat().st_mode & 0o777) == "0o644"


def test_retention_skips_old_remote_files(tmp_path: Path):
    """Pruned files must not be downloaded again."""
    import time

    cfg = make_config(tmp_path, retention_days=30)
    now = int(time.time())
    old = now - 90 * 86400
    payload = (LINE + "\n").encode()

    client = FakeClient(
        {
            "access.log.30.gz": (payload, old),
            "access.log.47.1": (payload, now),
        }
    )
    stats = fetch_logs(cfg, client=client)

    assert stats["retired"] == 1
    assert stats["downloaded"] == 1
    assert client.downloads == ["access.log.47.1"]
    assert not (Path(cfg.log_dir) / "access.log.30.gz").exists()


def test_force_does_not_override_retention(tmp_path: Path):
    import time

    cfg = make_config(tmp_path, retention_days=30)
    old = int(time.time()) - 90 * 86400
    client = FakeClient({"access.log.30.gz": ((LINE + "\n").encode(), old)})

    stats = fetch_logs(cfg, client=client, force=True)
    assert stats["retired"] == 1
    assert stats["downloaded"] == 0
    assert client.downloads == []


def test_retired_files_are_recorded_in_manifest(tmp_path: Path):
    import time

    cfg = make_config(tmp_path, retention_days=30)
    old = int(time.time()) - 90 * 86400
    client = FakeClient(
        {"access.log.30.gz": (b"x\n", old), "access.log.47.1": (b"y\n", int(time.time()))}
    )

    fetch_logs(cfg, client=client)
    manifest = read_manifest(tmp_path / MANIFEST_NAME)
    assert set(manifest) == {"access.log.30.gz", "access.log.47.1"}
