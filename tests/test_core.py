import tempfile
import unittest
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from careerdesk.core import Store
from careerdesk.dashboard import overview, urgency
from careerdesk.timeutil import SHANGHAI


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "isolated.sqlite3"
        self.store = Store(self.path)

    def _company_position(self):
        company = self.store.save_company({"name": " 星河科技 "})
        position = self.store.save_position({"company_id": company, "title": "软件工程师", "recruitment_batch": "秋招"})
        return company, position

    def test_companies_positions_archives_and_persistence(self):
        company, position = self._company_position()
        with self.assertRaises(ValueError):
            self.store.save_company({"name": "星河科技"})
        second = self.store.save_position({"company_id": company, "title": "软件工程师", "recruitment_batch": "春招"})
        self.assertEqual(2, len(self.store.list_positions()))
        self.store.archive("companies", company)
        self.assertEqual([], self.store.list_positions())
        self.assertEqual(2, len(self.store.list_positions(include_archived=True)))
        self.store.archive("positions", second)
        self.store.archive("companies", company, archived=False)
        self.assertEqual([position], [p["id"] for p in self.store.list_positions()])
        restarted = Store(self.path)
        self.assertEqual("星河科技", restarted.get("companies", company)["name"])

    def test_new_company_and_position_details_persist_and_search(self):
        company = self.store.save_company({"name": "星河科技", "english_name": "Galaxy Tech"})
        position = self.store.save_position({"company_id": company, "title": "数据分析师",
                                             "recruitment_project": "2027 届管培项目",
                                             "major_requirements": "统计学或计算机",
                                             "graduation_time_requirement": "2027 年毕业"})
        self.assertEqual("Galaxy Tech", self.store.get("companies", company)["english_name"])
        self.assertEqual("2027 届管培项目", self.store.get("positions", position)["recruitment_project"])
        self.assertEqual("统计学或计算机", self.store.get("positions", position)["major_requirements"])
        self.assertEqual("2027 年毕业", self.store.get("positions", position)["graduation_time_requirement"])
        self.assertEqual(company, self.store.list_companies(search="Galaxy")[0]["id"])
        self.assertEqual(position, self.store.list_positions(search="2027 届管培")[0]["id"])

    def test_unrelated_database_is_not_modified(self):
        alien = Path(self.tmp.name) / "other.sqlite3"
        with closing(sqlite3.connect(alien)) as con:
            con.execute("CREATE TABLE private_notes (text TEXT)")
            con.execute("INSERT INTO private_notes VALUES ('保留原文')")
            con.commit()
        with self.assertRaises(RuntimeError):
            Store(alien)
        with closing(sqlite3.connect(alien)) as con:
            self.assertEqual("保留原文", con.execute("SELECT text FROM private_notes").fetchone()[0])
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='companies'").fetchone())

    def test_applications_status_history_and_unknown_date(self):
        _, position = self._company_position()
        first = self.store.create_application(position, "第一次")
        second = self.store.create_application(position, "第二次")
        self.assertNotEqual(first, second)
        self.assertEqual(1, len(self.store.history(first)))
        submitted = next(s for s in self.store.statuses() if s["name"] == "已投递")
        with self.assertRaises(ValueError):
            self.store.change_status(first, submitted["id"])
        self.assertEqual(1, len(self.store.history(first)))
        self.assertEqual("待研究", self.store.list_applications()[0]["status_name"])
        self.assertTrue(self.store.change_status(first, submitted["id"], submission_precision="明确未知"))
        self.assertEqual(2, len(self.store.history(first)))
        self.store.update_application(first, round_label="首次申请", notes="只改备注")
        self.assertEqual(2, len(self.store.history(first)))
        self.assertFalse(self.store.change_status(first, submitted["id"]))
        self.assertEqual(2, len(self.store.history(first)))
        self.assertEqual(0, overview(self.store)["submitted"])
        assessed = next(s for s in self.store.statuses() if s["name"] == "测评准备")
        self.store.change_status(first, assessed["id"], submission_precision="日期", submitted_on="2026-10-20")
        self.assertEqual(1, overview(self.store)["submitted"])
        stage = self.store.get("applications", first)["current_stage_id"]
        self.assertEqual({"通知时间", "测评截止时间"}, {n["label"] for n in self.store.nodes(stage)})
        self.store.save_node(stage, "测评截止时间", "截止", "日期", "2026-10-30")
        self.assertEqual("2026-10-30", next(n for n in self.store.nodes(stage) if n["label"] == "测评截止时间")["date_on"])
        closed = next(s for s in self.store.statuses() if s["name"] == "已关闭")
        self.store.change_status(first, closed["id"], submission_precision="明确未知")
        self.assertEqual("2026-10-20", self.store.get("applications", first)["submitted_on"])
        self.assertEqual(0, overview(self.store)["progressing"])

    def test_create_submission_records_date_and_history_atomically(self):
        _, position = self._company_position()
        with self.assertRaises(ValueError):
            self.store.create_submission(position, precision="日期")
        self.assertEqual([], self.store.list_applications())
        application = self.store.create_submission(position, "首次投递", "网申完成",
                                                   precision="日期", submitted_on="2026-10-23")
        row = self.store.get("applications", application)
        self.assertEqual("2026-10-23", row["submitted_on"])
        self.assertEqual("日期", row["submission_precision"])
        self.assertEqual("已投递", self.store.list_applications()[0]["status_name"])
        self.assertEqual(1, len(self.store.history(application)))
        self.assertEqual(1, len(self.store.stages(application)))

    def test_repeated_application_details_do_not_create_duplicate_records(self):
        _, position = self._company_position()
        first = self.store.create_submission(position, "2027 秋招", precision="日期", submitted_on="2026-10-18")
        with self.assertRaisesRegex(ValueError, "已有申请"):
            self.store.create_submission(position, " 2027   秋招 ", "不同备注",
                                         precision="日期", submitted_on="2026-10-18")
        self.assertEqual([first], [row["id"] for row in self.store.list_applications()])
        self.store.create_submission(position, "2028 春招", precision="日期", submitted_on="2026-10-18")
        self.store.create_submission(position, "2027 秋招", precision="日期", submitted_on="2026-10-19")
        with self.assertRaisesRegex(ValueError, "已有申请"):
            self.store.create_application(position, "2027 秋招")
        self.store.create_application(position, "另一轮")
        with self.assertRaisesRegex(ValueError, "已有申请"):
            self.store.create_application(position, " 另一轮 ")

    def test_status_change_saves_stage_time_atomically(self):
        _, position = self._company_position()
        application = self.store.create_submission(position, precision="日期", submitted_on="2026-10-18")
        assessed = next(s for s in self.store.statuses() if s["name"] == "已测评")
        with self.assertRaises(ValueError):
            self.store.change_status(application, assessed["id"], stage_times=[
                {"label": "测评完成时间", "kind": "完成", "precision": "日期", "value": "不合法日期"}])
        self.assertEqual("已投递", self.store.list_applications()[0]["status_name"])
        self.assertEqual(1, len(self.store.stages(application)))
        self.store.change_status(application, assessed["id"], stage_times=[
            {"label": "测评完成时间", "kind": "完成", "precision": "日期", "value": "2026-10-23"}])
        nodes = self.store.nodes(self.store.get("applications", application)["current_stage_id"])
        self.assertEqual("2026-10-23", next(n for n in nodes if n["label"] == "测评完成时间")["date_on"])
        self.assertEqual("2026-10-18", self.store.get("applications", application)["submitted_on"])
        custom = self.store.add_status("复核", "推进中", [("状态发生时间", "完成")])
        self.store.change_status(application, custom, stage_times=[
            {"label": "状态发生时间", "kind": "完成", "precision": "日期", "value": "2026-10-25"}])
        custom_nodes = self.store.nodes(self.store.get("applications", application)["current_stage_id"])
        self.assertEqual(1, len(custom_nodes))
        self.assertEqual("2026-10-25", custom_nodes[0]["date_on"])

    def test_custom_status_and_invalid_relations(self):
        company, position = self._company_position()
        other = self.store.save_company({"name": "另一家公司"})
        app = self.store.create_application(position)
        status_id = self.store.add_status("主管面试", "推进中", [("通知时间", "通知"), ("面试时间", "安排")])
        with self.assertRaises(ValueError):
            self.store.change_status(app, status_id)
        self.store.change_status(app, status_id, submission_precision="明确未知")
        self.assertEqual("推进中", next(s for s in self.store.statuses() if s["id"] == status_id)["category"])
        with self.assertRaises(ValueError):
            self.store.save_todo({"title": "准备资料", "company_id": other, "position_id": position})
        with self.assertRaises(ValueError):
            self.store.save_event({"title": "测评", "type": "测评", "company_id": company,
                                   "application_id": app, "stage_id": self.store.get("applications", app)["current_stage_id"]})
        self.store.archive("applications", app)
        self.assertEqual(0, overview(self.store)["progressing"])

    def test_todo_dates_and_profile(self):
        company, position = self._company_position()
        fixed = datetime(2026, 10, 22, 12, 0, tzinfo=SHANGHAI)
        self.assertNotEqual("已逾期", urgency("2026-10-22", current=fixed)["label"])
        self.assertEqual("已逾期", urgency("2026-10-21", current=fixed)["label"])
        self.assertEqual("时间未知", urgency(current=fixed)["label"])
        self.assertEqual("已完成/取消", urgency("2026-10-21", completed=True, current=fixed)["label"])
        todo = self.store.save_todo({"title": "练习题", "company_id": company, "position_id": position,
                                     "precision": "日期", "due_on": "2026-10-21"})
        self.assertEqual(1, overview(self.store, fixed)["overdue_todos"])
        self.store.complete_todo(todo)
        self.assertEqual(0, overview(self.store, fixed)["overdue_todos"])
        profile = self.store.save_profile({"category": "项目", "title": "科研项目", "body": "我负责实验设计"})
        self.store.archive("profiles", profile)
        self.assertEqual([], self.store.list_profiles())
        self.store.archive("profiles", profile, archived=False)
        self.assertEqual(1, len(self.store.list_profiles()))

    def test_assessment_notice_event_and_nodes_commit_together(self):
        company, position = self._company_position()
        app = self.store.create_application(position)
        assessed = next(s for s in self.store.statuses() if s["name"] == "测评准备")
        self.store.change_status(app, assessed["id"], submission_precision="明确未知")
        stage = self.store.get("applications", app)["current_stage_id"]
        data = {"company_id": company, "position_id": position, "application_id": app,
                "stage_id": stage, "title": "在线测评", "type": "测评", "status": "已安排",
                "precision": "时间", "event_at_utc": "2026-10-25 18:00", "requirements": "完成逻辑题"}
        with self.assertRaises(ValueError):
            self.store.save_event(data, stage_nodes=[
                {"label": "通知时间", "kind": "通知", "precision": "时间", "value": "2026-10-22 18:00"},
                {"label": "测评截止时间", "kind": "截止", "precision": "时间", "value": "错误时间"}])
        self.assertEqual([], self.store.list_events())
        self.assertTrue(all(n["precision"] == "未知" for n in self.store.nodes(stage)))
        event_id = self.store.save_event(data, stage_nodes=[
            {"label": "通知时间", "kind": "通知", "precision": "时间", "value": "2026-10-22 18:00"},
            {"label": "测评截止时间", "kind": "截止", "precision": "时间", "value": "2026-10-25 18:00"}])
        self.assertEqual(1, len(self.store.list_events()))
        self.assertIsNotNone(self.store.get("recruitment_events", event_id)["node_id"])
        self.assertEqual("2026-10-25 18:00", datetime.fromisoformat(
            self.store.list_events()[0]["deadline_at_utc"]).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M"))
        fixed = datetime(2026, 10, 24, 12, 0, tzinfo=SHANGHAI)
        self.assertEqual(1, len([x for x in overview(self.store, fixed)["upcoming"] if "测评" in x["title"]]))

    def test_same_day_event_and_stage_node_share_one_reminder_and_event_can_be_deleted(self):
        company, position = self._company_position()
        application = self.store.create_submission(position, precision="日期", submitted_on="2026-10-22")
        status = next(row["id"] for row in self.store.statuses() if row["name"] == "测评准备")
        self.store.change_status(application, status)
        stage = self.store.get("applications", application)["current_stage_id"]
        self.store.save_node(stage, "测评截止时间", "截止", "时间", "2026-10-26 16:32")
        event = self.store.save_event({"company_id": company, "position_id": position,
                                      "application_id": application, "stage_id": stage,
                                      "title": "在线测评", "type": "测评", "status": "已安排",
                                      "precision": "日期", "event_on": "2026-10-26",
                                      "location_or_link": "https://example.test/quiz"})
        fixed = datetime(2026, 10, 25, 12, 0, tzinfo=SHANGHAI)
        reminders = [item for item in overview(self.store, fixed)["upcoming"]
                     if item.get("stage_id") == stage]
        self.assertEqual(1, len(reminders))
        self.assertEqual("application", reminders[0]["source"])
        self.assertEqual(event, reminders[0]["linked_event_id"])
        self.assertIn("在线测评", reminders[0]["title"])
        self.store.delete_event(event)
        self.assertEqual([], self.store.list_events())
        self.assertEqual(1, len([node for node in self.store.nodes(stage)
                                 if node["label"] == "测评截止时间" and node["precision"] == "时间"]))
        self.assertEqual(1, len([item for item in overview(self.store, fixed)["upcoming"]
                                 if item.get("stage_id") == stage]))

    def test_completed_assessment_stage_no_longer_appears_as_pending(self):
        company, position = self._company_position()
        application = self.store.create_submission(position, precision="日期", submitted_on="2026-10-22")
        status_ids = {row["name"]: row["id"] for row in self.store.statuses()}
        self.store.change_status(application, status_ids["测评准备"])
        assessment_stage = self.store.get("applications", application)["current_stage_id"]
        self.store.save_node(assessment_stage, "测评截止时间", "截止", "日期", "2026-10-29")
        old_event = self.store.save_event({"company_id": company, "position_id": position,
                                           "application_id": application, "stage_id": assessment_stage,
                                           "title": "线上测评详情", "type": "测评", "status": "已安排",
                                           "precision": "日期", "event_on": "2026-10-30"})
        fixed = datetime(2026, 10, 28, 12, 0, tzinfo=SHANGHAI)
        self.assertTrue(any(item.get("stage_id") == assessment_stage
                            for item in overview(self.store, fixed)["upcoming"]))

        self.store.change_status(application, status_ids["已测评"], stage_times=[
            {"label": "测评完成时间", "kind": "完成", "precision": "日期", "value": "2026-10-28"}])
        current_stage = self.store.get("applications", application)["current_stage_id"]
        self.store.save_node(current_stage, "下一轮安排", "安排", "日期", "2026-10-31")
        reminders = overview(self.store, fixed)["upcoming"] + overview(self.store, fixed)["overdue"]
        self.assertFalse(any(item.get("stage_id") == assessment_stage for item in reminders))
        self.assertTrue(any(item.get("stage_id") == current_stage for item in reminders))
        self.assertEqual("2026-10-29", next(node for node in self.store.nodes(assessment_stage)
                                             if node["label"] == "测评截止时间")["date_on"])
        self.assertEqual(old_event, self.store.list_events()[0]["id"])

    def test_position_reminders_distinguish_company_and_position_submission_history(self):
        new_company = self.store.save_company({"name": "尚未投递公司"})
        new_position = self.store.save_position({"company_id": new_company, "title": "新岗位",
                                                 "deadline_on": "2026-10-28"})
        self.store.create_application(new_position)  # A draft is not a submission.
        known_company = self.store.save_company({"name": "已投递其他岗位公司"})
        known_position = self.store.save_position({"company_id": known_company, "title": "新机会",
                                                   "deadline_on": "2026-10-27"})
        previous_position = self.store.save_position({"company_id": known_company, "title": "已投的旧岗位"})
        old_application = self.store.create_submission(previous_position, precision="明确未知")
        self.store.archive("applications", old_application)
        applied_company = self.store.save_company({"name": "岗位已投公司"})
        applied_position = self.store.save_position({"company_id": applied_company, "title": "已投岗位",
                                                     "deadline_on": "2026-10-26"})
        self.store.create_submission(applied_position, precision="日期", submitted_on="2026-10-24")
        reminders = {item["id"]: item for item in overview(
            self.store, datetime(2026, 10, 25, 12, 0, tzinfo=SHANGHAI))["upcoming"]
            if item["source"] == "position"}
        self.assertEqual(0, reminders[new_position]["application_rank"])
        self.assertEqual(1, reminders[known_position]["application_rank"])
        self.assertEqual(2, reminders[applied_position]["application_rank"])


if __name__ == "__main__":
    unittest.main()
