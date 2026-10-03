"""Verify the backup can actually recover committed data, including a live WAL."""
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run_backup(tmp_path, *arguments):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "sqlite:///" + (tmp_path / "source.db").as_posix()
    environment["REPORTS_DIR"] = str(tmp_path / "private")
    environment["BACKUP_DIR"] = str(tmp_path / "backups")
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "backup.py"), "--project-root", str(tmp_path), *map(str, arguments)], env=environment, capture_output=True, text=True)


def test_online_backup_restore_external_copy_and_corruption(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "test.txt").write_text("仅用于软件测试的附件", encoding="utf-8")
    with sqlite3.connect(tmp_path / "source.db") as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE tests (value TEXT)")
        live.execute("INSERT INTO tests VALUES ('committed WAL data')")
        live.commit()
        result = run_backup(tmp_path, "--external-dir", tmp_path / "external")
        assert result.returncode == 0, result.stderr
        folders = list((tmp_path / "backups").iterdir())
        assert len(folders) == 1
        backup = folders[0]
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "complete"
        # Recover into a separate file, never overwrite the live database.
        with sqlite3.connect(backup / "database.sqlite3") as restored:
            assert restored.execute("SELECT value FROM tests").fetchall() == [("committed WAL data",)]
        assert (tmp_path / "external" / backup.name / "manifest.json").is_file()
        assert run_backup(tmp_path, "--verify", backup).returncode == 0
        (backup / "reports" / "test.txt").write_text("损坏的副本", encoding="utf-8")
        damaged = run_backup(tmp_path, "--verify", backup)
        assert damaged.returncode != 0
        assert "failed" in damaged.stderr.lower()


def test_missing_database_never_reports_backup_success(tmp_path):
    (tmp_path / "private").mkdir()
    result = run_backup(tmp_path)
    assert result.returncode != 0
    assert "verified" not in result.stdout.lower()
