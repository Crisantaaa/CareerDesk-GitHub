"""A full first-use journey using only disposable files and a disposable database."""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from careerdesk.backup import backup_database, export_csv, export_json, restore_database
from careerdesk.bulk import apply_import, preview_import
from careerdesk.core import Store
from careerdesk.dashboard import overview
from careerdesk.files import list_files, preview, scan_directory
from careerdesk.parsing import notice_suggestions, profile_suggestions
from tests.test_bulk_parsing import workbook_bytes


class JourneyTests(unittest.TestCase):
    def test_import_to_isolated_restore(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            database = base / "careerdesk.sqlite3"
            store = Store(database)
            deadline = (date.today() + timedelta(days=3)).isoformat()
            workbook = workbook_bytes(
                company_rows=[{"公司名称": "旅程测试公司", "行业": "科技", "招聘官网": "https://jobs.example.org/career"}],
                position_rows=[{"公司名称": "旅程测试公司", "岗位名称": "数据岗位", "城市": "上海",
                                "招聘批次": "秋招", "岗位描述JD": "职位说明", "截止日期": deadline}])
            result = apply_import(store, preview_import(store, workbook))
            self.assertEqual((1, 1), (result["companies_added"], result["positions_added"]))
            company = store.list_companies()[0]["id"]
            position = store.list_positions()[0]["id"]
            application = store.create_application(position, "第一轮")
            status = next(s for s in store.statuses() if s["name"] == "测评准备")
            store.change_status(application, status["id"], submission_precision="明确未知")
            stage = store.get("applications", application)["current_stage_id"]
            suggestions = notice_suggestions("测评要求：完成逻辑题\n测评链接：https://test.example.org/x\n请在3天内作答", "2026-10-22 14:00")
            self.assertEqual("2026-10-25 14:00", suggestions["deadline"]["value"])
            event = store.save_event({"company_id": company, "position_id": position,
                                      "application_id": application, "stage_id": stage,
                                      "title": "在线测评", "type": "测评", "status": "已安排",
                                      "precision": "时间", "event_at_utc": suggestions["deadline"]["value"],
                                      "location_or_link": suggestions["location_or_link"]["value"],
                                      "requirements": suggestions["requirements"]["value"]},
                                     stage_nodes=[{"label": "测评截止时间", "kind": "截止", "precision": "时间",
                                                   "value": suggestions["deadline"]["value"]}])
            self.assertTrue(store.get("recruitment_events", event)["node_id"])
            store.save_todo({"company_id": company, "position_id": position,
                             "application_id": application, "stage_id": stage,
                             "title": "准备测评", "precision": "日期", "due_on": deadline})
            document = base / "我的 简历.txt"
            document.write_text("旅程测试简历", encoding="utf-8")
            original = document.read_bytes()
            self.assertEqual(1, scan_directory(store, base)["added"])
            file_id = list_files(store)[0]["id"]
            store.link_file(file_id, "stage", stage)
            self.assertIn("旅程测试简历", preview(store, file_id)["content"])
            drafts = profile_suggestions("项目：数据清洗\n我负责清洗。\n实习：运营助理\n我负责访谈。")
            profile = store.save_profile(drafts[0])
            store.link_profile(profile, stage_id=stage)
            restarted = Store(database)
            self.assertEqual(1, len(restarted.list_events()))
            self.assertEqual(1, len(restarted.list_todos()))
            self.assertEqual(1, len(restarted.list_profiles()))
            self.assertEqual(1, len([x for x in overview(restarted)["upcoming"] if x["source"] == "position"]))
            self.assertTrue(export_json(database, base / "exports").exists())
            self.assertEqual(7, len(export_csv(database, base / "exports")))
            backup = backup_database(database, base / "backups")
            clone = base / "restored.sqlite3"
            Store(clone)
            restore_database(clone, backup, base / "clone-backups")
            self.assertEqual(1, len(Store(clone).list_profiles()))
            self.assertEqual(original, document.read_bytes())


if __name__ == "__main__":
    unittest.main()
