import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from careerdesk.bulk import COMPANY_COLUMNS, POSITION_COLUMNS, apply_import, existing_template_bytes, preview_import, template_bytes
from careerdesk.core import Store
from careerdesk.parsing import company_suggestions, notice_suggestions, position_suggestions, profile_suggestions


def workbook_bytes(company_rows=(), position_rows=()):
    wb = load_workbook(io.BytesIO(template_bytes()))
    for row in company_rows:
        headers = [cell.value for cell in wb["公司"][1]]
        wb["公司"].append([row.get(header, "") for header in headers] if isinstance(row, dict) else row)
    for row in position_rows:
        headers = [cell.value for cell in wb["岗位"][1]]
        wb["岗位"].append([row.get(header, "") for header in headers] if isinstance(row, dict) else row)
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


class BulkParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "isolated.sqlite3")

    def test_export_existing_records_uses_current_template_and_includes_archived(self):
        active = self.store.save_company({"name": "甲公司", "english_name": "Alpha", "research_notes": "=SUM(1,2)"})
        archived = self.store.save_company({"name": "乙公司", "industry": "制造业"})
        self.store.save_position({"company_id": active, "title": "在招岗位", "deadline_on": "2026-11-01"})
        old_position = self.store.save_position({"company_id": active, "title": "归档岗位"})
        self.store.save_position({"company_id": archived, "title": "所属公司已归档的岗位"})
        self.store.archive("positions", old_position)
        self.store.archive("companies", archived)

        exported = existing_template_bytes(self.store)
        wb = load_workbook(io.BytesIO(exported), data_only=False)
        self.assertEqual(["填写说明", "公司", "岗位"], wb.sheetnames)
        self.assertEqual(list(COMPANY_COLUMNS), [cell.value for cell in wb["公司"][1]])
        self.assertEqual(list(POSITION_COLUMNS), [cell.value for cell in wb["岗位"][1]])
        self.assertEqual(3, wb["公司"].max_row)
        self.assertEqual(4, wb["岗位"].max_row)
        company_rows = {row[0].value: row for row in wb["公司"].iter_rows(min_row=2)}
        position_rows = {row[2].value: row for row in wb["岗位"].iter_rows(min_row=2)}
        self.assertEqual("Alpha", company_rows["甲公司"][1].value)
        self.assertEqual("=SUM(1,2)", company_rows["甲公司"][6].value)
        self.assertEqual("s", company_rows["甲公司"][6].data_type)
        self.assertEqual("2026-11-01", position_rows["在招岗位"][10].value.date().isoformat())
        self.assertTrue(company_rows["乙公司"][0].font.color.rgb.endswith("666666"))
        self.assertTrue(position_rows["归档岗位"][0].font.color.rgb.endswith("666666"))
        self.assertTrue(position_rows["所属公司已归档的岗位"][0].font.color.rgb.endswith("666666"))
        self.assertEqual(2, len(preview_import(Store(Path(self.tmp.name) / "fresh.sqlite3"), exported)["companies"]))

    def test_existing_company_new_position_and_repeat(self):
        company = self.store.save_company({"name": "星河科技", "recruitment_url": "https://jobs.example.com/star"})
        content = workbook_bytes(company_rows=[{"公司名称": "星河科技", "招聘官网": "https://jobs.example.com/star/"}],
                                 position_rows=[{"公司名称": "星河科技", "岗位名称": "软件工程师", "城市": "上海", "招聘批次": "秋招"}])
        preview = preview_import(self.store, content)
        self.assertTrue(preview["companies"][0]["red"])
        self.assertEqual("沿用", preview["companies"][0]["action"])
        self.assertTrue(any("招聘官网" in issue for issue in preview["companies"][0]["issues"]))
        self.assertEqual("新增", preview["positions"][0]["action"])
        result = apply_import(self.store, preview)
        self.assertEqual(1, result["positions_added"])
        self.assertEqual(company, self.store.list_positions()[0]["company_id"])
        with self.assertRaises(ValueError):
            apply_import(self.store, preview)
        self.assertEqual(1, len(self.store.list_positions()))

    def test_recruitment_url_collision_and_batch_rollback(self):
        self.store.save_company({"name": "星河科技", "recruitment_url": "https://ats.example.com/star"})
        content = workbook_bytes(company_rows=[{"公司名称": "另一名称", "招聘官网": "https://ats.example.com/star"}],
                                 position_rows=[{"公司名称": "另一名称", "岗位名称": "岗位", "城市": "上海", "招聘批次": "秋招"}])
        preview = preview_import(self.store, content)
        self.assertEqual("跳过", preview["companies"][0]["action"])
        self.assertTrue(preview["companies"][0]["red"])
        self.assertEqual("星河科技", preview["companies"][0]["matches"][0]["data"]["name"])
        with self.assertRaises(ValueError):
            apply_import(self.store, preview)
        self.assertEqual(1, len(self.store.list_companies()))
        self.assertEqual([], self.store.list_positions())
        decisions = {("company", 2): "新增"}
        result = apply_import(self.store, preview, decisions)
        self.assertEqual(1, result["companies_added"])
        self.assertEqual(1, result["positions_added"])
        self.assertEqual(2, len(self.store.list_companies()))

    def test_different_company_paths_on_shared_platform(self):
        self.store.save_company({"name": "甲公司", "recruitment_url": "https://ats.example.com/a"})
        content = workbook_bytes(company_rows=[{"公司名称": "乙公司", "招聘官网": "https://ats.example.com/b"}])
        preview = preview_import(self.store, content)
        self.assertFalse(preview["companies"][0]["red"])

    def test_distinct_positions_can_share_an_application_link(self):
        company = self.store.save_company({"name": "星河科技"})
        shared_link = "https://jobs.example.com/apply"
        self.store.save_position({"company_id": company, "title": "已有岗位", "apply_url": shared_link})
        content = workbook_bytes(position_rows=[
            {"公司名称": "星河科技", "岗位名称": "数据分析师", "投递链接": shared_link},
            {"公司名称": "星河科技", "岗位名称": "产品经理", "投递链接": shared_link},
        ])
        preview = preview_import(self.store, content)
        self.assertEqual(["新增", "新增"], [row["action"] for row in preview["positions"]])
        self.assertFalse(any(row["red"] for row in preview["positions"]))
        result = apply_import(self.store, preview)
        self.assertEqual(2, result["positions_added"])
        self.assertEqual(3, len(self.store.list_positions()))

    def test_same_position_still_warns_even_with_different_application_link(self):
        self.store.save_company({"name": "星河科技"})
        content = workbook_bytes(position_rows=[
            {"公司名称": "星河科技", "岗位名称": "数据分析师", "招聘项目名称": "秋招", "投递链接": "https://jobs.example.com/a"},
            {"公司名称": "星河科技", "岗位名称": "数据分析师", "招聘项目名称": "秋招", "投递链接": "https://jobs.example.com/b"},
        ])
        preview = preview_import(self.store, content)
        self.assertEqual("新增", preview["positions"][0]["action"])
        self.assertEqual("跳过", preview["positions"][1]["action"])
        self.assertTrue(preview["positions"][1]["red"])
        self.assertEqual("本次岗位表第 2 行", preview["positions"][1]["matches"][0]["source"])
        self.assertEqual("https://jobs.example.com/a", preview["positions"][1]["matches"][0]["data"]["apply_url"])

    def test_text_suggestions_do_not_invent(self):
        company = company_suggestions("公司名称：星河科技\n招聘官网：https://jobs.example.com/star")
        self.assertEqual("星河科技", company["name"]["value"])
        self.assertNotIn("industry", company)
        job = position_suggestions("岗位名称：软件工程师\n截止日期：2026-11-30\n投递链接：https://jobs.example.com/42")
        self.assertEqual("2026-11-30", job["deadline_on"]["value"])
        notice = notice_suggestions("请在3天内完成测评。链接：https://test.example.com/x", "2026-10-22 14:00")
        self.assertEqual("2026-10-25 14:00", notice["deadline"]["value"])
        self.assertTrue(notice["deadline"]["inferred"])
        self.assertNotIn("deadline", notice_suggestions("请在3个工作日内完成", "2026-10-22 14:00"))
        profiles = profile_suggestions("项目：研究计划\n我做了数据清洗。\n实习：产品助理\n参与访谈。")
        self.assertEqual(2, len(profiles))
        self.assertEqual("研究计划", profiles[0]["title"])

    def test_new_fields_import_and_text_suggestions(self):
        content = workbook_bytes(
            company_rows=[{"公司名称": "星河科技", "公司英文名": "Galaxy Tech"}],
            position_rows=[{"公司名称": "星河科技", "招聘项目名称": "2027 届管培",
                            "岗位名称": "数据分析师", "专业要求": "统计学",
                            "毕业时间要求": "2027 年毕业"}])
        preview = preview_import(self.store, content)
        self.assertEqual("新增", preview["positions"][0]["action"])
        apply_import(self.store, preview)
        self.assertEqual("Galaxy Tech", self.store.list_companies()[0]["english_name"])
        position = self.store.list_positions()[0]
        self.assertEqual("2027 届管培", position["recruitment_project"])
        self.assertEqual("统计学", position["major_requirements"])
        self.assertEqual("2027 年毕业", position["graduation_time_requirement"])
        self.assertEqual("Galaxy Tech", company_suggestions("公司英文名：Galaxy Tech")["english_name"]["value"])
        suggestions = position_suggestions("招聘项目名称：2027 届管培\n专业要求：统计学\n毕业时间要求：2027 年毕业")
        self.assertEqual("2027 届管培", suggestions["recruitment_project"]["value"])
        self.assertEqual("统计学", suggestions["major_requirements"]["value"])
        self.assertEqual("2027 年毕业", suggestions["graduation_time_requirement"]["value"])

    def test_old_template_still_imports_and_english_duplicates_are_red(self):
        wb = Workbook()
        company_sheet = wb.active
        company_sheet.title = "公司"
        company_sheet.append(["公司名称", "行业", "官网", "招聘官网"])
        company_sheet.append(["旧模板公司", "科技", "", "https://old.example/jobs"])
        position_sheet = wb.create_sheet("岗位")
        position_sheet.append(["公司名称", "岗位名称", "部门", "城市", "招聘批次"])
        position_sheet.append(["旧模板公司", "工程师", "研发", "上海", "秋招"])
        output = io.BytesIO()
        wb.save(output)
        preview = preview_import(self.store, output.getvalue())
        self.assertEqual("新增", preview["positions"][0]["action"])
        apply_import(self.store, preview)
        self.assertIsNone(self.store.list_positions()[0]["recruitment_project"])
        self.store.save_company({"name": "另一公司", "english_name": "Same English"})
        content = workbook_bytes(company_rows=[{"公司名称": "第三公司", "公司英文名": "same english"}])
        duplicate = preview_import(self.store, content)["companies"][0]
        self.assertTrue(duplicate["red"])
        self.assertEqual("跳过", duplicate["action"])
        self.assertTrue(any("英文名" in issue for issue in duplicate["issues"]))


if __name__ == "__main__":
    unittest.main()
