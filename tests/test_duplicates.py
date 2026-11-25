import tempfile
import unittest
from pathlib import Path

from careerdesk.core import Store
from careerdesk.db import connect, initialize
from careerdesk.duplicates import archive_unlinked_position, position_duplicate_groups, set_group_reviewed


class PositionDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "isolated.sqlite3")

    def test_same_opening_with_different_details_is_reviewed(self):
        company = self.store.save_company({"name": "测试公司"})
        first = self.store.save_position({"company_id": company, "title": "管培生",
                                          "recruitment_project": "秋招"})
        second = self.store.save_position({"company_id": company, "title": " 管培生 ",
                                           "recruitment_project": "秋招", "jd": "另一个岗位描述"})
        groups = position_duplicate_groups(self.store)
        self.assertEqual(1, len(groups))
        self.assertFalse(groups[0]["identical"])
        self.assertEqual({first, second}, {row["id"] for row in groups[0]["positions"]})
        set_group_reviewed(self.store, groups[0]["positions"], True)
        self.assertTrue(position_duplicate_groups(self.store)[0]["reviewed"])
        self.store.save_position({"company_id": company, "title": "管培生",
                                  "recruitment_project": "秋招", "jd": "另一个岗位描述",
                                  "notes": "已更新"}, second)
        self.assertFalse(position_duplicate_groups(self.store)[0]["reviewed"])

    def test_different_department_or_city_is_not_flagged(self):
        company = self.store.save_company({"name": "太古"})
        self.store.save_position({"company_id": company, "title": "管理培训生",
                                  "recruitment_project": "2027 SMP", "department": "饮料", "city": "中国内地"})
        self.store.save_position({"company_id": company, "title": "管理培训生",
                                  "recruitment_project": "2027 SMP", "department": "海运", "city": "香港"})
        self.assertEqual([], position_duplicate_groups(self.store))

    def test_exact_duplicates_and_linked_records_are_handled_safely(self):
        company = self.store.save_company({"name": "重复公司"})
        first = self.store.save_position({"company_id": company, "title": "分析师"})
        second = self.store.save_position({"company_id": company, "title": "分析师"})
        group = position_duplicate_groups(self.store)[0]
        self.assertTrue(group["identical"])
        self.store.create_application(first)
        with self.assertRaisesRegex(ValueError, "关联"):
            archive_unlinked_position(self.store, first)
        archive_unlinked_position(self.store, second)
        self.assertEqual([first], [row["id"] for row in self.store.list_positions()])
        self.assertEqual(2, len(self.store.list_positions(include_archived=True)))
        self.assertEqual([], position_duplicate_groups(self.store))

    def test_existing_database_migrates_statuses_and_keeps_legacy_history(self):
        path = self.store.path
        con = connect(path)
        try:
            con.execute("INSERT INTO status_definitions VALUES(?,?,?,?,?,?)",
                        ("legacy_assessment", "测评/AI 面试准备", "推进中", 1, 1, 2))
            con.execute("DELETE FROM schema_migrations WHERE version=3")
            con.execute("PRAGMA user_version=2")
            con.commit()
        finally:
            con.close()
        initialize(path)
        statuses = {row["name"]: row for row in self.store.statuses(active_only=False)}
        self.assertFalse(statuses["测评/AI 面试准备"]["is_active"])
        self.assertTrue(all(statuses[name]["is_active"] for name in
                            ("测评准备", "AI 面试准备", "视频面试准备", "已测评")))
        self.assertTrue(list((path.parent / "backups").glob("*")))


if __name__ == "__main__":
    unittest.main()
