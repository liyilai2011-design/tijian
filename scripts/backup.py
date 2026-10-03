"""Back up the server database and private files without third-party Python modules.

Run from any directory. Relative configuration paths resolve from the project root.
Credentials are read from .env/environment and never placed in subprocess arguments.
This script does not delete old backups or restore over an existing database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4


class BackupError(Exception):
    """A safe, non-secret error suitable for the console."""


def read_config(project: Path, env_file: Path) -> dict[str, str]:
    keys = {"DATABASE_URL", "DATA_DIR", "REPORTS_DIR", "BACKUP_DIR"}
    values: dict[str, str] = {}
    if env_file.exists():
        for number, line in enumerate(env_file.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)$", line)
            if not match or match[1] not in keys:
                continue
            key, value = match[1], match[2].strip()
            if value.startswith(("'", '"')):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                    raise BackupError(f"Invalid quoted configuration at .env line {number}.")
                value = value[1:end]
            else:
                value = re.split(r"\s+#", value, 1)[0].rstrip()
            if "${" in value:
                raise BackupError("Backup configuration must use explicit values; variable expansion is not supported.")
            values[key] = value
    for key in keys:
        if key in os.environ:
            values[key] = os.environ[key]
    values["DATA_DIR"] = values.get("DATA_DIR") or "data"
    data = resolve_path(project, values["DATA_DIR"])
    values["DATABASE_URL"] = values.get("DATABASE_URL") or "sqlite:///" + (data / "healthcheck.db").as_posix()
    values["REPORTS_DIR"] = values.get("REPORTS_DIR") or str(data / "reports")
    values["BACKUP_DIR"] = values.get("BACKUP_DIR") or "backups"
    return values


def resolve_path(project: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else project / path).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sqlite_path(project: Path, url: str) -> Path:
    # SQLAlchemy SQLite file URLs have three slashes, followed by the file path.
    match = re.match(r"^sqlite(?:\+pysqlite)?:///(.+)$", url)
    if not match or "?" in match[1] or match[1] == ":memory:":
        raise BackupError("Use a file-backed SQLite DATABASE_URL without URI query parameters.")
    path = resolve_path(project, match[1])
    if not path.is_file():
        raise BackupError("SQLite database does not exist. Initialize the system before backing it up.")
    return path


def verify_sqlite(path: Path) -> None:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            raise BackupError("SQLite backup integrity check failed.")


def backup_sqlite(source: Path, target: Path) -> None:
    # sqlite3.backup includes committed WAL transactions; copying the live .db alone does not.
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30) as original:
        with sqlite3.connect(target) as backup:
            original.backup(backup, pages=256, sleep=0.1)
    verify_sqlite(target)


def pg_executable(name: str, pg_bin: str | None) -> str:
    if pg_bin:
        candidate = Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))
        if candidate.is_file():
            return str(candidate)
        raise BackupError(f"{name} was not found in --pg-bin.")
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} is not on PATH. Specify the PostgreSQL bin directory with --pg-bin.")
    return found


def postgres_environment(url: str) -> dict[str, str]:
    parsed = urlsplit(re.sub(r"^postgresql\+[^:]+:", "postgresql:", url))
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.path.strip("/"):
        raise BackupError("Invalid PostgreSQL DATABASE_URL.")
    child_env = os.environ.copy()
    child_env["PGDATABASE"] = unquote(parsed.path[1:])
    if parsed.hostname:
        child_env["PGHOST"] = parsed.hostname
    if parsed.port:
        child_env["PGPORT"] = str(parsed.port)
    if parsed.username is not None:
        child_env["PGUSER"] = unquote(parsed.username)
    if parsed.password is not None:
        child_env["PGPASSWORD"] = unquote(parsed.password)
    options = {
        "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
        "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME",
    }
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key not in options:
            raise BackupError("Unsupported PostgreSQL URL option. Check the backup configuration.")
        child_env[options[key]] = value
    child_env.setdefault("PGCONNECT_TIMEOUT", "15")
    return child_env


def run_pg(command: list[str], env: dict[str, str] | None = None) -> None:
    # PostgreSQL stderr can echo connection details; do not print or store it.
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise BackupError("PostgreSQL backup/archive check failed. Check service status, tool version, and credentials.")


def backup_postgres(url: str, target: Path, pg_bin: str | None) -> None:
    run_pg([pg_executable("pg_dump", pg_bin), "--format=custom", "--no-owner", "--no-acl",
            "--no-password", "--file", str(target)], postgres_environment(url))
    run_pg([pg_executable("pg_restore", pg_bin), "--list", str(target)])


def copy_private_files(source: Path, destination: Path) -> None:
    if not source.exists():
        raise BackupError("Private reports directory is missing. Check REPORTS_DIR before backing up.")
    if not source.is_dir() or source.is_symlink():
        raise BackupError("Private reports must be a real directory, not a symbolic link.")
    destination.mkdir()
    originals = sorted(source.rglob("*"))
    for item in originals:
        if item.is_symlink():
            raise BackupError("Symbolic links are not supported inside the private files directory.")
        relative = item.relative_to(source)
        copied = destination / relative
        if item.is_dir():
            copied.mkdir(exist_ok=True)
        elif item.is_file():
            copied.parent.mkdir(parents=True, exist_ok=True)
            before = item.stat()
            shutil.copy2(item, copied)
            after = item.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha256(item) != sha256(copied):
                raise BackupError("A private file changed during backup. Retry after file writes have stopped.")
        else:
            raise BackupError("Unsupported entry inside the private files directory.")
    if sorted(source.rglob("*")) != originals:
        raise BackupError("The private file list changed during backup. Retry after file writes have stopped.")


def manifest_files(directory: Path) -> list[dict[str, object]]:
    return [{"path": path.relative_to(directory).as_posix(), "size": path.stat().st_size,
             "sha256": sha256(path)} for path in sorted(directory.rglob("*"))
            if path.is_file() and path.relative_to(directory).as_posix() not in {"manifest.json", ".incomplete"}]


def verify_backup(directory: Path, pg_bin: str | None = None, *, allow_incomplete: bool = False) -> dict[str, object]:
    if ((directory / ".incomplete").exists() and not allow_incomplete) or not (directory / "manifest.json").is_file():
        raise BackupError("Backup is incomplete or manifest.json is missing.")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1 or manifest.get("status") != "complete":
        raise BackupError("Unsupported or incomplete backup manifest.")
    expected_paths = set()
    if any(path.is_symlink() for path in directory.rglob("*")):
        raise BackupError("Backup contains a symbolic link.")
    for entry in manifest["files"]:
        path = directory / entry["path"]
        if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink() or not path.is_file():
            raise BackupError("Backup contains an invalid or missing file.")
        expected_paths.add(entry["path"])
        if path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
            raise BackupError("Backup file checksum does not match its manifest.")
    actual_paths = {path.relative_to(directory).as_posix() for path in directory.rglob("*")
                    if path.is_file() and path.relative_to(directory).as_posix() not in {"manifest.json", ".incomplete"}}
    if actual_paths != expected_paths:
        raise BackupError("Backup file list differs from its manifest.")
    if manifest["database_type"] == "sqlite":
        verify_sqlite(directory / "database.sqlite3")
    elif manifest["database_type"] == "postgresql":
        run_pg([pg_executable("pg_restore", pg_bin), "--list", str(directory / "database.dump")])
    else:
        raise BackupError("Unsupported database type in manifest.")
    return manifest


RESTORE_NOTE = """# 恢复说明

此目录包含敏感医疗数据，务必限制读取权限并保护外置备份盘。
manifest.json 保存备份清单和 SHA-256，私有文件位于 reports/。
数据库包含网页报告快照、重要操作日志、结果修改记录。
配置 .env 和 PostgreSQL 角色密码不包含在备份中，请由管理员另行安全保存。

恢复前先在隔离的测试目录和测试数据库演练，不能直接覆盖正在使用的数据库。
先运行 scripts/backup.py --verify 此备份目录，确认文件完整。
SQLite：停止后端，先另外备份现有数据，把 database.sqlite3 复制为新的测试数据库文件；
同时恢复 reports/ 到新的私有目录，配置新的 DATABASE_URL、REPORTS_DIR 后验证。
PostgreSQL：建立空测试数据库，使用 pg_restore --exit-on-error --no-owner --no-acl \
--dbname 测试数据库 database.dump，连接密码使用 pgpass，不要写进命令。
恢复为正式数据库时，由管理员先停止服务、保存旧数据、确认连接目标再操作。
测试登录、历史结果、报告版本、记录数量及私有文件后，才能确认可恢复。

完整流程见项目 docs/DEPLOYMENT.md。本脚本的校验不替代实际恢复演练。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Backup parent directory; defaults to BACKUP_DIR or backups/.")
    parser.add_argument("--external-dir", type=Path, help="Optional second copy, e.g. E:/HealthcheckBackups.")
    parser.add_argument("--pg-bin", help="Directory containing pg_dump and pg_restore of a compatible version.")
    parser.add_argument("--verify", type=Path, help="Verify an existing backup without modifying the database.")
    args = parser.parse_args()
    try:
        if args.verify:
            verify_backup(args.verify.resolve(), args.pg_bin)
            print("Backup verification passed. A restore rehearsal is still required.")
            return 0
        project = args.project_root.resolve()
        config = read_config(project, args.env_file or project / ".env")
        private = resolve_path(project, config["REPORTS_DIR"])
        parent = resolve_path(project, str(args.output_dir or config["BACKUP_DIR"]))
        external_parent = resolve_path(project, str(args.external_dir)) if args.external_dir else None
        if parent.is_relative_to(private) or (external_parent and external_parent.is_relative_to(private)):
            raise BackupError("Backup destination must not be inside the private files directory.")
        parent.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid4().hex[:8]
        folder = parent / name
        folder.mkdir()
        (folder / ".incomplete").write_text("Backup incomplete; do not use for recovery.\n", encoding="utf-8")
        url = config["DATABASE_URL"]
        if url.startswith("sqlite"):
            backup_sqlite(sqlite_path(project, url), folder / "database.sqlite3")
            database_type = "sqlite"
        elif url.startswith(("postgresql:", "postgresql+", "postgres:")):
            backup_postgres(url, folder / "database.dump", args.pg_bin)
            database_type = "postgresql"
        else:
            raise BackupError("DATABASE_URL must use SQLite or PostgreSQL.")
        copy_private_files(private, folder / "reports")
        (folder / "RESTORE.md").write_text(RESTORE_NOTE, encoding="utf-8")
        manifest = {"format_version": 1, "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
                    "database_type": database_type, "files": manifest_files(folder),
                    "validation": "SHA-256 and SQLite integrity_check or PostgreSQL archive listing; not a full restore"}
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_backup(folder, args.pg_bin, allow_incomplete=True)
        (folder / ".incomplete").unlink()
        print(f"Local backup verified: {folder}")
        if external_parent:
            external_parent.mkdir(parents=True, exist_ok=True)
            external = external_parent / name
            if external.resolve().is_relative_to(folder.resolve()):
                raise BackupError("External copy must have a different destination.")
            external.mkdir()
            (external / ".incomplete").write_text("External copy incomplete; do not use for recovery.\n", encoding="utf-8")
            shutil.copytree(folder, external, dirs_exist_ok=True)
            verify_backup(external, args.pg_bin, allow_incomplete=True)
            (external / ".incomplete").unlink()
            print(f"External backup verified: {external}")
        return 0
    except BackupError as error:
        print(f"Backup failed: {error}", file=sys.stderr)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        print("Backup failed: check configuration, disk space, file permissions, and database status. Details are not printed to protect credentials.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
