import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from careerdesk.backup import restore_database, validate_backup
from careerdesk.core import Store
from careerdesk.db import SCHEMA


class MigrationTests(unittest.TestCase):
    def test_v1_data_and_backup_survive_new_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "careerdesk.sqlite3"
            old_schema = (SCHEMA
                .replace("name TEXT NOT NULL, english_name TEXT, name_key", "name TEXT NOT NULL, name_key")
                .replace(" recruitment_project TEXT, department TEXT", " department TEXT")
                .replace(" major_requirements TEXT, graduation_time_requirement TEXT, jd TEXT", " jd TEXT"))
            with closing(sqlite3.connect(path)) as con:
                con.executescript(old_schema)
                con.execute("INSERT INTO app_meta VALUES('project_id','careerdesk')")
                con.execute("INSERT INTO schema_migrations VALUES(1,datetime('now'))")
                con.execute("PRAGMA user_version=1")
                con.execute("""INSERT INTO companies(id,name,name_key,created_at_utc,updated_at_utc)
                             VALUES('company-1','旧公司','旧公司','2026-01-01','2026-01-01')""")
                con.execute("""INSERT INTO positions(id,company_id,title,created_at_utc,updated_at_utc)
                             VALUES('position-1','company-1','旧岗位','2026-01-01','2026-01-01')""")
                con.commit()
            store = Store(path)
            self.assertEqual("旧公司", store.get("companies", "company-1")["name"])
            self.assertEqual("旧岗位", store.get("positions", "position-1")["title"])
            self.assertIsNone(store.get("companies", "company-1")["english_name"])
            self.assertIsNone(store.get("positions", "position-1")["recruitment_project"])
            old_backups = list((path.parent / "backups").glob("careerdesk-pre-migration-*.sqlite3"))
            self.assertEqual(1, len(old_backups))
            self.assertEqual(1, validate_backup(old_backups[0])["schema_version"])
            store.save_company({"name": "旧公司", "english_name": "Legacy Co."}, "company-1")
            self.assertEqual("Legacy Co.", store.get("companies", "company-1")["english_name"])
            restore_database(path, old_backups[0], path.parent / "backups")
            restored = Store(path)
            self.assertEqual("旧公司", restored.get("companies", "company-1")["name"])
            self.assertIsNone(restored.get("companies", "company-1")["english_name"])
            self.assertIsNone(restored.get("positions", "position-1")["major_requirements"])
