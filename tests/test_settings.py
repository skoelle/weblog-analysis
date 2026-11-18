# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Tests for the read-only settings information endpoint."""

from pathlib import Path

from app.config import Config
from app.info import storage_info


def test_storage_info_falls_back_to_nearest_existing_parent(tmp_path: Path):
    cfg = Config(
        log_dir=str(tmp_path / "missing" / "logs"),
        db_path=str(tmp_path / "db.sqlite"),
    )

    entries = storage_info(cfg)

    assert entries[0]["exists"] is False
    assert entries[0]["free"] is not None
    assert entries[0]["total"] is not None


def test_settings_endpoint_reports_config_without_secrets(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import config as app_config
    from app.main import app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(app_config, "db_path", str(tmp_path / "data" / "weblog.sqlite"))
    monkeypatch.setattr(app_config, "log_dir", str(log_dir))
    monkeypatch.setattr(app_config, "sftp_host", "sftp.example.org")
    monkeypatch.setattr(app_config, "sftp_port", 2222)
    monkeypatch.setattr(app_config, "sftp_user", "logs")
    monkeypatch.setattr(app_config, "sftp_pass", "super-secret-password")
    monkeypatch.setattr(app_config, "sftp_host_key", "SHA256:secretfingerprint")
    monkeypatch.setattr(app_config, "sftp_known_hosts", "")
    monkeypatch.setattr(app_config, "sftp_remote_dir", "/remote/logs")
    monkeypatch.setattr(app_config, "self_networks", [])
    monkeypatch.setattr(app_config, "history_url", "")

    with TestClient(app) as client:
        response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime"]["log_dir"] == str(log_dir)
    assert body["fetch"]["configured"] is True
    assert body["fetch"]["host"] == "sftp.example.org"
    assert body["fetch"]["user"] == "logs"
    assert body["fetch"]["remote_dir"] == "/remote/logs"
    assert body["fetch"]["host_key_mode"] == "pinned"
    assert body["data"]["total_requests"] == 0
    assert body["data"]["self_network_list"] == []
    assert body["data"]["self_network_count"] == 0
    assert body["data"]["history_configured"] is False
    assert body["data"]["history_host"] is None
    assert len(body["storage"]) == 1
    assert "Logs" in body["storage"][0]["labels"]
    assert "Database" in body["storage"][0]["labels"]

    assert "super-secret-password" not in response.text
    assert "secretfingerprint" not in response.text


def test_settings_lists_own_networks_with_sources(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import config as app_config
    from app.db import connect, init_db
    from app.main import app

    db_path = tmp_path / "data" / "weblog.sqlite"
    monkeypatch.setattr(app_config, "db_path", str(db_path))
    monkeypatch.setattr(app_config, "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(app_config, "self_networks", ["192.0.2.0", "203.0.113.0"])
    monkeypatch.setattr(
        app_config,
        "history_url",
        "https://user:secret@dyndns.example.org/api/history?token=abc",
    )

    conn = connect(db_path)
    init_db(conn)
    conn.executemany(
        "INSERT INTO self_ip_history(ts, ip, net, trigger) VALUES(?, ?, ?, ?)",
        [
            ("2026-10-01 00:00:00", "198.51.100.7", "198.51.100.0", "webhook"),
            ("2026-11-01 00:00:00", "203.0.113.9", "203.0.113.0", "poll"),
        ],
    )
    conn.commit()
    conn.close()

    with TestClient(app) as client:
        response = client.get("/api/settings")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["self_network_list"] == [
        {"net": "192.0.2.0", "sources": ["config"], "ranges": []},
        {
            "net": "198.51.100.0",
            "sources": ["history"],
            "ranges": [{"start": "2026-10-01 00:00:00", "end": "2026-11-01 00:00:00"}],
        },
        {
            "net": "203.0.113.0",
            "sources": ["config", "history"],
            "ranges": [{"start": "2026-11-01 00:00:00", "end": None}],
        },
    ]
    assert data["self_network_count"] == 3
    assert data["self_history"] == [
        {
            "ts": "2026-10-01 00:00:00",
            "ip": "198.51.100.7",
            "net": "198.51.100.0",
            "trigger": "webhook",
            "current": False,
        },
        {
            "ts": "2026-11-01 00:00:00",
            "ip": "203.0.113.9",
            "net": "203.0.113.0",
            "trigger": "poll",
            "current": True,
        },
    ]
    assert data["history_configured"] is True
    assert data["history_host"] == "dyndns.example.org"
    assert data["self_hits"] == 0
    assert data["self_nets_observed"] == 0

    assert "secret" not in response.text
    assert "token" not in response.text


def test_legacy_self_net_meta_is_migrated(tmp_path: Path):
    from app.db import connect, init_db

    db_path = tmp_path / "legacy.sqlite"
    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('self_net', ?)",
        ("198.51.100.0",),
    )
    conn.commit()

    init_db(conn)
    networks = {row["net"] for row in conn.execute("SELECT net FROM self_networks")}
    conn.close()

    assert networks == {"198.51.100.0"}


def test_cached_legacy_networks_are_shown_without_history(tmp_path: Path):
    from app.config import Config
    from app.db import connect, init_db
    from app.info import settings_info

    db_path = tmp_path / "cached.sqlite"
    conn = connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO self_networks(net) VALUES('198.51.100.0')")
    conn.commit()
    conn.close()

    cfg = Config(
        log_dir=str(tmp_path / "logs"),
        db_path=str(db_path),
        history_url="",
        self_networks=[],
    )

    data = settings_info(cfg)["data"]

    assert data["self_network_list"] == [
        {"net": "198.51.100.0", "sources": ["cached"], "ranges": []}
    ]
    assert data["self_history"] == []
