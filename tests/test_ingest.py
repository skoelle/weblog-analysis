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


def test_week_overview_vhost_and_category_filter(tmp_path: Path):
    cfg = make_config(tmp_path)
    ingest(cfg, refresh_self=False)
    conn = connect(cfg.db_path)
    init_db(conn)

    by_vhost = queries.week_overview(conn, vhost="example.net")
    assert len(by_vhost) == 1
    assert by_vhost[0]["hits"] == 1

    by_category = queries.week_overview(conn, category="browser")
    assert len(by_category) == 1
    assert by_category[0]["hits"] == 2
    assert by_category[0]["human_hits"] == 2

    combined = queries.week_overview(conn, vhost="example.org", category="browser")
    assert combined[0]["hits"] <= 4
    conn.close()


def test_status_class_validation(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import config as app_config
    from app.main import app

    monkeypatch.setattr(app_config, "db_path", str(tmp_path / "api.sqlite"))
    with TestClient(app) as client:
        response = client.get("/api/weeks", params={"status_class": "2xx"})
        assert response.status_code == 200
        filtered = client.get("/api/weeks", params={"vhost": "example.org", "category": "browser"})
        assert filtered.status_code == 200
        bad = client.get("/api/weeks", params={"status_class": "nope"})
        assert bad.status_code == 422


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _line(ip: str, path: str, status: int = 200) -> str:
    return LINE_TMPL.format(ip=ip, path=path, status=status, vhost="www.example.org", ua=CHROME)


def _count(cfg: Config) -> int:
    conn = connect(cfg.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    finally:
        conn.close()


def test_weekly_bundle_and_daily_files_do_not_double_count(tmp_path: Path):
    """A bundle and its split daily files contain the same lines."""
    logs = tmp_path / "logs"
    logs.mkdir()
    day1 = [_line(f"192.0.2.{i}", "/shop/") for i in range(3)]
    day2 = [_line(f"198.51.100.{i}", "/docs/") for i in range(2)]
    _write(logs / "access.log.45.1", day1)
    _write(logs / "access.log.45.2", day2)
    _write(logs / "access.log.45", day1 + day2)

    cfg = Config(log_dir=str(logs), db_path=str(tmp_path / "db.sqlite"))
    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 5

    # Ingest again in a different file order: numbers must stay stable.
    (logs / "access.log.45").unlink()
    _write(logs / "access.log.45", day1 + day2)
    stats = ingest(cfg, refresh_self=False)
    assert _count(cfg) == 5
    assert stats["inserted"] == 0


def test_partial_file_replaced_by_complete_file(tmp_path: Path):
    """A truncated download later replaced by the full file must not duplicate."""
    logs = tmp_path / "logs"
    logs.mkdir()
    full = [_line(f"192.0.2.{i}", "/shop/") for i in range(4)]
    _write(logs / "access.log.46.6", full[:2])

    cfg = Config(log_dir=str(logs), db_path=str(tmp_path / "db.sqlite"))
    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 2

    _write(logs / "access.log.46.6", full)
    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 4


def test_identical_lines_inside_one_file_are_kept(tmp_path: Path):
    """Scan traffic can repeat the exact same line; those are distinct requests."""
    logs = tmp_path / "logs"
    logs.mkdir()
    repeated = _line("192.0.2.0", "/admin", 404)
    _write(logs / "access.log.1", [repeated] * 5)

    cfg = Config(log_dir=str(logs), db_path=str(tmp_path / "db.sqlite"))
    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 5

    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 5


def test_bundle_reread_keeps_daily_rows(tmp_path: Path):
    """Re-reading the bundle must not delete rows contributed by daily files."""
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [_line(f"192.0.2.{i}", "/shop/") for i in range(3)]
    _write(logs / "access.log.45.1", lines)
    _write(logs / "access.log.45.2", lines)
    _write(logs / "access.log.45", lines + lines)

    cfg = Config(log_dir=str(logs), db_path=str(tmp_path / "db.sqlite"))
    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 6

    # Force a re-read of the bundle only.
    conn = connect(cfg.db_path)
    conn.execute("DELETE FROM ingested_files WHERE path = 'access.log.45'")
    conn.commit()
    conn.close()

    ingest(cfg, refresh_self=False)
    assert _count(cfg) == 6


def test_signature_migration_backfills_existing_rows(tmp_path: Path):
    """A database from before the sig column gets migrated without data loss."""
    import sqlite3

    from app.db import SIG_FIELDS, init_db, line_signature

    db_path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE ingested_files (
            path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL,
            lines INTEGER NOT NULL, parsed INTEGER NOT NULL, failed INTEGER NOT NULL,
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
            iso_week TEXT NOT NULL, ip TEXT NOT NULL, net TEXT NOT NULL,
            ip_version TEXT NOT NULL, vhost_raw TEXT NOT NULL, vhost TEXT NOT NULL,
            method TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL,
            bytes INTEGER, referer TEXT NOT NULL, ua TEXT NOT NULL,
            category TEXT NOT NULL, bot_name TEXT, is_scanner_path INTEGER NOT NULL DEFAULT 0,
            is_self INTEGER NOT NULL DEFAULT 0, source_file TEXT NOT NULL DEFAULT '',
            country_code TEXT, region TEXT, city TEXT, asn TEXT
        );
        """
    )
    row = {
        "ts": "2026-11-14 00:00:02",
        "day": "2026-11-14",
        "iso_week": "2026-W46",
        "ip": "192.0.2.0",
        "net": "192.0.2.0",
        "ip_version": "v4",
        "vhost_raw": "www.example.org",
        "vhost": "example.org",
        "method": "GET",
        "path": "/shop/",
        "status": 200,
        "bytes": 100,
        "referer": "-",
        "ua": CHROME,
        "category": "browser",
        "bot_name": None,
        "is_scanner_path": 0,
        "is_self": 0,
        "source_file": "access.log.1",
        "country_code": None,
        "region": None,
        "city": None,
        "asn": None,
    }
    columns = ", ".join(row)
    placeholders = ", ".join("?" * len(row))
    conn.executemany(
        f"INSERT INTO requests ({columns}) VALUES ({placeholders})",
        [tuple(row.values()), tuple(row.values())],
    )
    conn.commit()
    conn.close()

    conn = connect(db_path)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM requests WHERE sig IS NULL").fetchone()[0] == 0
    counts = conn.execute("SELECT sig, source_file, n FROM line_counts").fetchall()
    assert len(counts) == 1
    assert counts[0]["n"] == 2
    expected = line_signature(*(row[name] for name in SIG_FIELDS))
    assert bytes(counts[0]["sig"]) == expected
    conn.close()


def test_migration_v2_to_v3_backfills_max_day(tmp_path: Path):
    """An existing database gains max_day without touching its numbers."""

    db_path = tmp_path / "v2.sqlite"
    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO ingested_files(path, size, mtime, lines, parsed, failed, ingested_at) "
        "VALUES ('access.log.1', 10, 0, 2, 2, 0, '2026-11-14T00:00:00')"
    )
    row = {
        "ts": "2026-11-14 00:00:02",
        "day": "2026-11-14",
        "iso_week": "2026-W46",
        "ip": "192.0.2.0",
        "net": "192.0.2.0",
        "ip_version": "v4",
        "vhost_raw": "www.example.org",
        "vhost": "example.org",
        "method": "GET",
        "path": "/shop/",
        "status": 200,
        "bytes": 100,
        "referer": "-",
        "ua": CHROME,
        "category": "browser",
        "bot_name": None,
        "is_scanner_path": 0,
        "is_self": 0,
        "source_file": "access.log.1",
        "sig": b"0123456789abcdef",
    }
    columns = ", ".join(row)
    placeholders = ", ".join("?" * len(row))
    conn.execute(f"INSERT INTO requests ({columns}) VALUES ({placeholders})", tuple(row.values()))
    # Simulate the old schema: drop the column and the migration marker.
    conn.execute("ALTER TABLE ingested_files DROP COLUMN max_day")
    conn.execute("DELETE FROM meta WHERE key = 'max_day_version'")
    conn.commit()
    conn.close()

    conn = connect(db_path)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
    row_out = conn.execute(
        "SELECT max_day FROM ingested_files WHERE path='access.log.1'"
    ).fetchone()
    assert row_out["max_day"] == "2026-11-14"
    conn.close()


def test_untracked_max_day_row_is_dropped_for_reread(tmp_path: Path):
    """A file whose rows vanished entirely gets re-read instead of pinned to NULL."""
    from app.db import _backfill_max_day, _has_column  # noqa: F401 - private on purpose

    db_path = tmp_path / "gap.sqlite"
    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO ingested_files(path, size, mtime, lines, parsed, failed, ingested_at) "
        "VALUES ('access.log.45.gz', 10, 0, 5, 5, 0, '2026-11-14T00:00:00')"
    )
    conn.commit()
    _backfill_max_day(conn)
    assert conn.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0] == 0
    conn.close()


def test_all_history_networks_are_stored(tmp_path: Path, monkeypatch):
    cfg = make_config(tmp_path)
    cfg.history_url = "https://history.example.org/api"
    monkeypatch.setattr(
        "app.ingest.fetch_self_networks",
        lambda url, timeout=10.0: {"192.0.2.0", "203.0.113.0"},
    )

    ingest(cfg)

    conn = connect(cfg.db_path)
    try:
        networks = {row["net"] for row in conn.execute("SELECT net FROM self_networks")}
    finally:
        conn.close()
    assert networks == {"192.0.2.0", "203.0.113.0"}
