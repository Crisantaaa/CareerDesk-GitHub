"""SQLite initialization and connection helpers."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
MAINTENANCE_LOCK = threading.RLock()


def data_dir() -> Path:
    """A per-process override keeps tests and UI checks away from real data."""
    override = os.environ.get("CAREERDESK_DATA_DIR")
    return Path(override).resolve() if override else ROOT / "data"


def database_path() -> Path:
    return data_dir() / "careerdesk.sqlite3"


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path) if path else database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("无法启用 SQLite 外键约束")
    return connection


@contextmanager
def transaction(path: Path | None = None):
    with MAINTENANCE_LOCK:
        con = connect(path)
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, english_name TEXT, name_key TEXT NOT NULL UNIQUE,
 industry TEXT, website TEXT, recruitment_url TEXT, cities TEXT, research_notes TEXT,
 evaluation TEXT, priority TEXT NOT NULL DEFAULT '未评估',
 created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL, archived_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS positions (
 id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), title TEXT NOT NULL,
 recruitment_project TEXT, department TEXT, city TEXT, recruitment_batch TEXT,
 major_requirements TEXT, graduation_time_requirement TEXT, jd TEXT, apply_url TEXT,
 deadline_on TEXT, interest TEXT NOT NULL DEFAULT '未评估',
 priority TEXT NOT NULL DEFAULT '未评估', match_rating TEXT NOT NULL DEFAULT '未评估',
 strengths TEXT, gaps TEXT, questions TEXT, notes TEXT,
 created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL, archived_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS status_definitions (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, category TEXT NOT NULL
 CHECK(category IN ('未投递','推进中','已结束')),
 is_builtin INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
 sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS status_time_fields (
 id TEXT PRIMARY KEY, status_id TEXT NOT NULL REFERENCES status_definitions(id),
 label TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('通知','截止','安排','完成')),
 sort_order INTEGER NOT NULL DEFAULT 0, UNIQUE(status_id,label)
);
CREATE TABLE IF NOT EXISTS applications (
 id TEXT PRIMARY KEY, position_id TEXT NOT NULL REFERENCES positions(id), round_label TEXT,
 current_status_id TEXT NOT NULL REFERENCES status_definitions(id),
 current_stage_id TEXT REFERENCES application_stages(id),
 submitted_on TEXT, submitted_at_utc TEXT,
 submission_precision TEXT NOT NULL DEFAULT '无'
 CHECK(submission_precision IN ('无','日期','时间','明确未知')),
 notes TEXT, created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL, archived_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS application_stages (
 id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
 status_id TEXT NOT NULL REFERENCES status_definitions(id),
 entered_at_utc TEXT NOT NULL, exited_at_utc TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS stage_time_nodes (
 id TEXT PRIMARY KEY, stage_id TEXT NOT NULL REFERENCES application_stages(id),
 field_id TEXT REFERENCES status_time_fields(id), label TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('通知','截止','安排','完成')),
 date_on TEXT, at_utc TEXT,
 precision TEXT NOT NULL DEFAULT '未知' CHECK(precision IN ('未知','日期','时间')),
 notes TEXT, UNIQUE(stage_id,label)
);
CREATE TABLE IF NOT EXISTS status_history (
 id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
 old_status_id TEXT REFERENCES status_definitions(id),
 new_status_id TEXT NOT NULL REFERENCES status_definitions(id),
 old_name_snapshot TEXT, new_name_snapshot TEXT NOT NULL,
 stage_id TEXT NOT NULL REFERENCES application_stages(id),
 changed_at_utc TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS recruitment_events (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT '已安排' CHECK(status IN ('已安排','已完成','已取消')),
 company_id TEXT NOT NULL REFERENCES companies(id), position_id TEXT REFERENCES positions(id),
 application_id TEXT REFERENCES applications(id), stage_id TEXT REFERENCES application_stages(id),
 node_id TEXT REFERENCES stage_time_nodes(id), event_on TEXT, event_at_utc TEXT,
 precision TEXT NOT NULL DEFAULT '未知' CHECK(precision IN ('未知','日期','时间')),
 round_label TEXT, location_or_link TEXT, requirements TEXT, notice_text TEXT, notes TEXT,
 created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, company_id TEXT NOT NULL REFERENCES companies(id),
 position_id TEXT REFERENCES positions(id), application_id TEXT REFERENCES applications(id),
 stage_id TEXT REFERENCES application_stages(id), due_on TEXT, due_at_utc TEXT,
 precision TEXT NOT NULL DEFAULT '未知' CHECK(precision IN ('未知','日期','时间')),
 priority TEXT NOT NULL DEFAULT '中', completed_at_utc TEXT, notes TEXT,
 created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
 id TEXT PRIMARY KEY, display_path TEXT NOT NULL, path_key TEXT NOT NULL UNIQUE,
 filename TEXT NOT NULL, ext TEXT NOT NULL, size_bytes INTEGER NOT NULL,
 modified_at_utc TEXT NOT NULL, last_scanned_at_utc TEXT NOT NULL,
 availability TEXT NOT NULL DEFAULT '可用', tags TEXT, version_name TEXT, notes TEXT,
 created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 tags TEXT, notes TEXT, start_on TEXT, end_on TEXT, star_situation TEXT, star_task TEXT,
 star_action TEXT, star_result TEXT,
 created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL, archived_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS file_companies (file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, company_id TEXT NOT NULL REFERENCES companies(id), PRIMARY KEY(file_id,company_id));
CREATE TABLE IF NOT EXISTS file_positions (file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, position_id TEXT NOT NULL REFERENCES positions(id), PRIMARY KEY(file_id,position_id));
CREATE TABLE IF NOT EXISTS file_applications (file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, application_id TEXT NOT NULL REFERENCES applications(id), PRIMARY KEY(file_id,application_id));
CREATE TABLE IF NOT EXISTS file_profiles (file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, profile_id TEXT NOT NULL REFERENCES profiles(id), PRIMARY KEY(file_id,profile_id));
CREATE TABLE IF NOT EXISTS profile_applications (profile_id TEXT NOT NULL REFERENCES profiles(id), application_id TEXT NOT NULL REFERENCES applications(id), PRIMARY KEY(profile_id,application_id));
CREATE TABLE IF NOT EXISTS stage_files (stage_id TEXT NOT NULL REFERENCES application_stages(id), file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, note TEXT, PRIMARY KEY(stage_id,file_id));
CREATE TABLE IF NOT EXISTS stage_profiles (stage_id TEXT NOT NULL REFERENCES application_stages(id), profile_id TEXT NOT NULL REFERENCES profiles(id), note TEXT, PRIMARY KEY(stage_id,profile_id));
CREATE TABLE IF NOT EXISTS import_batches (id TEXT PRIMARY KEY, source_fingerprint TEXT NOT NULL, imported_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_rows (
 batch_id TEXT NOT NULL REFERENCES import_batches(id), row_kind TEXT NOT NULL,
 row_number INTEGER NOT NULL, row_fingerprint TEXT NOT NULL, decision TEXT NOT NULL,
 target_id TEXT, PRIMARY KEY(batch_id,row_kind,row_number)
);
CREATE INDEX IF NOT EXISTS ix_positions_company ON positions(company_id);
CREATE INDEX IF NOT EXISTS ix_applications_position ON applications(position_id);
CREATE INDEX IF NOT EXISTS ix_events_company ON recruitment_events(company_id);
CREATE INDEX IF NOT EXISTS ix_todos_company ON todos(company_id);
CREATE INDEX IF NOT EXISTS ix_nodes_stage ON stage_time_nodes(stage_id);
"""

BUILTIN_STATUSES = [
    ("待研究", "未投递", []),
    ("已投递", "推进中", []),
    ("测评准备", "推进中", [("通知时间", "通知"), ("测评截止时间", "截止")]),
    ("AI 面试准备", "推进中", [("通知时间", "通知"), ("AI 面试截止时间", "截止")]),
    ("视频面试准备", "推进中", [("通知时间", "通知"), ("视频面试时间", "安排")]),
    ("已测评", "推进中", [("测评完成时间", "完成")]),
    ("真人面试通知", "推进中", [("通知时间", "通知"), ("面试时间", "安排")]),
    ("面试", "推进中", []),
    ("等待结果", "推进中", []),
    ("已录用", "已结束", []),
    ("已拒绝", "已结束", []),
    ("主动退出", "已结束", []),
    ("已关闭", "已结束", []),
]


def initialize(path: Path | None = None) -> Path:
    target = Path(path) if path else database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = connect(target)
    try:
        current = con.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError("数据库版本高于本程序，已拒绝打开")
        if current == 0 and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
            raise RuntimeError("目标位置已有其他数据库表，已拒绝改写")
        if current < SCHEMA_VERSION and current != 0 and target.stat().st_size:
            from .backup import backup_database
            backup_database(target, target.parent / "backups", label="pre-migration")
        if current == 0:
            con.executescript(SCHEMA)
            with con:
                con.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('project_id','careerdesk')")
                con.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('export_version','1')")
                for order, (name, category, fields) in enumerate(BUILTIN_STATUSES):
                    status_id = str(uuid.uuid4())
                    con.execute("INSERT OR IGNORE INTO status_definitions VALUES(?,?,?,?,?,?)",
                                (status_id, name, category, 1, 1, order))
                    actual_id = con.execute("SELECT id FROM status_definitions WHERE name=?", (name,)).fetchone()[0]
                    for index, (label, kind) in enumerate(fields):
                        con.execute("INSERT OR IGNORE INTO status_time_fields VALUES(?,?,?,?,?)",
                                    (str(uuid.uuid4()), actual_id, label, kind, index))
                con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1,datetime('now'))")
                con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2,datetime('now'))")
                con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3,datetime('now'))")
                con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif current == 1:
            with con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("ALTER TABLE companies ADD COLUMN english_name TEXT")
                con.execute("ALTER TABLE positions ADD COLUMN recruitment_project TEXT")
                con.execute("ALTER TABLE positions ADD COLUMN major_requirements TEXT")
                con.execute("ALTER TABLE positions ADD COLUMN graduation_time_requirement TEXT")
                con.execute("INSERT INTO schema_migrations VALUES(2,datetime('now'))")
                con.execute("PRAGMA user_version=2")
            current = 2
        if current == 2:
            with con:
                for offset, (name, category, fields) in enumerate(BUILTIN_STATUSES[2:5]):
                    status_id = str(uuid.uuid4())
                    con.execute("INSERT OR IGNORE INTO status_definitions VALUES(?,?,?,?,?,?)",
                                (status_id, name, category, 1, 1, 2 + offset))
                    actual_id = con.execute("SELECT id FROM status_definitions WHERE name=?", (name,)).fetchone()[0]
                    for index, (label, kind) in enumerate(fields):
                        con.execute("INSERT OR IGNORE INTO status_time_fields VALUES(?,?,?,?,?)",
                                    (str(uuid.uuid4()), actual_id, label, kind, index))
                con.execute("UPDATE status_definitions SET is_active=0 WHERE name='测评/AI 面试准备'")
                con.execute("UPDATE status_definitions SET sort_order=100+sort_order WHERE is_builtin=0")
                for order, (name, _, _) in enumerate(BUILTIN_STATUSES):
                    con.execute("UPDATE status_definitions SET sort_order=? WHERE name=?", (order, name))
                con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3,datetime('now'))")
                con.execute("PRAGMA user_version=3")
        if con.execute("SELECT value FROM app_meta WHERE key='project_id'").fetchone()[0] != "careerdesk":
            raise RuntimeError("数据库项目标识不匹配")
        return target
    finally:
        con.close()
