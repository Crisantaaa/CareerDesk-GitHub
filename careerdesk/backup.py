"""Consistent SQLite backup, verified restore, and offline exports."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import EXPORT_VERSION, SCHEMA_VERSION
from .db import MAINTENANCE_LOCK, connect

EXPORT_TABLES = (
    "app_meta", "companies", "positions", "status_definitions", "status_time_fields",
    "applications", "application_stages", "stage_time_nodes", "status_history",
    "recruitment_events", "todos", "files", "profiles", "file_companies",
    "file_positions", "file_applications", "file_profiles", "profile_applications",
    "stage_files", "stage_profiles", "import_batches", "import_rows",
)
CSV_TABLES = ("companies", "positions", "applications", "recruitment_events", "todos", "profiles", "files")


def _unique_name(prefix: str, suffix: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}{suffix}"


def validate_backup(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValueError("备份文件不存在")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份完整性检查失败")
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"app_meta", "schema_migrations", "companies", "applications"}.issubset(tables):
                raise ValueError("不是完整的 CareerDesk 数据库")
            marker = con.execute("SELECT value FROM app_meta WHERE key='project_id'").fetchone()
            if not marker or marker[0] != "careerdesk":
                raise ValueError("备份项目标识不匹配")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version < 1 or version > SCHEMA_VERSION:
                raise ValueError(f"备份版本 {version} 与当前版本 {SCHEMA_VERSION} 不兼容")
            recorded = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if recorded != version:
                raise ValueError("备份迁移记录不完整")
            return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "schema_version": version}
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"备份无法读取或已损坏：{exc}") from exc


def backup_database(source: Path, backup_dir: Path, label="backup") -> Path:
    source = Path(source)
    if not source.is_file():
        raise ValueError("当前数据库不存在")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / _unique_name(f"careerdesk-{label}", ".sqlite3")
    with MAINTENANCE_LOCK:
        try:
            with closing(connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
            validate_backup(destination)
            return destination
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def restore_database(current: Path, candidate: Path, backup_dir: Path) -> Path:
    """Return the mandatory pre-restore backup path; keep current on failure."""
    current = Path(current)
    candidate = Path(candidate)
    validate_backup(candidate)
    with MAINTENANCE_LOCK:
        safety = backup_database(current, backup_dir, label="before-restore")
        staged = current.with_name(f".__restore-{uuid.uuid4().hex}.sqlite3")
        try:
            uri = f"file:{candidate.resolve().as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as src, closing(sqlite3.connect(staged)) as dst:
                src.backup(dst)
            from .db import initialize
            initialize(staged)
            validate_backup(staged)
            os.replace(staged, current)
            return safety
        except Exception:
            staged.unlink(missing_ok=True)
            if not current.is_file():
                with closing(sqlite3.connect(safety)) as src, closing(sqlite3.connect(current)) as dst:
                    src.backup(dst)
            raise


def export_json(database: Path, export_dir: Path) -> Path:
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    destination = export_dir / _unique_name("careerdesk-export", ".json")
    with closing(connect(database)) as con:
        payload = {
            "project_id": "careerdesk",
            "export_version": EXPORT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tables": {table: [dict(row) for row in con.execute(f"SELECT * FROM {table}")] for table in EXPORT_TABLES},
        }
    with destination.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
    return destination


def export_csv(database: Path, export_dir: Path) -> list[Path]:
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    result = []
    with closing(connect(database)) as con:
        for table in CSV_TABLES:
            destination = export_dir / _unique_name(f"careerdesk-{table}", ".csv")
            cursor = con.execute(f"SELECT * FROM {table}")
            with destination.open("x", encoding="utf-8-sig", newline="") as output:
                writer = csv.writer(output)
                writer.writerow([column[0] for column in cursor.description])
                writer.writerows(cursor)
            result.append(destination)
    return result
