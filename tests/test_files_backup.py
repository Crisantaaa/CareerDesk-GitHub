import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from careerdesk.backup import backup_database, export_csv, export_json, restore_database, validate_backup
from careerdesk import SCHEMA_VERSION
from careerdesk.core import Store
from careerdesk.files import list_files, path_key, preview, refresh_availability, relocate, remove_index, scan_directory


class FileBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "isolated.sqlite3")
        self.docs = self.root / "测试 资料"
        self.docs.mkdir()

    def test_scan_link_move_preview_and_remove_without_touching_original(self):
        company = self.store.save_company({"name": "星河科技"})
        position = self.store.save_position({"company_id": company, "title": "数据分析"})
        app = self.store.create_application(position)
        original = self.docs / "中文 简历.txt"
        original.write_text("这是我的简历内容。", encoding="utf-8")
        first = scan_directory(self.store, self.docs)
        self.assertEqual((1, 0), (first["added"], first["updated"]))
        file = list_files(self.store)[0]
        self.store.link_file(file["id"], "application", app)
        self.store.link_file(file["id"], "position", position)
        self.assertIn("简历内容", preview(self.store, file["id"])["content"])
        second = scan_directory(self.store, self.docs)
        self.assertEqual((0, 1), (second["added"], second["updated"]))
        self.assertEqual(1, len(list_files(self.store)))
        original.write_text("更新后的简历内容。", encoding="utf-8")
        scan_directory(self.store, self.docs)
        self.assertEqual(original.stat().st_size, self.store.get("files", file["id"])["size_bytes"])
        moved = self.docs / "简历 新位置.txt"
        original.rename(moved)
        self.assertFalse(refresh_availability(self.store, file["id"]))
        relocate(self.store, file["id"], moved)
        self.assertEqual(path_key(moved), self.store.get("files", file["id"])["path_key"])
        self.assertEqual(1, len(self.store.query("SELECT * FROM file_applications WHERE file_id=?", (file["id"],))))
        self.store.link_file(file["id"], "position", position, False)
        self.assertTrue(moved.is_file())
        remove_index(self.store, file["id"])
        self.assertTrue(moved.is_file())
        self.assertEqual([], list_files(self.store))

    def test_scan_scope_failure_and_subfolders(self):
        nested = self.docs / "子目录"
        nested.mkdir()
        (nested / "岗位 JD.md").write_text("JD", encoding="utf-8")
        self.assertEqual(0, scan_directory(self.store, self.docs)["added"])
        self.assertEqual(1, scan_directory(self.store, self.docs, recursive=True)["added"])
        before = len(list_files(self.store))
        with self.assertRaises(ValueError):
            scan_directory(self.store, self.root / "不存在的目录")
        self.assertEqual(before, len(list_files(self.store)))

    def test_backup_restore_and_exports(self):
        company = self.store.save_company({"name": "备份前公司"})
        backup = backup_database(self.store.path, self.root / "backups")
        self.assertEqual(SCHEMA_VERSION, validate_backup(backup)["schema_version"])
        self.store.save_company({"name": "备份后公司"})
        safety = restore_database(self.store.path, backup, self.root / "backups")
        self.assertTrue(safety.is_file())
        self.assertEqual([company], [c["id"] for c in Store(self.store.path).list_companies()])
        export = export_json(self.store.path, self.root / "exports")
        payload = json.loads(export.read_text(encoding="utf-8"))
        self.assertEqual("careerdesk", payload["project_id"])
        self.assertEqual(company, payload["tables"]["companies"][0]["id"])
        csv_files = export_csv(self.store.path, self.root / "exports")
        self.assertTrue(csv_files)
        self.assertEqual(b"\xef\xbb\xbf", csv_files[0].read_bytes()[:3])
        damaged = self.root / "damaged.sqlite3"
        damaged.write_bytes(b"not a sqlite database")
        with self.assertRaises(ValueError):
            restore_database(self.store.path, damaged, self.root / "backups")
        self.assertEqual([company], [c["id"] for c in Store(self.store.path).list_companies()])
        other = self.root / "other.sqlite3"
        other.write_bytes(b"")
        with self.assertRaises(ValueError):
            validate_backup(other)

    def test_office_preview_reads_without_changing_files(self):
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation

        doc_path = self.docs / "中文 简历.docx"
        document = Document()
        document.add_paragraph("项目经历：数据分析")
        document.save(doc_path)
        slides_path = self.docs / "面试准备.pptx"
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = "面试介绍"
        presentation.save(slides_path)
        sheet_path = self.docs / "岗位清单.xlsx"
        workbook = Workbook()
        workbook.active.append(["公司", "岗位"])
        workbook.active.append(["星河科技", "工程师"])
        workbook.save(sheet_path)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (doc_path, slides_path, sheet_path)}
        scan_directory(self.store, self.docs)
        by_name = {f["filename"]: f["id"] for f in list_files(self.store)}
        self.assertIn("项目经历", preview(self.store, by_name[doc_path.name])["content"])
        self.assertIn("面试介绍", preview(self.store, by_name[slides_path.name])["content"])
        self.assertEqual("星河科技", preview(self.store, by_name[sheet_path.name])["sheets"]["Sheet"][1][0])
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (doc_path, slides_path, sheet_path)}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
