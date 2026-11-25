import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest
from streamlit.runtime.uploaded_file_manager import UploadedFile, UploadedFileRec

from careerdesk.bulk import preview_import
from careerdesk.core import Store
from careerdesk.timeutil import SHANGHAI
from tests.test_bulk_parsing import workbook_bytes


class UiFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous = os.environ.get("CAREERDESK_DATA_DIR")
        os.environ["CAREERDESK_DATA_DIR"] = self.tmp.name
        self.addCleanup(self._restore_env)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30).run()

    def _restore_env(self):
        if self.previous is None:
            os.environ.pop("CAREERDESK_DATA_DIR", None)
        else:
            os.environ["CAREERDESK_DATA_DIR"] = self.previous

    def _navigate(self, page):
        self.app.sidebar.radio[0].set_value(page).run()
        self.assertFalse(self.app.exception, [x.message for x in self.app.exception])

    def _input(self, label):
        return next(x for x in self.app.text_input if x.label == label)

    def _button(self, label):
        return next(x for x in self.app.button if x.label == label)

    def test_company_position_application_and_other_pages(self):
        self._navigate("招聘信息")
        self._button("＋ 新增公司").click().run()
        self._input("公司名称 *").set_value("界面测试公司")
        self._input("公司英文名").set_value("Interface Test Co.")
        self._button("保存公司").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any(x.label == "＋ 新增公司" for x in self.app.button))
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        self.assertEqual(1, len(store.list_companies()))
        self.assertEqual("Interface Test Co.", store.list_companies()[0]["english_name"])
        self._button("我要投递").click().run()
        self._button("＋ 新增岗位").click().run()
        self._input("招聘项目名称").set_value("2027 届校园招聘")
        self._input("岗位名称 *").set_value("界面测试岗位")
        self._input("专业要求").set_value("不限")
        self._input("毕业时间要求").set_value("2027 年毕业")
        self._button("保存岗位").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any(x.label == "＋ 新增岗位" for x in self.app.button))
        self.assertEqual(1, len(store.list_positions()))
        self.assertEqual("2027 届校园招聘", store.list_positions()[0]["recruitment_project"])
        self._navigate("申请进度")
        self._button("＋ 建立申请").click().run()
        self._button("建立申请").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(1, len(store.list_applications()))
        for page in ("求职总览", "文件中心", "个人资料库", "设置与备份"):
            self._navigate(page)

    def test_ai_trial_stays_local_until_key_and_positions_are_selected(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "试用公司"})
        store.save_position({"company_id": company, "title": "试用岗位"})
        self._navigate("AI 试用")
        self.assertFalse(self.app.exception)
        self._input("先搜索要分析的公司或岗位").set_value("试用公司").run()
        self.assertTrue(any(x.label == "选择岗位（最多 3 条）" for x in self.app.multiselect))
        self.assertTrue(self._button("发送所选岗位并分析").disabled)

    def test_overview_item_opens_original_position(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "导航测试公司"})
        position = store.save_position({"company_id": company, "title": "导航测试岗位",
                                        "deadline_on": (date.today() + timedelta(days=2)).isoformat()})
        self._navigate("求职总览")
        self._button("查看").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("招聘信息", self.app.sidebar.radio[0].value)
        self.assertEqual(position, next(x for x in self.app.selectbox if x.label == "新增或选择岗位").value)
        self._button("← 返回求职总览").click().run()
        self.assertEqual("求职总览", self.app.sidebar.radio[0].value)

    def test_overview_position_detail_can_locate_job_and_start_submission(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "待投递公司"})
        target = store.save_position({"company_id": company, "title": "目标岗位",
                                      "deadline_on": (date.today() + timedelta(days=2)).isoformat()})
        self._navigate("求职总览")
        self._button("查看").click().run()
        for index in range(12):
            store.save_position({"company_id": company, "title": f"其他岗位{index:02d}"})
        self._button("我要投递").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(target, self.app.session_state["position_focus_id"])
        self.assertTrue(any("岗位名录" in x.value for x in self.app.title))
        self.assertTrue(any("刚查看的岗位已排在最前" in x.value for x in self.app.caption))
        titles = [x.value for x in self.app.markdown if 'class="cd-position-title"' in x.value]
        self.assertEqual(10, len(titles))
        self.assertIn("目标岗位", titles[0])
        self.assertEqual([], store.list_applications())
        self._button("我要投递").click().run()
        self.assertEqual("申请进度", self.app.sidebar.radio[0].value)
        self.assertEqual(target, next(x for x in self.app.selectbox if x.label == "所属岗位 *").value)

    def test_overview_metric_lists_match_counts_and_open_application(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "清单测试公司"})
        submitted_position = store.save_position({"company_id": company, "title": "已投递岗位"})
        progressing_position = store.save_position({"company_id": company, "title": "推进中岗位"})
        ended_position = store.save_position({"company_id": company, "title": "已结束岗位"})
        submitted_id = store.create_submission(submitted_position, submitted_on="2026-10-22")
        progressing_id = store.create_application(progressing_position)
        ended_id = store.create_submission(ended_position, submitted_on="2026-10-21")
        statuses = {row["name"]: row["id"] for row in store.statuses()}
        store.change_status(progressing_id, statuses["测评准备"], submission_precision="明确未知")
        store.change_status(ended_id, statuses["已拒绝"])

        self._navigate("申请进度")
        self.assertTrue(any("未填写" in x.label for x in self.app.button))
        self.assertFalse(any("状态变更：" in x.value for x in self.app.markdown))
        self._navigate("求职总览")
        submitted_card = next(x for x in self.app.button if x.label.startswith("已投递申请 ↗"))
        self.assertIn("**2**", submitted_card.label)
        self.assertIn("**2**", next(x for x in self.app.button if x.label.startswith("正在推进 ↗")).label)
        submitted_card.click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("已投递岗位" in x.value for x in self.app.markdown))
        self.assertTrue(any("已结束岗位" in x.value for x in self.app.markdown))
        self.assertFalse(any("推进中岗位" in x.value for x in self.app.markdown))
        self.assertEqual(2, len([x for x in self.app.button if x.label == "查看申请详情"]))

        self._button("← 返回求职总览").click().run()
        next(x for x in self.app.button if x.label.startswith("正在推进 ↗")).click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("已投递岗位" in x.value for x in self.app.markdown))
        self.assertTrue(any("推进中岗位" in x.value for x in self.app.markdown))
        self.assertFalse(any("已结束岗位" in x.value for x in self.app.markdown))
        self._button("查看申请详情").click().run()
        self.assertEqual("申请进度", self.app.sidebar.radio[0].value)
        self.assertIn(self.app.session_state["application_selected"], (submitted_id, progressing_id))
        self.assertTrue(any(x.label == "改变申请状态" for x in self.app.button))
        self._button("← 返回正在推进").click().run()
        self.assertEqual("求职总览", self.app.sidebar.radio[0].value)
        self.assertTrue(any(x.label == "查看申请详情" for x in self.app.button))

    def test_submission_details_are_visible_and_new_application_is_a_separate_page(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "备注公司"})
        position = store.save_position({"company_id": company, "title": "备注岗位"})
        store.create_submission(position, "秋招首轮", "官网投递\n邮件确认<已收到>",
                                precision="日期", submitted_on="2026-10-22")
        self._navigate("申请进度")
        next(x for x in self.app.button if "备注公司" in x.label).click().run()
        details = [x.value for x in self.app.markdown]
        self.assertTrue(any("投递记录" in value for value in details))
        self.assertTrue(any("官网投递<br>邮件确认&lt;已收到&gt;" in value for value in details))
        self.assertTrue(any("秋招首轮" in x.value for x in self.app.markdown))
        self.assertTrue(any("2026-10-22" in x.value for x in self.app.markdown))
        self.assertTrue(any(x.label == "查看状态历史与变更备注" for x in self.app.expander))
        self._button("← 返回申请列表").click().run()
        self._button("＋ 建立申请").click().run()
        self.assertFalse(any(x.label.startswith("备注公司　｜") for x in self.app.button))
        self.assertTrue(any(x.label == "所属岗位 *" for x in self.app.selectbox))
        self._button("返回申请列表").click().run()
        self.assertTrue(any(x.label.startswith("备注公司　｜") for x in self.app.button))

    def test_text_suggestions_preserve_manual_entry(self):
        self._navigate("招聘信息")
        self._button("＋ 新增公司").click().run()
        self._input("公司名称 *").set_value("手动填写公司")
        next(x for x in self.app.text_area if x.label.startswith("在这里粘贴文字")).set_value(
            "公司名称：解析建议公司\n行业：科技")
        self._button("解析文字").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("手动填写公司", self._input("公司名称 *").value)
        self.assertEqual("科技", self._input("行业").value)
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        self.assertEqual([], store.list_companies())

    def test_empty_pages_and_backup_button(self):
        for page in ("求职总览", "招聘信息", "申请进度",
                     "文件中心", "个人资料库", "设置与备份"):
            self._navigate(page)
        self._button("创建一致性备份").click().run()
        self.assertFalse(self.app.exception)
        backups = list((Path(self.tmp.name) / "backups").glob("careerdesk-*.sqlite3"))
        self.assertEqual(1, len(backups))

    def test_recruitment_page_offers_one_click_existing_template_export(self):
        self._navigate("招聘信息")
        self.assertTrue(any(button.label == "导出已有公司与岗位" for button in self.app.get("download_button")))

    def test_existing_position_duplicate_review_opens_and_marks_distinct_records(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "核对公司"})
        store.save_position({"company_id": company, "title": "管培生", "recruitment_project": "秋招"})
        store.save_position({"company_id": company, "title": "管培生", "recruitment_project": "秋招", "jd": "不同介绍"})
        self._navigate("招聘信息")
        self._button("检查已有岗位重复").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("已有岗位重复检查" in item.value for item in self.app.title))
        self.assertTrue(any("1 组同一招聘岗位的重复候选" in item.value for item in self.app.info))
        self.assertTrue(any("核对公司" in item.label for item in self.app.expander))
        self._button("查看／编辑岗位").click().run()
        self.assertTrue(any("← 返回重复检查" == item.label for item in self.app.button))
        self._button("← 返回重复检查").click().run()
        self.assertTrue(any("已有岗位重复检查" in item.value for item in self.app.title))
        self._button("确认这些是不同岗位，全部保留").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("0 组待核对" in item.value for item in self.app.info))
        self._button("← 返回招聘信息").click().run()
        self.assertTrue(any(item.label == "检查已有岗位重复" for item in self.app.button))

    def test_duplicate_review_can_archive_an_unlinked_candidate(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "归档核对公司"})
        store.save_position({"company_id": company, "title": "同名岗位"})
        store.save_position({"company_id": company, "title": "同名岗位"})
        self._navigate("招聘信息")
        self._button("检查已有岗位重复").click().run()
        self._button("归档这条岗位").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(1, len(store.list_positions()))
        self.assertTrue(any("0 组同一招聘岗位的重复候选" in item.value for item in self.app.info))

    def test_large_import_preview_keeps_one_confirm_button_and_collapses_routine_rows(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        store.save_company({"name": "批量导入测试公司"})
        position_rows = [{"公司名称": "批量导入测试公司", "岗位名称": f"岗位{i}"}
                         for i in range(39)]
        position_rows.append({"公司名称": "批量导入测试公司", "岗位名称": "岗位0"})
        self.app.session_state["import_preview"] = preview_import(
            store, workbook_bytes(position_rows=position_rows))
        self._navigate("招聘信息")
        self.assertTrue(any("普通条目（39 条" in x.label for x in self.app.expander))
        self.assertEqual(1, len([x for x in self.app.selectbox if "行处理方式" in x.label]))
        self.assertTrue(any("两条记录详细对照" in x.value for x in self.app.markdown))
        self.assertTrue(any("本次岗位表第 2 行" in x.value for x in self.app.markdown))
        self._button("确认整批导入").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(39, len(store.list_positions()))
        self.assertNotIn("import_preview", self.app.session_state)
        self.assertEqual(1, self.app.session_state["import_upload_serial"])
        self.assertTrue(any("导入成功" in item.value and "新增岗位 39 个" in item.value
                            for item in self.app.success))

    def test_failed_import_keeps_preview_and_shows_error(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        content = workbook_bytes(company_rows=[{"公司名称": "已导入公司"}])
        from careerdesk.bulk import apply_import
        apply_import(store, preview_import(store, content))
        self.app.session_state["import_preview"] = preview_import(store, content)
        self._navigate("招聘信息")
        self._button("确认整批导入").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(1, len(store.list_companies()))
        self.assertIn("import_preview", self.app.session_state)
        self.assertTrue(any("导入失败" in item.value and "已经导入过" in item.value
                            for item in self.app.error))

    def test_previous_successful_upload_is_cleared_on_next_run(self):
        from careerdesk.bulk import apply_import
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        content = workbook_bytes(company_rows=[{"公司名称": "旧版导入公司"}])
        apply_import(store, preview_import(store, content))
        self.app.session_state["company_position_import"] = UploadedFile(
            UploadedFileRec("previous-upload", "previous.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", content),
            None)
        self._navigate("招聘信息")
        self.assertFalse(self.app.exception)
        self.assertEqual(1, self.app.session_state["import_upload_serial"])
        self.assertTrue(any("已于" in item.value and "导入成功" in item.value for item in self.app.success))

    def test_import_errors_explain_why_confirmation_is_disabled(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        self.app.session_state["import_preview"] = preview_import(
            store, workbook_bytes(
                company_rows=[{"公司名称": "测试公司"}],
                position_rows=[{"公司名称": "测试公司", "岗位名称": "测试岗位",
                                "截止日期": "2026-10-31 18:00"}]))
        self._navigate("招聘信息")
        self.assertTrue(self._button("确认整批导入").disabled)
        self.assertTrue(any("岗位表第 2 行" in item.value and "暂不能确认导入" in item.value
                            for item in self.app.error))
        self.assertTrue(any("只接受 YYYY-MM-DD" in item.value for item in self.app.caption))

    def test_company_and_position_cards_open_prefilled_submission(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "卡片测试公司"})
        position = store.save_position({"company_id": company, "title": "产品岗位",
                                        "recruitment_batch": "秋招"})
        self._navigate("招聘信息")
        self._button("我要投递").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(company, self.app.session_state["company_catalog_id"])
        self.assertTrue(any('class="cd-position-title"' in x.value and "产品岗位" in x.value
                            for x in self.app.markdown))
        self.assertFalse(any(x.label == "产品岗位" for x in self.app.button))
        self._button("我要投递").click().run()
        self.assertEqual("申请进度", self.app.sidebar.radio[0].value)
        self.assertEqual(position, next(x for x in self.app.selectbox if x.label == "所属岗位 *").value)
        self._button("← 返回岗位名录").click().run()
        self.assertEqual("招聘信息", self.app.sidebar.radio[0].value)
        self.assertTrue(any('class="cd-position-title"' in x.value and "产品岗位" in x.value
                            for x in self.app.markdown))
        self.assertTrue(any(x.label == "查看／编辑岗位" for x in self.app.button))
        self.assertTrue(any(x.label == "删除岗位" for x in self.app.button))
        self._button("我要投递").click().run()
        self._button("保存投递事件").click().run()
        self.assertFalse(self.app.exception)
        applications = store.list_applications()
        self.assertEqual(1, len(applications))
        self.assertEqual("已投递", applications[0]["status_name"])

    def test_company_directory_sorts_pinyin_and_shows_requested_details(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        for name in ("星河", "阿里", "百度", "优衣库"):
            store.save_company({"name": name, "english_name": f"{name} English",
                                "industry": "零售", "cities": "上海"})
        self._navigate("招聘信息")
        headings = [x.value for x in self.app.markdown if 'class="cd-company-heading"' in x.value]
        self.assertEqual(4, len(headings))
        self.assertIn("阿里", headings[0])
        self.assertIn("百度", headings[1])
        self.assertIn("星河", headings[2])
        self.assertIn("优衣库", headings[3])
        self.assertTrue(all("English" in value and "零售" in value for value in headings))
        self.assertEqual(4, len([x for x in self.app.markdown if 'class="cd-company-city"' in x.value and "上海" in x.value]))
        self.assertEqual(4, len([x for x in self.app.button if x.label == "我要投递"]))
        self.assertEqual(4, len([x for x in self.app.button if x.label == "查看/编辑资料"]))
        self._button("我要投递").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("阿里 · 岗位名录" in x.value for x in self.app.title))
        self.assertTrue(any(x.label == "＋ 新增岗位" for x in self.app.button))
        self._button("← 返回公司列表").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(4, len([x for x in self.app.button if x.label == "我要投递"]))

    def test_imported_priority_labels_remain_editable(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "特殊评级公司", "priority": "S-必投（AI评估）"})
        store.save_position({"company_id": company, "title": "分析岗位",
                             "priority": "S-必投（AI评估）", "interest": "非常感兴趣"})
        self._navigate("招聘信息")
        self._button("查看/编辑资料").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("S-必投（AI评估）",
                         next(x for x in self.app.selectbox if x.label == "关注优先级").value)
        self._button("← 返回招聘信息").click().run()
        self._button("我要投递").click().run()
        self._button("查看／编辑岗位").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("S-必投（AI评估）",
                         next(x for x in self.app.selectbox if x.label == "申请优先级").value)

    def test_card_pagination_filters_and_reversible_position_delete(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        companies = [store.save_company({"name": f"翻页公司{i:02d}"}) for i in range(12)]
        self._navigate("招聘信息")
        self.assertEqual(10, len([x for x in self.app.button if x.label == "我要投递"]))
        self._button("下一页 →").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(2, len([x for x in self.app.button if x.label == "我要投递"]))
        self._button("← 上一页").click().run()
        self.assertEqual(10, len([x for x in self.app.button if x.label == "我要投递"]))

        for i in range(10):
            store.save_position({"company_id": companies[0], "title": f"翻页岗位{i:02d}",
                                 "deadline_on": (date.today() + timedelta(days=5)).isoformat()})
        expired = store.save_position({"company_id": companies[0], "title": "逾期岗位",
                                       "deadline_on": (date.today() - timedelta(days=1)).isoformat()})
        self._button("我要投递").click().run()
        self.assertEqual(10, len([x for x in self.app.button if x.label == "我要投递"]))
        self._button("下一页 →").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(1, len([x for x in self.app.button if x.label == "我要投递"]))
        next(x for x in self.app.checkbox if x.label == "不显示已逾期岗位").set_value(True).run()
        self.assertFalse(self.app.exception)
        self.assertEqual(10, len([x for x in self.app.button if x.label == "我要投递"]))
        self.assertFalse(any("逾期岗位" in x.value for x in self.app.markdown))
        next(x for x in self.app.selectbox if x.label == "排序").set_value("岗位名称").run()
        self._button("删除岗位").click().run()
        self.assertEqual(10, len(store.list_positions()))
        self.assertEqual(11, len(store.list_positions(include_archived=True)))
        removed_title = next(row["title"] for row in store.list_positions(include_archived=True)
                             if row["archived_at_utc"])
        next(x for x in self.app.checkbox if x.label == "查看已删除岗位").set_value(True).run()
        next(x for x in self.app.text_input if x.label == "搜索这家公司的岗位").set_value(removed_title).run()
        self.assertTrue(any(x.label == "恢复岗位" for x in self.app.button))
        self.assertIsNotNone(store.get("positions", expired))

    def test_application_list_click_opens_detail_then_editor_without_banner(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "申请直达公司"})
        position = store.save_position({"company_id": company, "title": "申请直达岗位"})
        application = store.create_submission(position, precision="日期", submitted_on="2026-10-22")
        self._navigate("申请进度")
        self.assertTrue(any("申请直达公司" in x.label for x in self.app.button))
        next(x for x in self.app.button if "申请直达公司" in x.label).click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(application, self.app.session_state["application_selected"])
        self.assertTrue(any(x.label == "改变申请状态" for x in self.app.button))
        self.assertTrue(any(x.label == "添加／编辑时间节点" for x in self.app.button))
        self.assertTrue(any(x.label == "阶段详情与材料" for x in self.app.button))
        self.assertTrue(any(x.label == "修改申请资料" for x in self.app.button))
        self.assertFalse(any(x.label == "搜索申请对应的公司、岗位或批次" for x in self.app.text_input))
        self.assertFalse(any('<div class="cd-banner">' in x.value for x in self.app.markdown))
        self._button("修改申请资料").click().run()
        self.assertTrue(any(x.label == "申请轮次/批次" for x in self.app.text_input))
        self._button("← 返回申请详情").click().run()
        self.assertTrue(any(x.label == "改变申请状态" for x in self.app.button))
        self._button("← 返回申请列表").click().run()
        self.assertTrue(any(x.label == "搜索申请对应的公司、岗位或批次" for x in self.app.text_input))

    def test_application_list_sorts_company_names_and_paginates(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        names = ("优衣库", "星河", "索尼", "农夫", "理想", "科大", "辉瑞", "德意志",
                 "达能", "波士顿", "百事", "阿里")
        for name in names:
            company = store.save_company({"name": name})
            position = store.save_position({"company_id": company, "title": "测试岗位"})
            store.create_application(position)
        self._navigate("申请进度")
        entries = [x.label for x in self.app.button if "｜" in x.label]
        self.assertEqual(10, len(entries))
        self.assertTrue(entries[0].startswith("阿里"))
        self.assertTrue(entries[1].startswith("百事"))
        self._button("下一页 →").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(2, len([x for x in self.app.button if "｜" in x.label]))
        self._button("← 上一页").click().run()
        self.assertTrue(next(x for x in self.app.button if "｜" in x.label).label.startswith("阿里"))

    def test_repeated_position_applications_show_batch_and_archive_state(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "批次公司"})
        position = store.save_position({"company_id": company, "title": "管培生"})
        first = store.create_submission(position, "秋招", precision="日期", submitted_on="2026-10-18")
        store.create_submission(position, "春招", precision="日期", submitted_on="2026-10-19")
        self._navigate("申请进度")
        labels = [button.label for button in self.app.button if button.label.startswith("批次公司")]
        self.assertTrue(any("秋招" in label for label in labels))
        self.assertTrue(any("春招" in label for label in labels))
        store.archive("applications", first)
        next(item for item in self.app.checkbox if item.label == "查看归档申请").set_value(True).run()
        self.assertTrue(any("秋招" in button.label and "已归档" in button.label
                            for button in self.app.button))

    def test_status_and_node_time_fields_open_only_in_their_panels(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "时间测试公司"})
        position = store.save_position({"company_id": company, "title": "测试岗位"})
        application = store.create_submission(position, precision="日期", submitted_on="2026-10-22")
        self._navigate("申请进度")
        next(x for x in self.app.button if "时间测试公司" in x.label).click().run()
        self.assertEqual(application, self.app.session_state["application_selected"])
        self.assertFalse(any(x.label == "新状态" for x in self.app.selectbox))
        self.assertFalse(any(x.label == "日期精度" for x in self.app.selectbox))
        self._button("改变申请状态").click().run()
        self.assertFalse(self.app.exception)
        assessed = next(x["id"] for x in store.statuses() if x["name"] == "已测评")
        next(x for x in self.app.selectbox if x.label == "新状态").set_value(assessed).run()
        self.assertTrue(any(x.label == "测评完成时间怎么记录" for x in self.app.radio))
        self.assertFalse(any(x.label == "实际投递时间" for x in self.app.radio))
        next(x for x in self.app.checkbox if x.label == "更正实际投递时间（可选）").set_value(True).run()
        next(x for x in self.app.radio if x.label == "实际投递时间").set_value("填写日期").run()
        self.assertTrue(any(x.label == "实际投递日期" for x in self.app.date_input))
        next(x for x in self.app.radio if x.label == "实际投递时间").set_value("填写具体时间").run()
        self.assertTrue(any(x.label == "投递时间" for x in self.app.time_input))
        self._button("← 返回申请详情").click().run()
        self._button("添加／编辑时间节点").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any(x.label == "日期精度" for x in self.app.selectbox))
        next(x for x in self.app.selectbox if x.label == "日期精度").set_value("时间").run()
        self.assertTrue(any(x.label == "节点日期" for x in self.app.date_input))
        self.assertTrue(any(x.label == "节点时间" for x in self.app.time_input))
        self._input("节点名称 *").set_value("测评安排时间")
        self._button("保存时间节点").click().run()
        self.assertFalse(self.app.exception)
        stage_id = store.stages(application)[-1]["id"]
        self.assertTrue(any(x["label"] == "测评安排时间" and x["precision"] == "时间"
                            for x in store.nodes(stage_id)))
        self._button("← 返回申请详情").click().run()
        self._button("改变申请状态").click().run()
        next(x for x in self.app.selectbox if x.label == "新状态").set_value(assessed).run()
        next(x for x in self.app.radio if x.label == "测评完成时间怎么记录").set_value("填写日期").run()
        next(x for x in self.app.date_input if x.label == "测评完成时间日期").set_value(date(2026, 10, 24)).run()
        self._button("确认变更状态").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("已测评", next(x for x in store.list_applications() if x["id"] == application)["status_name"])
        self.assertEqual(2, len(store.stages(application)))
        self.assertEqual("2026-10-24", next(n for n in store.nodes(store.get("applications", application)["current_stage_id"])
                                            if n["label"] == "测评完成时间")["date_on"])
        self._button("← 返回申请列表").click().run()
        self.assertTrue(any("2026-10-24" in x.label for x in self.app.button))
        tooltip = next(x.value for x in self.app.markdown if 'class="cd-history-tooltip"' in x.value)
        self.assertIn("投递时间：2026-10-22", tooltip)
        self.assertIn("测评完成时间：2026-10-24", tooltip)
        self.assertNotIn('title="', tooltip)
        self.assertNotIn("状态变更：", tooltip)
        next(x for x in self.app.button if "时间测试公司" in x.label).click().run()
        self._button("改变申请状态").click().run()
        self.assertTrue(any("已测评" in option and "当前状态" in option
                            for option in next(x for x in self.app.selectbox if x.label == "新状态").options))
        next(x for x in self.app.selectbox if x.label == "新状态").set_value(assessed).run()
        self.assertTrue(any("当前已处于此状态" in x.value for x in self.app.info))

    def test_stage_event_details_open_from_application(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "事件测试公司"})
        position = store.save_position({"company_id": company, "title": "测试岗位"})
        application = store.create_application(position)
        assessment = next(x["id"] for x in store.statuses() if x["name"] == "测评准备")
        store.change_status(application, assessment, submission_precision="明确未知")
        self._navigate("申请进度")
        next(x for x in self.app.button if "事件测试公司" in x.label).click().run()
        self._button("阶段详情与材料").click().run()
        self.assertFalse(any(x.label == "事件时间精度" for x in self.app.selectbox))
        self._button("＋ 新增测评／面试详情").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any(x.label == "← 返回阶段详情" for x in self.app.button))
        self.assertFalse(any(x.label == "← 返回申请详情" for x in self.app.button))
        next(x for x in self.app.selectbox if x.label == "事件时间精度").set_value("日期").run()
        self.assertTrue(any(x.label == "事件日期 YYYY-MM-DD" for x in self.app.text_input))
        self._input("事件标题 *").set_value("测评详情")
        self._input("事件日期 YYYY-MM-DD").set_value("2026-10-30")
        self._input("地点或测评／会议链接").set_value("https://example.test/assessment")
        self._button("保存阶段详情").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("https://example.test/assessment", store.list_events()[0]["location_or_link"])
        self.assertTrue(any("测评详情" in x.label for x in self.app.expander))
        self.assertFalse(any("招聘事件与待办" in option for option in self.app.sidebar.radio[0].options))
        self._button("删除招聘事件").click().run()
        self.assertEqual(1, len(store.list_events()))
        self.assertTrue(any(x.label == "确认删除招聘事件" for x in self.app.button))
        self._button("确认删除招聘事件").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual([], store.list_events())

    def test_unlinked_old_event_can_be_attached_to_stage(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "历史事件公司"})
        position = store.save_position({"company_id": company, "title": "测试岗位"})
        application = store.create_application(position)
        event = store.save_event({"company_id": company, "title": "旧面试通知",
                                  "type": "其他", "precision": "未知",
                                  "location_or_link": "https://example.test/interview",
                                  "requirements": "准备作品集", "notes": "提前十分钟进入"})
        self._navigate("申请进度")
        self.assertTrue(any("未关联申请阶段的历史招聘详情" in x.label for x in self.app.expander))
        self._button("关联这条详情").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(application, store.get("recruitment_events", event)["application_id"])
        next(x for x in self.app.button if "历史事件公司" in x.label).click().run()
        self._button("阶段详情与材料").click().run()
        self.assertTrue(any("旧面试通知" in x.label for x in self.app.expander))
        self.assertTrue(any("准备作品集" in x.value for x in self.app.markdown))

    def test_old_todo_data_remains_without_todo_page(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "待办跳转公司"})
        today = datetime.now(SHANGHAI).date()
        todo = store.save_todo({"company_id": company, "title": "准备笔试", "precision": "日期",
                                "due_on": (today + timedelta(days=2)).isoformat()})
        self._navigate("求职总览")
        self.assertFalse(any("待办" in x.label for x in self.app.button))
        self.assertFalse(any("招聘事件与待办" in option for option in self.app.sidebar.radio[0].options))
        self.assertEqual(todo, store.list_todos()[0]["id"])

    def test_overview_schedule_paginates_ten_items(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "日程翻页公司"})
        deadline = (datetime.now(SHANGHAI).date() + timedelta(days=3)).isoformat()
        for index in range(12):
            store.save_position({"company_id": company, "title": f"安排岗位{index:02d}",
                                 "deadline_on": deadline})
        self._navigate("求职总览")
        self.assertEqual(10, len([x for x in self.app.button if x.label == "查看"]))
        self._button("下一页 →").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(2, len([x for x in self.app.button if x.label == "查看"]))
        self._button("← 上一页").click().run()
        self.assertEqual(10, len([x for x in self.app.button if x.label == "查看"]))

    def test_overview_prioritizes_companies_without_submissions_and_labels_history(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        today = datetime.now(SHANGHAI).date()
        new_company = store.save_company({"name": "新公司"})
        new_position = store.save_position({"company_id": new_company, "title": "新机会",
                                            "deadline_on": (today + timedelta(days=3)).isoformat()})
        store.create_application(new_position)
        prior_company = store.save_company({"name": "投递过的公司"})
        store.save_position({"company_id": prior_company, "title": "尚未投的新岗位",
                             "deadline_on": (today + timedelta(days=2)).isoformat()})
        prior_position = store.save_position({"company_id": prior_company, "title": "旧岗位"})
        store.create_submission(prior_position, precision="明确未知")
        applied_company = store.save_company({"name": "当前岗位已投公司"})
        applied_position = store.save_position({"company_id": applied_company, "title": "已投岗位",
                                                "deadline_on": (today + timedelta(days=1)).isoformat()})
        store.create_submission(applied_position, precision="日期", submitted_on=today.isoformat())
        self._navigate("求职总览")
        rows = [x.value for x in self.app.markdown if 'class="cd-overview-position"' in x.value]
        self.assertEqual(3, len(rows))
        self.assertIn("新公司", rows[0])
        self.assertNotIn("已投递", rows[0])
        self.assertIn("投递过的公司", rows[1])
        self.assertIn("该公司其他岗位已投递", rows[1])
        self.assertIn("当前岗位已投公司", rows[2])
        self.assertIn("该岗位已投递", rows[2])

    def test_overview_schedule_switches_between_positions_and_stage_events(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "日程分类公司"})
        today = datetime.now(SHANGHAI).date()
        position = store.save_position({"company_id": company, "title": "即将截止岗位",
                                        "deadline_on": (today + timedelta(days=3)).isoformat()})
        store.save_todo({"company_id": company, "title": "即将截止待办", "precision": "日期",
                         "due_on": (today + timedelta(days=2)).isoformat()})
        application = store.create_application(position)
        interview = next(x["id"] for x in store.statuses() if x["name"] == "真人面试通知")
        store.change_status(application, interview, submission_precision="明确未知")
        stage = store.get("applications", application)["current_stage_id"]
        store.save_event({"company_id": company, "position_id": position,
                          "application_id": application, "stage_id": stage,
                          "title": "即将开始面试", "type": "真人面试",
                          "precision": "日期", "event_on": (today + timedelta(days=1)).isoformat()})
        self._navigate("求职总览")
        self.assertTrue(any("即将截止岗位" in x.value for x in self.app.markdown))
        self.assertFalse(any("即将截止待办" in x.value for x in self.app.markdown))
        self._button("即将截止的阶段／面试安排").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("即将开始面试" in x.value for x in self.app.markdown))
        self.assertFalse(any("即将截止待办" in x.value for x in self.app.markdown))
        self.assertFalse(any("即将截止岗位" in x.value for x in self.app.markdown))
        self._button("查看").click().run()
        self.assertEqual("申请进度", self.app.sidebar.radio[0].value)
        self.assertEqual("materials", self.app.session_state["application_panel"])
        self._button("← 返回申请详情").click().run()
        self._button("← 返回求职总览").click().run()
        self.assertEqual("求职总览", self.app.sidebar.radio[0].value)
        self._navigate("求职总览")
        self._button("最近要截止的岗位投递").click().run()
        self.assertTrue(any("即将截止岗位" in x.value for x in self.app.markdown))

    def test_assessment_event_list_shows_deadline_and_requires_confirmation_for_change(self):
        store = Store(Path(self.tmp.name) / "careerdesk.sqlite3")
        company = store.save_company({"name": "测评截止公司"})
        position = store.save_position({"company_id": company, "title": "测评岗位"})
        application = store.create_application(position)
        assessment = next(row["id"] for row in store.statuses() if row["name"] == "测评准备")
        store.change_status(application, assessment, submission_precision="明确未知")
        stage = store.get("applications", application)["current_stage_id"]
        event = store.save_event({"company_id": company, "position_id": position,
                                  "application_id": application, "stage_id": stage,
                                  "title": "在线测评", "type": "测评", "status": "已安排",
                                  "precision": "未知"}, stage_nodes=[
            {"label": "测评截止时间", "kind": "截止", "precision": "日期", "value": "2026-10-30"}])
        self._navigate("申请进度")
        next(x for x in self.app.button if "测评截止公司" in x.label).click().run()
        self._button("阶段详情与材料").click().run()
        self.assertTrue(any("在线测评" in x.label for x in self.app.expander))
        self.assertTrue(any("2026-10-30" in x.value for x in self.app.markdown))
        self._button("编辑这条详情").click().run()
        self.assertEqual(event, self.app.session_state["stage_event_selected"])
        deadline_input = next(x for x in self.app.text_input if x.label.startswith("测评截止时间 YYYY"))
        self.assertEqual("2026-10-30", deadline_input.value)
        deadline_input.set_value("2026-11-01")
        self._button("保存阶段详情").click().run()
        self.assertTrue(any("请勾选“确认写入测评截止时间”" in x.value for x in self.app.error))
        self.assertEqual("2026-10-30", store.list_events()[0]["deadline_on"])
        next(x for x in self.app.text_input if x.label.startswith("测评截止时间 YYYY")).set_value("2026-11-01")
        next(x for x in self.app.checkbox if x.label == "确认写入测评截止时间").set_value(True)
        self._button("保存阶段详情").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual("2026-11-01", store.list_events()[0]["deadline_on"])


if __name__ == "__main__":
    unittest.main()
