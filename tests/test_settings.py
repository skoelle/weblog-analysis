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
    assert len(body["storage"]) == 1
    assert "Logs" in body["storage"][0]["labels"]
    assert "Database" in body["storage"][0]["labels"]

    assert "super-secret-password" not in response.text
    assert "secretfingerprint" not in response.text
