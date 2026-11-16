# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from datetime import date, timedelta
from pathlib import Path

from app.config import Config
from app.db import connect
from app.ingest import ingest
from app.retention import cutoff_for, prune_log_files

LINE_TMPL = (
    '{ip} - - [{day}:00:00:02 +0100] "GET /shop/ HTTP/1.1" 200 100 '
    'www.example.org "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36" "-"'
)


def _line(day: date, ip: str = "192.0.2.0") -> str:
    return LINE_TMPL.format(ip=ip, day=day.strftime("%d/%b/%Y"))


def write_log(path: Path, day: date, count: int = 1) -> None:
    lines = [_line(day, f"192.0.2.{i}") for i in range(count)]
    path.write_text("\n".join(lines) + "\n")


def make_config(tmp_path: Path, days: int = 400) -> Config:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return Config(
        log_dir=str(logs),
        db_path=str(tmp_path / "db.sqlite"),
        file_retention_days=days,
    )


def test_cutoff_for():
    today = date(2026, 11, 16)
    assert cutoff_for(400, today) == date(2025, 10, 12)
    assert cutoff_for(0, today) is None
    assert cutoff_for(-5, today) is None


def test_old_file_is_pruned_and_db_rows_are_kept(tmp_path: Path):
    cfg = make_config(tmp_path)
    logs = Path(cfg.log_dir)
    old_day = date.today() - timedelta(days=500)
    fresh_day = date.today() - timedelta(days=10)
    write_log(logs / "access.log.1", old_day)
    write_log(logs / "access.log.2", fresh_day)

    ingest(cfg, refresh_self=False)
    stats = prune_log_files(cfg)

    assert stats == {"checked": 2, "pruned": 1, "kept": 1}
    assert not (logs / "access.log.1").exists()
    assert (logs / "access.log.2").exists()

    conn = connect(cfg.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM line_counts").fetchone()[0] == 2
    finally:
        conn.close()


def test_dry_run_deletes_nothing(tmp_path: Path):
    cfg = make_config(tmp_path)
    logs = Path(cfg.log_dir)
    write_log(logs / "access.log.1", date.today() - timedelta(days=500))
    ingest(cfg, refresh_self=False)

    stats = prune_log_files(cfg, dry_run=True)
    assert stats["pruned"] == 1
    assert (logs / "access.log.1").exists()


def test_untracked_file_is_never_deleted(tmp_path: Path):
    """A file that was never ingested might be a user's own copy."""
    cfg = make_config(tmp_path)
    logs = Path(cfg.log_dir)
    write_log(logs / "access.log.1", date.today() - timedelta(days=500))

    stats = prune_log_files(cfg)  # no ingest happened
    assert stats == {"checked": 1, "pruned": 0, "kept": 1}
    assert (logs / "access.log.1").exists()


def test_retention_zero_disables_pruning(tmp_path: Path):
    cfg = make_config(tmp_path, days=0)
    logs = Path(cfg.log_dir)
    write_log(logs / "access.log.1", date.today() - timedelta(days=500))
    ingest(cfg, refresh_self=False)

    stats = prune_log_files(cfg)
    assert stats == {"checked": 0, "pruned": 0, "kept": 0}
    assert (logs / "access.log.1").exists()


def test_max_day_comes_from_log_lines_not_mtime(tmp_path: Path):
    """A fresh download of an old log must still be pruned."""
    cfg = make_config(tmp_path)
    logs = Path(cfg.log_dir)
    old_day = date.today() - timedelta(days=500)
    write_log(logs / "access.log.1", old_day)

    ingest(cfg, refresh_self=False)
    conn = connect(cfg.db_path)
    try:
        row = conn.execute("SELECT max_day FROM ingested_files").fetchone()
    finally:
        conn.close()
    assert row["max_day"] == old_day.isoformat()
    # File mtime is "now", so a mtime-based check would never prune it.
    assert prune_log_files(cfg)["pruned"] == 1


def test_cli_days_override(tmp_path: Path):
    cfg = make_config(tmp_path, days=0)
    logs = Path(cfg.log_dir)
    write_log(logs / "access.log.1", date.today() - timedelta(days=20))
    ingest(cfg, refresh_self=False)

    assert prune_log_files(cfg)["pruned"] == 0
    assert prune_log_files(cfg, days=10)["pruned"] == 1


def test_file_with_unparseable_max_day_is_kept(tmp_path: Path):
    cfg = make_config(tmp_path)
    logs = Path(cfg.log_dir)
    write_log(logs / "access.log.1", date.today() - timedelta(days=500))
    ingest(cfg, refresh_self=False)

    conn = connect(cfg.db_path)
    conn.execute("UPDATE ingested_files SET max_day = 'not-a-date'")
    conn.commit()
    conn.close()

    assert prune_log_files(cfg)["pruned"] == 0
    assert (logs / "access.log.1").exists()
