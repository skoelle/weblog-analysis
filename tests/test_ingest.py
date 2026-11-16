# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from pathlib import Path

from app import queries
from app.config import Config
from app.db import connect, init_db
from app.ingest import ingest

LINE_TMPL = (
    '{ip} - - [14/Nov/2026:00:00:02 +0100] "GET {path} HTTP/1.1" {status} 100 '
    '{vhost} "-" "{ua}" "-"'
)

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def make_config(tmp_path: Path) -> Config:
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [
        LINE_TMPL.format(
            ip="192.0.2.0", path="/shop/", status=200, vhost="www.example.org", ua=CHROME
        ),
        LINE_TMPL.format(
            ip="198.51.100.0", path="/shop/", status=200, vhost="www.example.org", ua=CHROME
        ),
        LINE_TMPL.format(
            ip="192.0.2.0", path="/.env", status=404, vhost="www.example.org", ua=CHROME
        ),
        LINE_TMPL.format(ip="203.0.113.0", path="/", status=200, vhost="example.net", ua=GOOGLEBOT),
        LINE_TMPL.format(ip="192.0.2.0", path="/", status=200, vhost="www.example.org", ua="-"),
    ]
    (logs / "access.log.1").write_text("\n".join(lines) + "\n")
    return Config(
        log_dir=str(logs),
        db_path=str(tmp_path / "test.sqlite"),
        history_url="",
        vhost_aliases={},
        self_networks=["192.0.2.0"],
    )


def test_ingest_and_queries(tmp_path: Path):
    cfg = make_config(tmp_path)
    stats = ingest(cfg, refresh_self=False)
    assert stats["parsed"] == 5
    assert stats["failed"] == 0

    conn = connect(cfg.db_path)
    init_db(conn)

    weeks = queries.week_overview(conn)
    assert len(weeks) == 1
    row = weeks[0]
    assert row["iso_week"] == "2026-W46"
    assert row["hits"] == 5
    assert row["unique_nets"] == 3
    assert row["human_hits"] == 2
    assert row["scanner_hits"] == 1
    assert row["status_4xx"] == 1

    cats = {c["category"]: c["hits"] for c in queries.category_breakdown(conn)}
    assert cats["browser"] == 2
    assert cats["scanner"] == 1
    assert cats["empty"] == 1
    assert cats["search_bot"] == 1

    vhosts = {v["vhost"]: v["hits"] for v in queries.vhost_breakdown(conn)}
    assert vhosts == {"example.org": 4, "example.net": 1}

    nets = queries.top_nets(conn)
    assert nets[0]["net"] == "192.0.2.0"
    assert nets[0]["hits"] == 3

    detail = queries.net_detail(conn, "192.0.2.0")
    assert detail["summary"]["hits"] == 3
    assert detail["summary"]["human_hits"] == 1

    conn.close()


def test_reingest_is_incremental(tmp_path: Path):
    cfg = make_config(tmp_path)
    first = ingest(cfg, refresh_self=False)
    assert first["files"] == 1

    second = ingest(cfg, refresh_self=False)
    assert second["files"] == 0
    assert second["skipped"] == 1

    conn = connect(cfg.db_path)
    count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 5
    conn.close()


def test_same_file_via_different_mount_path_is_not_duplicated(tmp_path: Path):
    """Reading logs from /logs (container) after a host-path ingest must not duplicate."""
    cfg = make_config(tmp_path)
    ingest(cfg, refresh_self=False)

    container_logs = tmp_path / "logs_mounted_elsewhere"
    container_logs.mkdir()
    (container_logs / "access.log.1").write_text((tmp_path / "logs" / "access.log.1").read_text())

    cfg2 = Config(
        log_dir=str(container_logs),
        db_path=cfg.db_path,
        history_url="",
        vhost_aliases={},
        self_networks=cfg.self_networks,
    )
    stats = ingest(cfg2, refresh_self=False)
    assert stats["files"] == 0
    assert stats["skipped"] == 1

    conn = connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 5
    conn.close()


def test_status_class_filter(tmp_path: Path):
    cfg = make_config(tmp_path)
    ingest(cfg, refresh_self=False)
    conn = connect(cfg.db_path)
    init_db(conn)

    weeks = queries.week_overview(conn, status_class="2xx")
    assert len(weeks) == 1
    row = weeks[0]
    assert row["hits"] == 4
    assert row["status_4xx"] == 0

    cats = {c["category"]: c["hits"] for c in queries.category_breakdown(conn, status_class="2xx")}
    assert cats == {"browser": 2, "search_bot": 1, "empty": 1}

    paths = queries.top_paths(conn, status_class="4xx")
    assert len(paths) == 1
    assert paths[0]["path"] == "/.env"

    nets = queries.top_nets(conn, status_class="4xx")
    assert [n["net"] for n in nets] == ["192.0.2.0"]

    detail = queries.net_detail(conn, "192.0.2.0", status_class="2xx")
    assert detail["summary"]["hits"] == 2

    assert queries.top_paths(conn, status_class="bogus")
    conn.close()


def test_status_class_validation(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import config as app_config
    from app.main import app

    monkeypatch.setattr(app_config, "db_path", str(tmp_path / "api.sqlite"))
    with TestClient(app) as client:
        response = client.get("/api/weeks", params={"status_class": "2xx"})
        assert response.status_code == 200
        bad = client.get("/api/weeks", params={"status_class": "nope"})
        assert bad.status_code == 422
