"""CareerDesk local Streamlit interface."""

from __future__ import annotations

import html
import hashlib
import importlib
import os
import ctypes
from collections import Counter
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path

import streamlit as st

from careerdesk.backup import backup_database, export_csv, export_json, restore_database, validate_backup
from careerdesk.bulk import COMPANY_COLUMNS, POSITION_COLUMNS, apply_import, existing_template_bytes, preview_import, template_bytes
import careerdesk.core as _core
import careerdesk.db as _db
from careerdesk.db import data_dir, database_path
from careerdesk.files import SUPPORTED, list_files, preview, refresh_availability, relocate, remove_index, scan_directory, update_file_notes
from careerdesk.parsing import company_suggestions, notice_suggestions, position_suggestions, profile_suggestions
from careerdesk.timeutil import SHANGHAI
import careerdesk.game_art as _game_art
import careerdesk.theme as _theme
import careerdesk.dashboard as _dashboard
import careerdesk.duplicates as _duplicates
import careerdesk.zhipu_trial as _zhipu_trial

# Streamlit reruns this file in the existing process. Refresh edited modules so
# business rules and visual changes take effect without restarting the launcher.
importlib.reload(_db)
importlib.reload(_core)
importlib.reload(_game_art)
importlib.reload(_theme)
importlib.reload(_dashboard)
importlib.reload(_duplicates)
importlib.reload(_zhipu_trial)
from careerdesk.core import EVENT_FIELDS, Store
from careerdesk.duplicates import archive_unlinked_position, position_duplicate_groups, set_group_reviewed
from careerdesk.zhipu_trial import analyze_positions, position_payload
from careerdesk.theme import banner, footer, inject_theme, sidebar_brand
from careerdesk.dashboard import overview, urgency

st.set_page_config(page_title="CareerDesk 求职工作台", page_icon="🌱", layout="wide")

PAGES = ("求职总览", "招聘信息", "申请进度", "AI 试用", "文件中心", "个人资料库", "设置与备份")
PROFILE_CATEGORIES = ("教育", "科研", "项目", "实习", "技能", "获奖与论文", "志愿及其他", "网申问答", "自我介绍", "面试复盘")


@st.cache_resource(show_spinner=False)
def open_store(path: str, core_revision: int) -> Store:
    """Store keeps only a path; its operations still open short-lived connections."""
    return Store(Path(path))


def safe_action(fn, success="已保存"):
    try:
        value = fn()
        if success:
            st.success(success)
        return True if value is None else value
    except (ValueError, OSError) as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"操作未完成：{exc}")
    return None


@st.cache_data(show_spinner=False)
def cached_existing_template(path: str, revision: int) -> bytes:
    """Reuse the export until the local database file changes."""
    return existing_template_bytes(Store(Path(path)))


def import_comparison_html(kind: str, incoming: dict, match: dict, row_number: int) -> str:
    """Show the matched record and incoming row with every field named in Chinese."""
    columns = COMPANY_COLUMNS if kind == "company" else POSITION_COLUMNS
    table_name = "公司表" if kind == "company" else "岗位表"

    def cell(value) -> str:
        return html.escape(str(value)).replace("\n", "<br>") if value not in (None, "") else "—"

    lines = ['<div class="cd-compare-scroll"><table class="cd-compare-table">',
             '<thead><tr><th>字段</th>',
             f'<th>{html.escape(match["source"])}</th>',
             f'<th>本次{table_name}第 {row_number} 行</th></tr></thead><tbody>']
    for label, field in columns.items():
        original = match["data"].get(field)
        candidate = incoming.get(field)
        changed = " class=\"cd-compare-difference\"" if str(original or "") != str(candidate or "") else ""
        lines.append(f'<tr{changed}><th>{html.escape(label)}</th><td>{cell(original)}</td><td>{cell(candidate)}</td></tr>')
    lines.append("</tbody></table></div>")
    return "".join(lines)


def position_duplicate_comparison_html(positions: list[dict]) -> str:
    """Show every imported field side by side and highlight differences."""
    parts = ['<div class="cd-compare-scroll"><table class="cd-compare-table"><thead><tr><th>字段</th>']
    parts.extend(f"<th>记录 {index}</th>" for index in range(1, len(positions) + 1))
    parts.append("</tr></thead><tbody>")
    for label, field in POSITION_COLUMNS.items():
        if field == "company_name":
            continue
        values = [str(row.get(field) or "").strip() for row in positions]
        changed = ' class="cd-compare-difference"' if len(set(values)) > 1 else ""
        parts.append(f"<tr{changed}><th>{html.escape(label)}</th>")
        parts.extend(f"<td>{html.escape(value).replace(chr(10), '<br>') if value else '—'}</td>"
                     for value in values)
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def chinese_table_html(rows: list[dict], *, height: int = 240, row_titles: list[str] | None = None) -> str:
    """Compact read-only table without Streamlit's English column menu."""
    if not rows:
        return ""
    columns = list(rows[0])
    wrapper_class = "cd-import-list-scroll cd-timeline-table" if row_titles else "cd-import-list-scroll"
    parts = [f'<div class="{wrapper_class}" style="max-height:{height}px">',
             '<table class="cd-simple-table"><thead><tr>']
    parts.extend(f"<th>{html.escape(str(label))}</th>" for label in columns)
    parts.append("</tr></thead><tbody>")
    for index, row in enumerate(rows):
        tooltip = "<br>".join(html.escape(line) for line in row_titles[index].splitlines()) if row_titles else ""
        parts.append('<tr class="cd-history-row" tabindex="0">' if row_titles else "<tr>")
        for column_index, column in enumerate(columns):
            value = row.get(column)
            display = "—" if value in (None, "") else html.escape(str(value))
            if row_titles and column_index == len(columns) - 1:
                parts.append(f'<td>{display}<span class="cd-history-tooltip" role="note">'
                             f'<strong>历次时间记录</strong><br>{tooltip}</span></td>')
            else:
                parts.append(f"<td>{display}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def choices(rows, label_key="name", *, empty="请选择", include_new=False):
    lookup = {row["id"]: row for row in rows}
    opts = ([None] if empty else []) + list(lookup)
    if include_new:
        opts = [None] + list(lookup)
    return opts, lookup, lambda value: ("＋ 新增" if include_new else empty) if value is None else str(lookup[value].get(label_key) or value)


def apply_text_suggestions(prefix: str, parser, field_map: dict[str, str]) -> None:
    pasted = st.session_state.get(f"{prefix}_pasted", "")
    suggestions = parser(pasted)
    st.session_state[f"{prefix}_suggestions"] = suggestions
    for field, suggestion in suggestions.items():
        if field in field_map:
            widget_key = field_map[field]
            if not st.session_state.get(widget_key):
                st.session_state[widget_key] = suggestion["value"]


def input_suggestions(prefix: str, parser, field_map: dict[str, str], label="粘贴文字辅助填写", *, in_form=False):
    with st.expander(label):
        pasted = st.text_area("在这里粘贴文字；点击解析后只生成待确认建议", key=f"{prefix}_pasted", height=150)
        action = st.form_submit_button if in_form else st.button
        action("解析文字", key=f"{prefix}_parse", on_click=apply_text_suggestions,
               args=(prefix, parser, field_map))
        for field, suggestion in st.session_state.get(f"{prefix}_suggestions", {}).items():
            st.caption(f"{field} → {suggestion['value']}；依据：{suggestion['source']}")
        if pasted and not st.session_state.get(f"{prefix}_suggestions"):
            st.caption("未找到可靠字段时，可继续手动填写；解析不会自动保存。")


def display_urgency(row):
    if not row:
        return "时间未知"
    return f"{row['label']}"


def local_display(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


@lru_cache(maxsize=2048)
def company_sort_key(name: str) -> bytes:
    """Use Windows' Chinese pinyin collation for mixed Chinese company names."""
    normalized = (name or "").strip()
    if os.name == "nt":
        mapper = ctypes.windll.kernel32.LCMapStringEx
        mapper.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int,
                           ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p)
        mapper.restype = ctypes.c_int
        size = mapper("zh-CN", 0x400, normalized, -1, None, 0, None, None, None)
        if size:
            result = ctypes.create_string_buffer(size)
            if mapper("zh-CN", 0x400, normalized, -1, result, size, None, None, None):
                return result.raw
    return normalized.casefold().encode("utf-8")


def application_time_details(store: Store, applications: list[dict]) -> dict[str, dict]:
    """Show only dates the user recorded, never automatic status-change timestamps."""
    if not applications:
        return {}
    stage_rows = []
    ids = [row["id"] for row in applications]
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        marks = ",".join("?" for _ in batch)
        stage_rows.extend(store.query(f"""SELECT x.application_id,x.id AS stage_id,x.entered_at_utc,
                                     s.name AS status_name,n.label,n.kind,n.date_on,n.at_utc
                                     FROM application_stages x
                                     JOIN status_definitions s ON s.id=x.status_id
                                     LEFT JOIN stage_time_nodes n ON n.stage_id=x.id
                                     WHERE x.application_id IN ({marks})
                                     ORDER BY x.entered_at_utc,x.id,n.label""", batch))
    by_app: dict[str, list[dict]] = {}
    for stage in stage_rows:
        by_app.setdefault(stage["application_id"], []).append(stage)
    result = {}
    for app in applications:
        submitted = app["submitted_on"] or local_display(app["submitted_at_utc"])
        history = [f"投递时间：{submitted}"] if submitted else []
        current_nodes = []
        seen_stages = set()
        empty_stages = []
        for stage in by_app.get(app["id"], []):
            stage_id = stage["stage_id"]
            if stage_id not in seen_stages:
                seen_stages.add(stage_id)
                empty_stages.append((stage_id, stage["status_name"]))
            node_time = stage["date_on"] or local_display(stage["at_utc"])
            if node_time:
                history.append(f"{stage['status_name']} · {stage['label']}：{node_time}")
                empty_stages = [(item_id, name) for item_id, name in empty_stages if item_id != stage_id]
            if stage_id == app["current_stage_id"]:
                if node_time:
                    current_nodes.append((stage, node_time))
        history.extend(f"{name}：未填写时间" for stage_id, name in empty_stages
                       if name != "已投递" or not submitted)
        if app["status_name"] == "已投递" and submitted:
            current = submitted
        elif current_nodes:
            rank = {"状态发生时间": -1, "完成": 0, "通知": 1, "安排": 2, "截止": 3}
            current = min(current_nodes, key=lambda item: rank.get(item[0]["label"],
                                                                    rank.get(item[0]["kind"], 4)))[1]
        else:
            current = "未填写"
        result[app["id"]] = {"current": current, "history": "\n".join(history) or "尚未填写时间"}
    return result


def urgency_badge(info: dict) -> str:
    colors = {"red": ("#fee2e2", "#991b1b"), "orange": ("#ffedd5", "#9a3412"),
              "gold": ("#fef3c7", "#92400e"), "slategray": ("#e2e8f0", "#334155"),
              "gray": ("#f3f4f6", "#4b5563")}
    background, foreground = colors.get(info["color"], colors["gray"])
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:12px;'
            f'background:{background};color:{foreground};font-weight:600">'
            f'{html.escape(info["label"])}</span>')


def open_overview_item(item: dict):
    if item["source"] == "position":
        st.session_state["navigation"] = "招聘信息"
        st.session_state["company_catalog_id"] = item["company_id"]
        st.session_state["position_selected"] = item["id"]
        st.session_state["position_editor_mode"] = True
        st.session_state["position_return_to_overview"] = True
    elif item["source"] == "application":
        st.session_state["navigation"] = "申请进度"
        st.session_state["application_selected"] = item["id"]
        st.session_state["application_panel_owner"] = item["id"]
        st.session_state["application_return_view"] = "schedule"
        target_panel = "materials" if item.get("linked_event_id") else "node" if item.get("stage_id") else "detail"
        st.session_state["application_panel"] = target_panel
        if item.get("stage_id"):
            st.session_state[f"stage_for_{target_panel}_{item['id']}"] = item["stage_id"]
        st.session_state["new_submission_mode"] = False
    elif item["source"] == "event" and item.get("application_id") and item.get("stage_id"):
        app_id = item["application_id"]
        st.session_state["navigation"] = "申请进度"
        st.session_state["application_selected"] = app_id
        st.session_state["application_panel_owner"] = app_id
        st.session_state["application_return_view"] = "schedule"
        st.session_state["application_panel"] = "materials"
        st.session_state[f"stage_for_materials_{app_id}"] = item["stage_id"]
        st.session_state["new_submission_mode"] = False


def overview_line(item: dict, section: str, index: int):
    left, right = st.columns([8, 1])
    if item["source"] == "position":
        rank = item["application_rank"]
        label = ("", "该公司其他岗位已投递", "该岗位已投递")[rank]
        marker = (f'<span class="cd-overview-application-mark cd-overview-application-mark-{rank}">'
                  f'{label}</span>' if label else "")
        left.markdown(
            f'<div class="cd-overview-position">'
            f'<div class="cd-overview-position-heading">{urgency_badge(item["urgency"])} '
            f'{marker} <strong>{html.escape(item["company_name"])}</strong></div>'
            f'<div class="cd-overview-position-detail">{html.escape(item["position_title"])}'
            f'<span>截止：{html.escape(item["display_time"])}</span></div></div>',
            unsafe_allow_html=True)
    else:
        left.markdown(f"{urgency_badge(item['urgency'])}　<strong>{html.escape(item['kind'])}</strong> · "
                      f"{html.escape(item['title'])} · {html.escape(item['display_time'])}", unsafe_allow_html=True)
    right.button("查看", key=f"open_{section}_{item['source']}_{item['id']}_{index}",
                 on_click=open_overview_item, args=(item,))


def overview_application_rows(store: Store, view: str) -> list[dict]:
    rows = store.list_applications()
    if view == "submitted":
        return [row for row in rows if row["submitted_on"] or row["submitted_at_utc"]]
    if view == "progressing":
        return [row for row in rows if row["status_category"] == "推进中"]
    raise ValueError("未知的总览清单")


def open_overview_application(app_id: str, view: str):
    st.session_state["navigation"] = "申请进度"
    st.session_state["overview_view"] = None
    st.session_state["application_selected"] = app_id
    st.session_state["application_panel_owner"] = app_id
    st.session_state["application_panel"] = "detail"
    st.session_state["application_return_view"] = view
    st.session_state["new_submission_mode"] = False


def page_overview_application_list(store: Store, view: str):
    title = "已投递申请" if view == "submitted" else "正在推进"
    st.title(title)
    st.button("← 返回求职总览", on_click=lambda: st.session_state.update(overview_view=None))
    rows = overview_application_rows(store, view)
    time_details = application_time_details(store, rows)
    st.caption(f"共 {len(rows)} 条申请 · 点击条目可查看状态历史、时间节点与阶段材料")
    if not rows:
        st.info("当前没有符合条件的申请。")
        return
    for row in rows:
        with st.container(border=True):
            left, right = st.columns([5, 1], vertical_alignment="center")
            left.markdown(f"**{html.escape(row['company_name'])} · {html.escape(row['position_title'])}**")
            left.caption(f"当前状态：{row['status_name']}（{row['status_category']}） · "
                         f"当前状态时间：{time_details[row['id']]['current']} · 轮次：{row['round_label'] or '未命名'}")
            right.button("查看申请详情", key=f"overview_application_{view}_{row['id']}",
                         use_container_width=True, on_click=open_overview_application, args=(row["id"], view))


def page_overview(store: Store):
    view = st.session_state.get("overview_view")
    if view in ("submitted", "progressing"):
        page_overview_application_list(store, view)
        return
    st.title("求职总览")
    info = overview(store)
    cols = st.columns(2)
    for col, label, value, kind in zip(cols, ("已投递申请", "正在推进"),
                                       (info["submitted"], info["progressing"]),
                                       ("submitted", "progressing")):
        col.button(f"{label} ↗\n\n**{value}**", key=f"overview_{kind}_open",
                   help=f"查看{label}清单", use_container_width=True,
                   on_click=lambda target=kind: st.session_state.update(overview_view=target))
    st.caption("已投递按有实际投递日期/时间的申请计；推进中按状态总标签计；归档记录不计入。业务时区：Asia/Shanghai。")
    if not any((info["submitted"], info["progressing"], info["upcoming"], info["recent"])):
        st.info("还没有求职记录。可从左侧“招聘信息”开始新增，或用 Excel 模板导入公司与岗位。")
    st.subheader("近期安排（今天至未来第 7 天）")
    schedule_filter = st.session_state.get("overview_schedule_filter", "positions")
    position_button, stage_button = st.columns(2)
    position_button.button("最近要截止的岗位投递", key="overview_schedule_positions",
                           type="primary" if schedule_filter == "positions" else "secondary",
                           use_container_width=True,
                           on_click=lambda: st.session_state.update(overview_schedule_filter="positions"))
    stage_button.button("即将截止的阶段／面试安排", key="overview_schedule_stages",
                        type="primary" if schedule_filter == "stages" else "secondary",
                        use_container_width=True,
                        on_click=lambda: st.session_state.update(overview_schedule_filter="stages"))
    if schedule_filter == "stages":
        st.caption("显示申请阶段的截止或面试安排，以及已安排的测评和面试详情时间。")
    else:
        st.caption("优先显示未投递过的公司；已有其他投递及该岗位已投递会分别标记。每组按截止时间排序，归档投递也计入历史。")

    def matches_schedule(item: dict) -> bool:
        if schedule_filter == "positions":
            return item["source"] == "position"
        return (item["source"] == "event" and bool(item.get("application_id") and item.get("stage_id")) or
                item["source"] == "application" and
                (item["kind"].endswith("截止") or item["kind"].endswith("安排")))

    upcoming = [item for item in info["upcoming"] if matches_schedule(item)]
    overdue = [item for item in info["overdue"] if matches_schedule(item)]
    if schedule_filter == "positions":
        upcoming.sort(key=lambda item: (item["application_rank"], item["display_time"], item["title"]))
        overdue.sort(key=lambda item: (item["application_rank"], item["display_time"], item["title"]))
    reset_card_page_on_filter_change("overview_upcoming_page", (schedule_filter,))
    reset_card_page_on_filter_change("overview_overdue_page", (schedule_filter,))
    if not upcoming:
        st.caption("这一类在未来 7 天内没有已知时间的安排。")
    else:
        st.caption(f"共 {len(upcoming)} 条，每页显示 10 条。")
        upcoming_rows, page, page_count = visible_cards(upcoming, "overview_upcoming_page", page_size=10)
        for index, item in enumerate(upcoming_rows, start=page * 10):
            overview_line(item, "upcoming", index)
        card_page_controls("overview_upcoming_page", page, page_count)
    if overdue:
        with st.expander(f"已过时间、仍待处理（{len(overdue)} 条）"):
            overdue_rows, page, page_count = visible_cards(overdue, "overview_overdue_page", page_size=10)
            for index, item in enumerate(overdue_rows, start=page * 10):
                overview_line(item, "overdue", index)
            card_page_controls("overview_overdue_page", page, page_count)
    st.subheader("最近更新")
    if info["recent"]:
        st.dataframe([{"类型": row["kind"], "名称": row["title"], "更新时间（UTC）": row["updated_at_utc"]} for row in info["recent"]],
                     hide_index=True, width="stretch")


def bulk_import_panel(store: Store):
    def clear_previous_preview():
        st.session_state.pop("import_preview", None)
        st.session_state.pop("import_feedback", None)

    upload_serial = st.session_state.get("import_upload_serial", 0)
    upload_key = "company_position_import" if upload_serial == 0 else f"company_position_import_{upload_serial}"
    selected_file = st.session_state.get(upload_key)
    # Reconcile sessions left on the old upload widget by a previous version.
    if selected_file and not st.session_state.get("import_preview") and not st.session_state.get("import_feedback"):
        fingerprint = hashlib.sha256(selected_file.getvalue()).hexdigest()
        previous_batch = store.one("SELECT imported_at_utc FROM import_batches WHERE source_fingerprint=?", (fingerprint,))
        if previous_batch:
            imported_at = datetime.fromisoformat(previous_batch["imported_at_utc"]).astimezone(SHANGHAI)
            st.session_state["import_feedback"] = {
                "kind": "success",
                "message": (f"这份文件已于 {imported_at:%Y-%m-%d %H:%M} 导入成功。"
                            "上传区已清空，本次没有再次写入。"),
            }
            st.session_state["import_upload_serial"] = upload_serial + 1
            st.rerun()

    feedback = st.session_state.get("import_feedback")
    if feedback:
        if feedback["kind"] == "success":
            st.success(feedback["message"])
        else:
            st.error(feedback["message"])

    # Selecting a file reruns Streamlit. Keep the import form visible so the
    # next step does not disappear immediately after the file chooser closes.
    upload_selected = st.session_state.get(upload_key) is not None
    with st.expander("Excel 模板批量导入公司与岗位", expanded=upload_selected):
        blank_col, existing_col = st.columns(2)
        with blank_col:
            st.download_button("下载空白双表模板", template_bytes(), file_name="CareerDesk_公司岗位导入模板.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True, on_click="ignore")
        with existing_col:
            st.download_button("导出已有公司与岗位", cached_existing_template(str(store.path), store.path.stat().st_mtime_ns),
                               file_name="CareerDesk_已有公司岗位_含归档.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True, on_click="ignore")
        st.caption("导出文件与空白模板列名一致，包含已归档记录；灰色行表示已归档。")
        st.caption("请选择 .xlsx 文件，且包含“公司”和“岗位”工作表。选中文件后点击“检查并预览导入”；预览不会写入数据。")
        uploaded = st.file_uploader("选择已填写的模板", type=["xlsx"], key=upload_key,
                                    on_change=clear_previous_preview)
        if uploaded:
            st.info(f"已选择：{uploaded.name}。请点击下方按钮检查内容。")
        if uploaded and st.button("检查并预览导入", key="preview_import"):
            result = safe_action(lambda: preview_import(store, uploaded.getvalue()), "检查完成，尚未写入")
            if result:
                st.session_state["import_preview"] = result
    result = st.session_state.get("import_preview")
    if not result:
        return

    rows = [(kind, row) for kind, group in (("company", result["companies"]),
                                           ("position", result["positions"])) for row in group]
    urgent = [(kind, row) for kind, row in rows if row["action"] in ("跳过", "错误")]
    review = [(kind, row) for kind, row in rows if row["red"] and row["action"] not in ("跳过", "错误")]
    routine = [(kind, row) for kind, row in rows if not row["red"]]
    st.markdown("**导入预览**")
    summary_slot = st.empty()
    error_slot = st.empty()
    hint_slot = st.empty()
    confirm_slot = st.empty()
    st.caption("普通条目已折叠并默认新增；仅重复、错误和其他需核对的行提供处理选项。")
    decisions = {(kind, row["row_number"]): row["action"] for kind, row in routine}

    def show_review_row(kind, row):
        table = "公司表" if kind == "company" else "岗位表"
        data = row["data"]
        summary = (f"公司：{data.get('name', '')}" if kind == "company" else
                   f"公司：{data.get('company_name', '')}；岗位：{data.get('title', '')}；"
                   f"招聘项目：{data.get('recruitment_project') or '未填'}")
        st.error(f"🔴 {table}第 {row['row_number']} 行：{summary}\n\n" + "；".join(row["issues"]))
        matches = row.get("matches", [])
        if matches:
            st.markdown("**两条记录详细对照**")
            st.markdown(import_comparison_html(kind, data, matches[0], row["row_number"]), unsafe_allow_html=True)
            if len(matches) > 1:
                st.caption("另外还匹配：" + "、".join(match["source"] for match in matches[1:]))
        key = (kind, row["row_number"])
        if row["action"] == "错误":
            st.warning("此行有校验错误，请修正模板后重新预览")
            decisions[key] = "错误"
            return
        valid_actions = ["新增", "沿用", "跳过"] if kind == "company" and row["candidate_id"] else ["新增", "跳过"]
        decisions[key] = st.selectbox(f"{table}第 {row['row_number']} 行处理方式", valid_actions,
                                      index=valid_actions.index(row["action"]),
                                      key=f"import_{kind}_{row['row_number']}")

    if urgent:
        st.markdown(f"**重点核对：{len(urgent)} 条重复或错误**")
        for kind, row in urgent:
            show_review_row(kind, row)
    if review:
        with st.expander(f"展开其他需核对条目（{len(review)} 条）"):
            for kind, row in review:
                show_review_row(kind, row)
    if routine:
        with st.expander(f"展开普通条目（{len(routine)} 条，默认新增）"):
            order = st.selectbox("排序方式", ("按 Excel 行号", "按公司名称", "按岗位名称"), key="import_routine_order")
            if order == "按公司名称":
                listed = sorted(routine, key=lambda item: (
                    (item[1]["data"].get("name") or item[1]["data"].get("company_name") or "").casefold(),
                    item[1]["row_number"]))
            elif order == "按岗位名称":
                listed = sorted(routine, key=lambda item: ((item[1]["data"].get("title") or "").casefold(),
                                                         item[1]["row_number"]))
            else:
                listed = routine
            st.markdown(chinese_table_html([
                {"工作表": "公司" if kind == "company" else "岗位", "Excel 行号": row["row_number"],
                 "公司": row["data"].get("name") or row["data"].get("company_name") or "",
                 "岗位": row["data"].get("title") or "", "处理方式": row["action"]}
                for kind, row in listed]), unsafe_allow_html=True)

    counts = {action: sum(action == value for value in decisions.values())
              for action in ("新增", "沿用", "跳过", "错误")}
    summary_slot.info(f"共 {len(rows)} 条：新增 {counts['新增']}、沿用 {counts['沿用']}、跳过 {counts['跳过']}、错误 {counts['错误']}。")
    if counts["错误"]:
        error_rows = [f"{'公司表' if kind == 'company' else '岗位表'}第 {row['row_number']} 行"
                      for kind, row in rows if row["action"] == "错误"]
        error_slot.error(f"暂不能确认导入：还有 {counts['错误']} 行校验错误。请修正 Excel 中的"
                         f"{'、'.join(error_rows)}，重新选择文件并点击“检查并预览导入”。")
        if any("截止日期必须是" in issue for _, row in rows for issue in row["issues"]):
            hint_slot.caption("截止日期列只接受 YYYY-MM-DD，例如 2026-10-31；如需保留 18:00 等具体时间，可写在“备注”列。")
    with confirm_slot:
        confirm = st.button("确认整批导入", type="primary", disabled=bool(counts["错误"]), key="confirm_import_batch")
    if confirm:
        try:
            answer = apply_import(store, result, decisions)
        except (ValueError, OSError) as exc:
            st.session_state["import_feedback"] = {"kind": "error", "message": f"导入失败，本批数据未写入：{exc}"}
        except Exception as exc:
            st.session_state["import_feedback"] = {"kind": "error", "message": f"导入失败，本批数据未写入：{exc}"}
        else:
            st.session_state["import_feedback"] = {
                "kind": "success",
                "message": (f"导入成功，页面已更新：新增公司 {answer['companies_added']} 家、"
                            f"沿用公司 {answer['companies_reused']} 家、新增岗位 {answer['positions_added']} 个、"
                            f"跳过 {answer['skipped']} 条。上传文件已清除。"),
            }
            st.session_state.pop("import_preview", None)
            st.session_state["import_upload_serial"] = upload_serial + 1
        st.rerun()


def visible_cards(rows: list[dict], key: str, *, page_size: int = 8) -> tuple[list[dict], int, int]:
    page_count = max(1, (len(rows) + page_size - 1) // page_size)
    page = min(max(int(st.session_state.get(key, 0)), 0), page_count - 1)
    st.session_state[key] = page
    return rows[page * page_size:(page + 1) * page_size], page, page_count


def reset_card_page_on_filter_change(key: str, signature: tuple) -> None:
    filter_key = f"{key}_filters"
    if st.session_state.get(filter_key) != signature:
        st.session_state[filter_key] = signature
        st.session_state[key] = 0


def change_card_page(key: str, page: int) -> None:
    st.session_state[key] = page


def card_page_controls(key: str, page: int, page_count: int) -> None:
    if page_count <= 1:
        return
    previous, current, following = st.columns([1, 1, 1])
    previous.button("← 上一页", key=f"{key}_previous", use_container_width=True,
                    disabled=page == 0, on_click=change_card_page, args=(key, page - 1))
    current.markdown(f'<div class="cd-page-number">第 {page + 1} 页 / 共 {page_count} 页</div>', unsafe_allow_html=True)
    following.button("下一页 →", key=f"{key}_next", use_container_width=True,
                     disabled=page >= page_count - 1, on_click=change_card_page, args=(key, page + 1))


def options_with_current(options: tuple[str, ...], current: str | None) -> tuple[str, ...]:
    """Keep imported labels selectable when editing an existing record."""
    return options + (current,) if current and current not in options else options


def open_new_submission(company_id: str, position_id: str | None = None) -> None:
    st.session_state["navigation"] = "申请进度"
    st.session_state["new_submission_mode"] = True
    st.session_state["submission_company"] = company_id
    st.session_state["submission_position"] = position_id
    st.session_state["application_return_view"] = None


def return_to_company_catalog() -> None:
    st.session_state["new_submission_mode"] = False
    open_company_catalog(st.session_state["submission_company"])


def open_new_position(company_id: str) -> None:
    st.session_state["navigation"] = "招聘信息"
    st.session_state["company_catalog_id"] = company_id
    st.session_state["position_editor_mode"] = True
    st.session_state["position_selected"] = None
    st.session_state["position_return_to_overview"] = False
    st.session_state["position_focus_id"] = None
    st.session_state["position_focus_filters"] = None
    st.session_state["new_submission_mode"] = False


def open_company_catalog(company_id: str) -> None:
    st.session_state["navigation"] = "招聘信息"
    st.session_state["company_catalog_id"] = company_id
    st.session_state["company_editor_mode"] = False
    st.session_state["position_editor_mode"] = False
    st.session_state["position_return_to_overview"] = False
    st.session_state["position_focus_id"] = None
    st.session_state["position_focus_filters"] = None


def close_company_catalog() -> None:
    st.session_state["company_catalog_id"] = None
    st.session_state["position_editor_mode"] = False
    st.session_state["position_return_to_overview"] = False
    st.session_state["position_focus_id"] = None
    st.session_state["position_focus_filters"] = None


def open_position_editor(company_id: str, position_id: str) -> None:
    st.session_state["navigation"] = "招聘信息"
    st.session_state["company_catalog_id"] = company_id
    st.session_state["position_selected"] = position_id
    st.session_state["position_editor_mode"] = True
    st.session_state["position_return_to_overview"] = False


def open_duplicate_position_editor(company_id: str, position_id: str) -> None:
    st.session_state["position_duplicate_review"] = False
    open_position_editor(company_id, position_id)
    st.session_state["position_return_to_duplicate_review"] = True


def close_position_editor() -> None:
    st.session_state["position_editor_mode"] = False
    if st.session_state.pop("position_return_to_duplicate_review", False):
        st.session_state["company_catalog_id"] = None
        st.session_state["position_duplicate_review"] = True
        return
    if st.session_state.pop("position_return_to_overview", False):
        st.session_state["company_catalog_id"] = None
        st.session_state["navigation"] = "求职总览"


def show_position_in_catalog(position_id: str) -> None:
    """Return from a deadline detail to its company list with that job first."""
    st.session_state["position_editor_mode"] = False
    st.session_state["position_return_to_overview"] = False
    st.session_state["position_focus_id"] = position_id
    st.session_state["position_focus_filters"] = None
    st.session_state["position_search"] = ""
    st.session_state["position_archived"] = False
    st.session_state["position_hide_overdue"] = False
    st.session_state["position_cards_page"] = 0


def open_application_detail(application_id: str) -> None:
    st.session_state["application_selected"] = application_id
    st.session_state["application_panel_owner"] = application_id
    st.session_state["application_panel"] = "detail"
    st.session_state["new_submission_mode"] = False
    st.session_state["application_return_view"] = None


def close_application_detail() -> None:
    return_view = st.session_state.pop("application_return_view", None)
    st.session_state["application_selected"] = None
    st.session_state["application_panel_owner"] = None
    st.session_state["application_panel"] = "detail"
    if return_view:
        st.session_state["navigation"] = "求职总览"
        st.session_state["overview_view"] = return_view if return_view != "schedule" else None


def open_stage_event_editor(application_id: str, stage_id: str, event_id: str | None = None) -> None:
    st.session_state["application_selected"] = application_id
    st.session_state["application_panel_owner"] = application_id
    st.session_state["application_panel"] = "stage_event"
    st.session_state["stage_event_stage_id"] = stage_id
    st.session_state["stage_event_selected"] = event_id
    st.session_state[f"stage_for_materials_{application_id}"] = stage_id


def render_event_delete_controls(store: Store, event_id: str, button_container=None) -> None:
    holder = button_container if button_container is not None else st
    if holder.button("删除招聘事件", key=f"delete_recruitment_event_{event_id}"):
        st.session_state["delete_event_pending"] = event_id
        st.rerun()
    if st.session_state.get("delete_event_pending") != event_id:
        return
    st.warning("确认后只删除这条招聘事件详情；已记录的阶段时间节点会保留。删除的详情只能从备份恢复。")
    confirm, cancel = st.columns(2)
    if confirm.button("确认删除招聘事件", key=f"confirm_delete_event_{event_id}"):
        if safe_action(lambda: store.delete_event(event_id), "招聘事件详情已删除；时间节点仍保留"):
            st.session_state["delete_event_pending"] = None
            st.rerun()
    if cancel.button("取消删除", key=f"cancel_delete_event_{event_id}"):
        st.session_state["delete_event_pending"] = None
        st.rerun()


def render_unlinked_events(store: Store) -> None:
    legacy = [row for row in store.list_events() if not row["stage_id"]]
    if not legacy:
        return
    stages = store.query("""SELECT x.id,x.application_id,p.id AS position_id,p.company_id,
                          p.title AS position_title,c.name AS company_name,s.name AS status_name
                          FROM application_stages x JOIN applications a ON a.id=x.application_id
                          JOIN positions p ON p.id=a.position_id JOIN companies c ON c.id=p.company_id
                          JOIN status_definitions s ON s.id=x.status_id
                          WHERE a.archived_at_utc IS NULL AND p.archived_at_utc IS NULL
                          AND c.archived_at_utc IS NULL ORDER BY x.entered_at_utc DESC""")
    with st.expander(f"未关联申请阶段的历史招聘详情（{len(legacy)} 条）"):
        st.caption("这些旧记录原先没有指定申请阶段，请核对后手动关联；原内容不会自动归入其他申请。")
        for event in legacy:
            with st.container(border=True):
                st.markdown(f"**{html.escape(event['title'])}** · {html.escape(event['type'])}")
                st.write(f"链接或地点：{event['location_or_link'] or '未填写'}")
                st.write(f"具体要求：{event['requirements'] or '未填写'}")
                st.write(f"备注：{event['notes'] or '未填写'}")
                render_event_delete_controls(store, event["id"])
                candidates = [stage for stage in stages if stage["company_id"] == event["company_id"]
                              and (not event["position_id"] or stage["position_id"] == event["position_id"])
                              and (not event["application_id"] or stage["application_id"] == event["application_id"])
                              and (event["type"] not in ("测评", "AI 面试", "视频面试") or
                                   stage["status_name"] in {
                                       "测评": ("测评准备", "测评/AI 面试准备"),
                                       "AI 面试": ("AI 面试准备", "测评/AI 面试准备"),
                                       "视频面试": ("视频面试准备",),
                                   }[event["type"]])]
                if not candidates:
                    st.info("暂无匹配的申请阶段；先建立对应申请和阶段，详情会继续保留在这里。")
                    continue
                stage_map = {row["id"]: row for row in candidates}
                stage_id = st.selectbox("关联到申请阶段", list(stage_map), key=f"legacy_stage_{event['id']}",
                                        format_func=lambda value: (
                                            f"{stage_map[value]['company_name']} · {stage_map[value]['position_title']} · "
                                            f"{stage_map[value]['status_name']}"))
                if st.button("关联这条详情", key=f"attach_legacy_event_{event['id']}"):
                    target = stage_map[stage_id]
                    values = {field: event.get(field) for field in EVENT_FIELDS}
                    values.update(company_id=target["company_id"], position_id=target["position_id"],
                                  application_id=target["application_id"], stage_id=stage_id, node_id=None)
                    if values["precision"] == "时间":
                        values["event_at_utc"] = local_display(values["event_at_utc"])
                    if safe_action(lambda: store.save_event(values, event["id"]), "已关联到所选申请阶段"):
                        st.rerun()


def is_nested_page(page: str) -> bool:
    if page == "求职总览":
        return bool(st.session_state.get("overview_view"))
    if page == "招聘信息":
        return bool(st.session_state.get("company_editor_mode") or
                    st.session_state.get("company_catalog_id") or
                    st.session_state.get("position_editor_mode") or
                    st.session_state.get("position_duplicate_review"))
    if page == "申请进度":
        return bool(st.session_state.get("new_submission_mode") or st.session_state.get("application_selected") or
                    st.session_state.get("application_panel") == "new")
    if page == "个人资料库":
        return bool(st.session_state.get("profile_editor_mode"))
    return False


def page_route(page: str) -> tuple:
    """Identify actual page transitions without treating ordinary widget edits as navigation."""
    if page == "求职总览":
        return page, st.session_state.get("overview_view") or "首页"
    if page == "招聘信息":
        if st.session_state.get("position_duplicate_review"):
            return page, "岗位重复检查"
        if st.session_state.get("company_editor_mode"):
            return page, "公司资料", st.session_state.get("company_selected")
        company_id = st.session_state.get("company_catalog_id")
        if st.session_state.get("position_editor_mode"):
            return page, "岗位资料", company_id, st.session_state.get("position_selected")
        return (page, "岗位名录", company_id) if company_id else (page, "公司列表")
    if page == "申请进度":
        if st.session_state.get("new_submission_mode"):
            return page, "新建投递", st.session_state.get("submission_position")
        application_id = st.session_state.get("application_selected")
        if application_id:
            return page, "申请详情", application_id, st.session_state.get("application_panel") or "detail"
        return page, "申请列表", st.session_state.get("application_panel") == "new"
    if page == "个人资料库":
        return page, "编辑" if st.session_state.get("profile_editor_mode") else "列表"
    return page,


def scroll_to_top_on_navigation(page: str) -> None:
    route = page_route(page)
    if st.session_state.get("_last_page_route") == route:
        return
    st.session_state["_last_page_route"] = route
    serial = st.session_state.get("_page_route_serial", 0) + 1
    st.session_state["_page_route_serial"] = serial
    # Streamlit preserves the scroll offset across reruns even when a shorter
    # nested page replaces a long list. This script runs only for route changes.
    st.html(f"""<script>
    (() => {{
      const routeSerial = {serial};
      const reset = () => {{
        window.scrollTo(0, 0);
        for (const selector of ['[data-testid="stMain"]', '[data-testid="stAppViewContainer"]']) {{
          const scroller = document.querySelector(selector);
          if (scroller) scroller.scrollTop = 0;
        }}
      }};
      reset();
      requestAnimationFrame(reset);
      setTimeout(reset, 80);
      setTimeout(reset, 240);
    }})();
    </script>""", unsafe_allow_javascript=True)


def page_companies(store: Store):
    if st.session_state.get("position_duplicate_review"):
        page_position_duplicate_review(store)
        return
    editor = st.session_state.get("company_editor_mode", False)
    catalog_id = st.session_state.get("company_catalog_id")
    if catalog_id and not editor:
        page_positions(store, catalog_id)
        return
    st.title("公司资料" if editor else "招聘信息")
    if not editor:
        bulk_import_panel(store)
        st.button("检查已有岗位重复", on_click=lambda: st.session_state.update(position_duplicate_review=True))
        st.button("＋ 新增公司", type="primary",
                  on_click=lambda: st.session_state.update(company_editor_mode=True, company_selected=None))
        search = st.text_input("搜索公司", key="company_search")
        archived = st.checkbox("查看归档公司", key="company_archived")
        rows = store.list_companies(include_archived=archived, search=search)
        rows.sort(key=lambda row: (company_sort_key(row["name"]), row["name"].casefold(), row["id"]))
        reset_card_page_on_filter_change("company_cards_page", (search, archived))
        if rows:
            with st.container(key="company_list_heading"):
                st.subheader(f"公司（{len(rows)}）")
            card_rows, card_page, card_page_count = visible_cards(rows, "company_cards_page", page_size=10)
            for item in card_rows:
                with st.container(key=f"company_card_{item['id']}", border=True):
                    english_name = (item["english_name"] or "").strip()
                    english = (f'<span class="cd-company-english">{html.escape(english_name)}</span>'
                               if english_name and english_name.casefold() != item["name"].strip().casefold() else "")
                    st.markdown(f'<div class="cd-company-heading"><strong>{html.escape(item["name"])}</strong>'
                                f'{english}<span class="cd-company-industry">'
                                f'{html.escape(item["industry"] or "行业未填写")}</span></div>',
                                unsafe_allow_html=True)
                    st.markdown(f'<div class="cd-company-city"><span>招聘城市</span>'
                                f'<span>{html.escape(item["cities"] or "未填写")}</span></div>',
                                unsafe_allow_html=True)
                    left, right = st.columns(2)
                    left.button("我要投递", key=f"open_company_catalog_{item['id']}",
                                use_container_width=True, disabled=bool(item["archived_at_utc"]),
                                on_click=open_company_catalog, args=(item["id"],))
                    if right.button("查看/编辑资料", key=f"edit_company_card_{item['id']}", use_container_width=True):
                        st.session_state["company_selected"] = item["id"]
                        st.session_state["company_editor_mode"] = True
                        st.rerun()
            card_page_controls("company_cards_page", card_page, card_page_count)
        else:
            st.info("当前没有公司。可新增公司或导入双表模板。")
        return
    if st.button("← 返回招聘信息"):
        st.session_state["company_editor_mode"] = False
        st.rerun()
    rows = store.list_companies(include_archived=True)
    opts, lookup, label = choices(rows, include_new=True)
    selected = st.selectbox("新增或选择公司", opts, format_func=label, key="company_selected")
    old = lookup.get(selected, {})
    prefix = f"company_{selected or 'new_' + str(st.session_state.get('new_company_nonce', 0))}"
    keys = {field: f"{prefix}_{field}" for field in ("name", "english_name", "industry", "website", "recruitment_url", "cities")}
    with st.form(f"{prefix}_form"):
        input_suggestions(prefix, company_suggestions, keys, in_form=True)
        name = st.text_input("公司名称 *", value=old.get("name") or "", key=keys["name"])
        english_name = st.text_input("公司英文名", value=old.get("english_name") or "", key=keys["english_name"])
        industry = st.text_input("行业", value=old.get("industry") or "", key=keys["industry"])
        website = st.text_input("官网", value=old.get("website") or "", key=keys["website"])
        recruitment_url = st.text_input("招聘官网", value=old.get("recruitment_url") or "", key=keys["recruitment_url"])
        cities = st.text_input("主要招聘城市", value=old.get("cities") or "", key=keys["cities"])
        research_notes = st.text_area("研究笔记", value=old.get("research_notes") or "", height=150)
        evaluation = st.text_area("个人评价", value=old.get("evaluation") or "")
        priority_options = options_with_current(("未评估", "高", "中", "低"), old.get("priority"))
        priority = st.selectbox("关注优先级", priority_options, index=priority_options.index(old.get("priority") or "未评估"))
        submitted = st.form_submit_button("保存公司", type="primary")
    if submitted:
        result = safe_action(lambda: store.save_company(dict(name=name, english_name=english_name,
                            industry=industry, website=website,
                            recruitment_url=recruitment_url, cities=cities, research_notes=research_notes,
                            evaluation=evaluation, priority=priority), selected), "公司已保存")
        if result:
            if selected is None:
                st.session_state["new_company_nonce"] = st.session_state.get("new_company_nonce", 0) + 1
            st.session_state["company_editor_mode"] = False
            st.rerun()
    if selected:
        st.subheader("关联记录")
        positions = store.list_positions(include_archived=True, company_id=selected)
        applications = store.query("""SELECT a.id,p.title,a.round_label,s.name AS status_name FROM applications a
                                      JOIN positions p ON p.id=a.position_id
                                      JOIN status_definitions s ON s.id=a.current_status_id WHERE p.company_id=?""", (selected,))
        events = store.query("SELECT title,type,status FROM recruitment_events WHERE company_id=?", (selected,))
        files = store.query("""SELECT f.filename,f.display_path FROM file_companies x
                               JOIN files f ON f.id=x.file_id WHERE x.company_id=?""", (selected,))
        st.write(f"岗位 {len(positions)} 条、申请 {len(applications)} 条、事件 {len(events)} 条、直接关联文件 {len(files)} 个")
        if positions:
            st.dataframe([{"岗位": p["title"], "招聘项目": p["recruitment_project"],
                           "批次": p["recruitment_batch"], "归档": bool(p["archived_at_utc"])} for p in positions], hide_index=True)
        if applications:
            st.dataframe([{"岗位": a["title"], "申请轮次": a["round_label"], "状态": a["status_name"]} for a in applications], hide_index=True)
        if events:
            st.dataframe([{"事件": e["title"], "类型": e["type"], "状态": e["status"]} for e in events], hide_index=True)
        if files:
            st.dataframe([{"文件": f["filename"], "原路径": f["display_path"]} for f in files], hide_index=True)
        if st.button("恢复公司" if old.get("archived_at_utc") else "归档公司"):
            safe_action(lambda: store.archive("companies", selected, not bool(old.get("archived_at_utc"))), "状态已更新")
            st.rerun()


def page_position_duplicate_review(store: Store) -> None:
    st.title("已有岗位重复检查")
    st.button("← 返回招聘信息", on_click=lambda: st.session_state.update(position_duplicate_review=False))
    notice = st.session_state.pop("position_duplicate_notice", None)
    if notice:
        st.success(notice)
    groups = position_duplicate_groups(store)
    groups.sort(key=lambda group: (not group["identical"], company_sort_key(group["company_name"]),
                                   company_sort_key(group["title"])))
    pending = sum(not group["reviewed"] for group in groups)
    identical = sum(group["identical"] for group in groups)
    st.info(f"发现 {len(groups)} 组同一招聘岗位的重复候选，其中 {identical} 组全部字段一致，{pending} 组待核对。")
    st.caption("仅在公司、岗位名称、招聘项目、批次、部门和城市均相同时列为候选；投递链接相同不会单独触发预警。")
    mode = st.segmented_control("查看范围", ("待核对", "已确认保留", "全部"),
                                default="待核对", required=True, key="position_duplicate_filter")
    if mode == "待核对":
        shown = [group for group in groups if not group["reviewed"]]
    elif mode == "已确认保留":
        shown = [group for group in groups if group["reviewed"]]
    else:
        shown = groups
    reset_card_page_on_filter_change("position_duplicate_page", (mode,))
    if not shown:
        st.success("当前范围没有需要显示的重复候选。")
        return
    page_groups, page, page_count = visible_cards(shown, "position_duplicate_page", page_size=5)
    st.caption(f"显示 {len(shown)} 组，每页 5 组。逐组核对后，可保留两条或归档无关联的多余岗位。")
    link_labels = {"applications": "申请", "recruitment_events": "招聘事件",
                   "todos": "待办", "file_positions": "文件"}
    for group in page_groups:
        label = (f"{group['company_name']} · {group['title']}（{len(group['positions'])} 条）"
                 f" · {'完全一致' if group['identical'] else '同名待核对'}"
                 f"{' · 已确认保留' if group['reviewed'] else ''}")
        with st.expander(label, key=f"position_duplicate_{group['review_key']}"):
            st.markdown(position_duplicate_comparison_html(group["positions"]), unsafe_allow_html=True)
            for index, position in enumerate(group["positions"], 1):
                links = group["links"][position["id"]]
                link_text = "、".join(f"{link_labels[table]} {count} 条" for table, count in links.items() if count)
                st.markdown(f"**记录 {index}** · {html.escape(position['recruitment_project'] or '招聘项目未填')}"
                            f" · {html.escape(position['recruitment_batch'] or '批次未填')}")
                st.caption(f"关联：{link_text or '无'}。归档后仍可在岗位名录勾选“查看已删除岗位”恢复。")
                edit_col, archive_col = st.columns(2)
                edit_col.button("查看／编辑岗位", key=f"review_edit_{position['id']}", width="stretch",
                                on_click=open_duplicate_position_editor,
                                args=(group["company_id"], position["id"]))
                if archive_col.button("归档这条岗位", key=f"review_archive_{position['id']}", width="stretch",
                                      disabled=bool(link_text),
                                      help="有关联记录的岗位请先核对，不能从这里直接归档。" if link_text else None):
                    if safe_action(lambda: archive_unlinked_position(store, position["id"]), success=""):
                        st.session_state["position_duplicate_notice"] = "岗位已归档；其余岗位资料没有改变。"
                        st.rerun()
            if group["reviewed"]:
                review_label = "重新列入待核对"
            else:
                review_label = "确认这些是不同岗位，全部保留"
            if st.button(review_label, key=f"review_keep_{group['review_key']}"):
                if safe_action(lambda: set_group_reviewed(store, group["positions"], not group["reviewed"]), success=""):
                    st.session_state["position_duplicate_notice"] = (
                        "已重新列入待核对。" if group["reviewed"] else "已标记为核对后保留；岗位资料没有改变。")
                    st.rerun()
    card_page_controls("position_duplicate_page", page, page_count)


def page_positions(store: Store, company_id: str):
    editor = st.session_state.get("position_editor_mode", False)
    company = store.get("companies", company_id)
    if not company:
        close_company_catalog()
        st.warning("这家公司已不存在，请返回公司列表。")
        return
    st.title(f"{company['name']} · {'岗位资料' if editor else '岗位名录'}")
    if not editor:
        st.button("← 返回公司列表", on_click=close_company_catalog)
        st.button("＋ 新增岗位", type="primary", on_click=open_new_position, args=(company_id,))
        notice = st.session_state.pop("position_notice", None)
        if notice:
            st.success(notice)
        search = st.text_input("搜索这家公司的岗位", key="position_search")
        archived = st.checkbox("查看已删除岗位", key="position_archived")
        hide_overdue = st.checkbox("不显示已逾期岗位", key="position_hide_overdue")
        rows = store.list_positions(include_archived=archived, search=search, company_id=company_id)
        priority_values = tuple(dict.fromkeys(("全部", "高", "中", "低", "未评估") +
                                           tuple(x["priority"] for x in rows if x["priority"])))
        priority_filter = st.selectbox("按优先级筛选", priority_values)
        sort_by = st.selectbox("排序", ("最近更新", "截止日期从近到远", "岗位名称"))
        filters = (company_id, search, archived, hide_overdue, priority_filter, sort_by)
        if st.session_state.get("position_focus_id"):
            focused_filters = st.session_state.get("position_focus_filters")
            if focused_filters is None:
                st.session_state["position_focus_filters"] = filters
            elif focused_filters != filters:
                st.session_state["position_focus_id"] = None
                st.session_state["position_focus_filters"] = None
        if priority_filter != "全部":
            rows = [x for x in rows if x["priority"] == priority_filter]
        if hide_overdue:
            rows = [x for x in rows if urgency(x["deadline_on"])["label"] != "已逾期"]
        if sort_by == "截止日期从近到远":
            rows.sort(key=lambda x: (not bool(x["deadline_on"]), x["deadline_on"] or "9999-12-31"))
        elif sort_by == "岗位名称":
            rows.sort(key=lambda x: (company_sort_key(x["title"]), x["title"].casefold()))
        focus_id = st.session_state.get("position_focus_id")
        if focus_id and any(row["id"] == focus_id for row in rows):
            rows.sort(key=lambda row: row["id"] != focus_id)
        reset_card_page_on_filter_change("position_cards_page", filters)
        if rows:
            st.subheader(f"岗位条目（{len(rows)}）")
            if focus_id and rows[0]["id"] == focus_id:
                st.caption("刚查看的岗位已排在最前；点击它下方的“我要投递”即可记录投递。")
            else:
                st.caption("查看岗位资料可核对专业与毕业时间要求；选择“我要投递”记录本次投递。")
            card_rows, card_page, card_page_count = visible_cards(rows, "position_cards_page", page_size=10)
            for item in card_rows:
                with st.container(border=True):
                    st.markdown(f'<div class="cd-position-title">{html.escape(item["title"])}</div>',
                                unsafe_allow_html=True)
                    st.caption(" · ".join(filter(None, (item["recruitment_project"],
                                                        item["recruitment_batch"], item["city"]))))
                    st.markdown(f"{urgency_badge(urgency(item['deadline_on']))}　"
                                f"截止：{html.escape(item['deadline_on'] or '未知')}　"
                                f"优先级：{html.escape(item['priority'] or '未评估')}", unsafe_allow_html=True)
                    action_submit, action_edit, action_delete = st.columns(3)
                    action_submit.button("我要投递", key=f"open_position_submission_{item['id']}",
                                         use_container_width=True, disabled=bool(item["archived_at_utc"]),
                                         on_click=open_new_submission, args=(company_id, item["id"]))
                    action_edit.button("查看／编辑岗位", key=f"edit_position_card_{item['id']}",
                                       use_container_width=True, on_click=open_position_editor,
                                       args=(company_id, item["id"]))
                    removed = bool(item["archived_at_utc"])
                    if action_delete.button("恢复岗位" if removed else "删除岗位",
                                            key=f"restore_position_card_{item['id']}" if removed else f"delete_position_card_{item['id']}",
                                            use_container_width=True,
                                            help="仅从工作列表移除，可在“查看已删除岗位”中恢复；申请历史保留。" if not removed else None):
                        if safe_action(lambda: store.archive("positions", item["id"], not removed), success=""):
                            st.session_state["position_notice"] = "岗位已恢复。" if removed else "岗位已移至已删除岗位，可随时恢复。"
                            st.rerun()
            card_page_controls("position_cards_page", card_page, card_page_count)
        else:
            st.info("当前筛选条件下没有岗位。")
        return
    back_label = ("← 返回重复检查" if st.session_state.get("position_return_to_duplicate_review") else
                  "← 返回求职总览" if st.session_state.get("position_return_to_overview") else "← 返回岗位名录")
    st.button(back_label, on_click=close_position_editor)
    rows = store.list_positions(include_archived=True, company_id=company_id)
    opts, lookup, _ = choices(rows, include_new=True)
    selected = st.selectbox("新增或选择岗位", opts, key="position_selected",
                            format_func=lambda x: "＋ 新增" if x is None else " · ".join(filter(None, (
                                lookup[x]["company_name"], lookup[x]["recruitment_project"],
                                lookup[x]["title"], lookup[x]["recruitment_batch"]))))
    old = lookup.get(selected, {})
    if selected and st.session_state.get("position_return_to_overview"):
        st.button("我要投递", key=f"overview_position_to_catalog_{selected}", type="primary",
                  disabled=bool(old.get("archived_at_utc")),
                  help="先在岗位名录中定位这条岗位，再记录投递。",
                  on_click=show_position_in_catalog, args=(selected,))
    prefix = f"position_{selected or 'new_' + str(st.session_state.get('new_position_nonce', 0))}"
    keys = {field: f"{prefix}_{field}" for field in ("title", "recruitment_project", "department", "city",
            "recruitment_batch", "major_requirements", "graduation_time_requirement", "deadline_on", "apply_url")}
    with st.form(f"{prefix}_form"):
        input_suggestions(prefix, position_suggestions, keys, "粘贴 JD 或招聘文字辅助填写", in_form=True)
        st.caption(f"所属公司：{company['name']}")
        recruitment_project = st.text_input("招聘项目名称", value=old.get("recruitment_project") or "", key=keys["recruitment_project"],
                                            placeholder="例如：2026 届校园招聘")
        title = st.text_input("岗位名称 *", value=old.get("title") or "", key=keys["title"])
        a, b, c = st.columns(3)
        department = a.text_input("部门", value=old.get("department") or "", key=keys["department"])
        city = b.text_input("城市", value=old.get("city") or "", key=keys["city"])
        recruitment_batch = c.text_input("招聘批次", value=old.get("recruitment_batch") or "", key=keys["recruitment_batch"])
        major_requirements = st.text_input("专业要求", value=old.get("major_requirements") or "",
                                            key=keys["major_requirements"], placeholder="例如：计算机、统计学相关专业")
        graduation_time_requirement = st.text_input("毕业时间要求", value=old.get("graduation_time_requirement") or "",
                                                      key=keys["graduation_time_requirement"], placeholder="例如：2026 年毕业")
        deadline_on = st.text_input("截止日期 YYYY-MM-DD（可空）", value=old.get("deadline_on") or "", key=keys["deadline_on"])
        apply_url = st.text_input("投递链接", value=old.get("apply_url") or "", key=keys["apply_url"])
        jd = st.text_area("岗位描述 JD", value=old.get("jd") or "", height=230)
        interest_options = options_with_current(("未评估", "高", "中", "低"), old.get("interest"))
        interest = st.selectbox("兴趣程度", interest_options, index=interest_options.index(old.get("interest") or "未评估"))
        position_priority_options = options_with_current(("未评估", "高", "中", "低"), old.get("priority"))
        priority = st.selectbox("申请优先级", position_priority_options,
                                index=position_priority_options.index(old.get("priority") or "未评估"))
        match_options = options_with_current(("未评估", "高", "中", "低"), old.get("match_rating"))
        match_rating = st.selectbox("人工判断匹配度", match_options, index=match_options.index(old.get("match_rating") or "未评估"))
        strengths = st.text_area("个人优势", value=old.get("strengths") or "")
        gaps = st.text_area("能力短板", value=old.get("gaps") or "")
        questions = st.text_area("待核实事项", value=old.get("questions") or "")
        notes = st.text_area("备注", value=old.get("notes") or "")
        submitted = st.form_submit_button("保存岗位", type="primary")
    if submitted:
        data = dict(company_id=company_id, title=title, recruitment_project=recruitment_project,
                    department=department, city=city, recruitment_batch=recruitment_batch,
                    major_requirements=major_requirements,
                    graduation_time_requirement=graduation_time_requirement,
                    deadline_on=deadline_on, apply_url=apply_url,
                    jd=jd, interest=interest, priority=priority, match_rating=match_rating,
                    strengths=strengths, gaps=gaps, questions=questions, notes=notes)
        if safe_action(lambda: store.save_position(data, selected), "岗位已保存"):
            if selected is None:
                st.session_state["new_position_nonce"] = st.session_state.get("new_position_nonce", 0) + 1
            if st.session_state.get("position_return_to_duplicate_review"):
                close_position_editor()
            else:
                st.session_state["position_editor_mode"] = False
            st.rerun()
    if selected:
        if old.get("jd"):
            with st.expander("阅读完整 JD"):
                st.text(old["jd"])
        if st.button("恢复岗位" if old.get("archived_at_utc") else "归档岗位"):
            safe_action(lambda: store.archive("positions", selected, not bool(old.get("archived_at_utc"))), "状态已更新")
            st.rerun()


def page_new_submission(store: Store):
    st.title("新建投递事件")
    st.caption("先核对公司和岗位，再记录实际投递时间。保存后可在“申请进度”继续添加测评、面试等阶段信息。")
    st.button("← 返回岗位名录", on_click=return_to_company_catalog)
    companies = store.list_companies()
    if not companies:
        st.info("请先新增公司和岗位。")
        return
    company_ids = [item["id"] for item in companies]
    if st.session_state.get("submission_company") not in company_ids:
        st.session_state["submission_company"] = company_ids[0]
    company_id = st.selectbox("所属公司 *", company_ids, key="submission_company",
                              format_func=lambda value: next(item["name"] for item in companies if item["id"] == value))
    positions = store.list_positions(company_id=company_id)
    if not positions:
        st.info("这家公司还没有可用岗位。先新增岗位，再回来记录投递。")
        st.button("到公司岗位名录新增岗位", on_click=open_new_position, args=(company_id,))
        return
    position_ids = [item["id"] for item in positions]
    if st.session_state.get("submission_position") not in position_ids:
        st.session_state["submission_position"] = position_ids[0]
    position_id = st.selectbox("所属岗位 *", position_ids, key="submission_position",
                               format_func=lambda value: next(" · ".join(filter(None, (
                                   item["title"], item["recruitment_project"], item["recruitment_batch"])))
                                   for item in positions if item["id"] == value))
    position = next(item for item in positions if item["id"] == position_id)
    st.info(f"已选：{position['company_name']} · {position['title']}；投递链接：{position['apply_url'] or '未填写'}")
    precision = st.radio("实际投递时间怎么记录", ("填写日期", "填写具体时间", "明确未知"),
                         key="submission_precision", horizontal=True)
    with st.form(f"new_submission_{position_id}"):
        round_label = st.text_input("申请轮次／批次", value=position["recruitment_batch"] or "",
                                    key=f"submission_round_{position_id}")
        current_day = datetime.now(SHANGHAI).date()
        submitted_on = st.date_input("实际投递日期", value=current_day) if precision == "填写日期" else None
        submitted_at_date = st.date_input("投递日期", value=current_day) if precision == "填写具体时间" else None
        submitted_at_time = st.time_input("投递时间", value=time(12, 0)) if precision == "填写具体时间" else None
        notes = st.text_area("投递备注", key=f"submission_notes_{position_id}",
                             placeholder="例如投递渠道、确认邮件、后续需要跟进的事项")
        submitted = st.form_submit_button("保存投递事件", type="primary")
    if submitted:
        mapped = {"填写日期": "日期", "填写具体时间": "时间", "明确未知": "明确未知"}[precision]
        local_time = (datetime.combine(submitted_at_date, submitted_at_time).isoformat(timespec="minutes")
                      if submitted_at_date and submitted_at_time else None)
        created = safe_action(lambda: store.create_submission(
            position_id, round_label, notes, precision=mapped,
            submitted_on=submitted_on.isoformat() if submitted_on else None,
            submitted_at=local_time), "投递事件已保存")
        if created:
            st.session_state["new_submission_mode"] = False
            st.session_state["application_selected"] = created
            st.rerun()


def page_applications(store: Store):
    if st.session_state.get("new_submission_mode"):
        page_new_submission(store)
        return
    st.title("申请进度")
    positions = store.list_positions()
    if not positions and not store.list_applications(include_archived=True):
        st.info("请先新增岗位，再建立申请。")
        render_unlinked_events(store)
        return
    nested = bool(st.session_state.get("application_selected") or
                  st.session_state.get("application_panel") == "new")
    if nested:
        rows = store.list_applications(include_archived=True)
        selected = st.session_state.get("application_selected")
        if selected and selected not in {row["id"] for row in rows}:
            st.session_state["application_panel"] = "detail"
            st.session_state["application_selected"] = None
            st.rerun()
    else:
        search = st.text_input("搜索申请对应的公司、岗位或批次")
        archived = st.checkbox("查看归档申请")
        rows = store.list_applications(include_archived=archived, search=search)
        status_filter = st.selectbox("按总标签筛选", ("全部", "未投递", "推进中", "已结束"))
        if status_filter != "全部":
            rows = [x for x in rows if x["status_category"] == status_filter]
        rows.sort(key=lambda row: (company_sort_key(row["company_name"]),
                                   row["company_name"].casefold(), row["position_title"].casefold(), row["id"]))
        position_counts = Counter(row["position_id"] for row in rows)
        reset_card_page_on_filter_change("application_cards_page", (search, archived, status_filter))
        page_rows, list_page, list_page_count = visible_cards(rows, "application_cards_page", page_size=10)
        time_details = application_time_details(store, page_rows)
        if rows:
            with st.container(key="application_list_panel"):
                st.subheader(f"申请列表（{len(rows)} 条）")
                st.caption("按公司名称首字母排序，每页 10 条；点击条目进入申请详情，鼠标停留可查看历次填写的时间。")
                for row in page_rows:
                    with st.container(key=f"application_entry_{row['id']}"):
                        parts = [row["company_name"], row["position_title"]]
                        if position_counts[row["position_id"]] > 1:
                            parts.append(row["round_label"] or "未标注批次")
                        parts.extend((row["status_name"], time_details[row["id"]]["current"]))
                        if row["archived_at_utc"]:
                            parts.append("已归档")
                        label = "　｜　".join(parts)
                        st.button(label, key=f"open_application_{row['id']}", use_container_width=True,
                                  on_click=open_application_detail, args=(row["id"],))
                        history = "<br>".join(html.escape(line) for line in time_details[row["id"]]["history"].splitlines())
                        st.markdown(f'<span class="cd-history-tooltip" role="note"><strong>历次时间记录</strong><br>{history}</span>',
                                    unsafe_allow_html=True)
                card_page_controls("application_cards_page", list_page, list_page_count)
        render_unlinked_events(store)
        selected = None
    lookup = {row["id"]: row for row in rows}
    if st.session_state.get("application_panel_owner") != selected:
        st.session_state["application_panel_owner"] = selected
        st.session_state["application_panel"] = "detail"
    panel = st.session_state.get("application_panel", "detail")
    if selected is None:
        if not positions:
            st.info("当前没有可新建申请的岗位；已有申请仍可从上方列表查看。")
            return
        if panel != "new":
            st.button("＋ 建立申请", on_click=lambda: st.session_state.update(application_panel="new"),
                      type="primary")
            return
        if st.button("返回申请列表"):
            st.session_state["application_panel"] = "detail"
            st.rerun()
        st.subheader("建立申请")
        with st.form("new_application"):
            position_id = st.selectbox("所属岗位 *", [x["id"] for x in positions],
                                       format_func=lambda x: next(f"{p['company_name']} · {p['title']} · {p['recruitment_batch'] or ''}" for p in positions if p["id"] == x))
            round_label = st.text_input("申请轮次/批次")
            notes = st.text_area("备注")
            submitted = st.form_submit_button("建立申请", type="primary")
        if submitted:
            created = safe_action(lambda: store.create_application(position_id, round_label, notes), "申请已建立，初始状态为待研究")
            if created:
                st.session_state["application_selected"] = created
                st.session_state["application_panel_owner"] = created
                st.session_state["application_panel"] = "detail"
                st.rerun()
        return
    app = lookup[selected]
    time_details = application_time_details(store, [app])
    if panel == "detail":
        return_view = st.session_state.get("application_return_view")
        back_label = {"submitted": "← 返回已投递申请", "progressing": "← 返回正在推进",
                      "schedule": "← 返回求职总览"}.get(return_view, "← 返回申请列表")
        st.button(back_label, on_click=close_application_detail)
    st.subheader(f"{app['company_name']} · {app['position_title']}")
    st.caption(f"当前状态：{app['status_name']}（{app['status_category']}） · "
               f"当前状态时间：{time_details[selected]['current']}")
    if panel == "detail":
        submitted_time = app["submitted_on"] or local_display(app["submitted_at_utc"])
        if not submitted_time:
            submitted_time = "时间未记录" if app["submission_precision"] == "明确未知" else "尚未投递"
        note_html = html.escape(app["notes"] or "暂无备注").replace("\n", "<br>")
        st.markdown(
            '<section class="cd-submission-card" aria-label="投递记录">'
            '<div class="cd-submission-title">投递记录</div>'
            '<div class="cd-submission-meta">'
            f'<div><span>申请轮次／批次</span><strong>{html.escape(app["round_label"] or "未填写")}</strong></div>'
            f'<div><span>实际投递时间</span><strong>{html.escape(submitted_time)}</strong></div>'
            '</div>'
            f'<div class="cd-submission-note"><span>投递备注</span><p>{note_html}</p></div>'
            '</section>', unsafe_allow_html=True)
        st.caption("改变状态记录申请进展；时间节点记录通知、截止或面试安排；测评／面试的链接、要求和长备注保存在“阶段详情与材料”。")
        actions = st.columns(4)
        for col, title, target in zip(actions, ("改变申请状态", "添加／编辑时间节点", "阶段详情与材料", "修改申请资料"),
                                      ("status", "node", "materials", "edit")):
            col.button(title, key=f"application_action_{target}_{selected}", use_container_width=True,
                       on_click=lambda value=target: st.session_state.update(application_panel=value))
        stages = store.stages(selected)
        st.caption(f"共 {len(stages)} 个申请阶段。事件保存详细内容；时间节点负责总览提醒。")
        with st.expander("查看状态历史与变更备注"):
            history = store.history(selected)
            st.markdown(chinese_table_html([{"原状态": h["old_name_snapshot"] or "（初始）",
                                            "新状态": h["new_name_snapshot"],
                                            "变更时间": local_display(h["changed_at_utc"]),
                                            "备注": h["note"]} for h in history]), unsafe_allow_html=True)
        if st.button("恢复申请" if app["archived_at_utc"] else "归档申请"):
            safe_action(lambda: store.archive("applications", selected, not bool(app["archived_at_utc"])), "状态已更新")
            st.rerun()
        return

    if panel != "stage_event":
        if st.button("← 返回申请详情"):
            st.session_state["application_panel"] = "detail"
            st.rerun()
    if panel == "edit":
        st.subheader("修改申请资料")
        with st.form(f"application_edit_{selected}"):
            round_label = st.text_input("申请轮次/批次", value=app["round_label"] or "")
            notes = st.text_area("申请备注", value=app["notes"] or "")
            submitted = st.form_submit_button("保存备注与轮次", type="primary")
        if submitted:
            if safe_action(lambda: store.update_application(selected, round_label=round_label, notes=notes), "申请已更新") is not None:
                st.session_state["application_panel"] = "detail"
                st.rerun()
        return
    if panel == "status":
        st.subheader("改变申请状态")
        st.info("选择新状态后，可同时填写该阶段的实际时间；未填写的时间仍可稍后到“添加／编辑时间节点”补录。")
        status_rows = store.statuses()
        if app["current_status_id"] not in {row["id"] for row in status_rows}:
            status_rows.extend(row for row in store.statuses(active_only=False)
                               if row["id"] == app["current_status_id"])
        if not status_rows:
            st.info("尚无可选状态。")
            return
        status_ids = [None] + [row["id"] for row in status_rows]
        status_id = st.selectbox("新状态", status_ids,
                                 format_func=lambda x: "请选择新状态" if x is None else next(
                                     f"{s['name']}（{s['category']}）{' · 当前状态' if s['id'] == app['current_status_id'] else ''}"
                                     for s in status_rows if s["id"] == x),
                                 key=f"new_status_{selected}")
        if status_id is None:
            st.caption("先选择新状态，再填写这次状态对应的时间。")
            return
        if status_id == app["current_status_id"]:
            st.info("这条申请当前已处于此状态。如需补录或修改该阶段时间，请返回申请详情，选择“添加／编辑时间节点”。")
            return
        selected_status = next(row for row in status_rows if row["id"] == status_id)
        time_fields = store.status_fields(status_id)
        if selected_status["name"] != "已投递" and not time_fields:
            time_fields = [{"label": "状态发生时间", "kind": "完成"}]
        stage_times = []
        st.markdown("**填写本次状态时间**")
        if selected_status["name"] == "已投递":
            st.caption("“已投递”的状态时间就是实际投递时间，请在下方填写或核对。")
        for field in time_fields:
            label = field["label"]
            mode = st.radio(f"{label}怎么记录", ("暂不填写", "填写日期", "填写具体时间"),
                            key=f"status_node_mode_{selected}_{status_id}_{label}", horizontal=True)
            if mode == "暂不填写":
                continue
            today = datetime.now(SHANGHAI).date()
            node_date = st.date_input(f"{label}日期", value=today,
                                      key=f"status_node_date_{selected}_{status_id}_{label}")
            node_time = (st.time_input(f"{label}时刻", value=time(12, 0),
                                       key=f"status_node_time_{selected}_{status_id}_{label}")
                         if mode == "填写具体时间" else None)
            stage_times.append({"label": label, "kind": field["kind"],
                                "precision": "时间" if node_time else "日期",
                                "value": (datetime.combine(node_date, node_time).isoformat(timespec="minutes")
                                          if node_time else node_date.isoformat())})
        st.markdown("**实际投递时间**")
        has_submission_record = app["submission_precision"] != "无"
        correct_submission = (st.checkbox("更正实际投递时间（可选）", key=f"correct_submission_{selected}")
                              if has_submission_record else True)
        precision = (st.radio("实际投递时间", ("沿用已有记录", "填写日期", "填写具体时间", "明确未知"),
                              key=f"status_precision_{selected}", horizontal=True)
                     if correct_submission else "沿用已有记录")
        with st.form(f"change_status_{selected}"):
            today = datetime.now(SHANGHAI).date()
            submitted_on = st.date_input("实际投递日期", value=today, key=f"status_date_{selected}") if precision == "填写日期" else None
            submitted_at_date = st.date_input("投递日期", value=today, key=f"status_datetime_date_{selected}") if precision == "填写具体时间" else None
            submitted_at_time = st.time_input("投递时间", value=time(12, 0), key=f"status_datetime_time_{selected}") if precision == "填写具体时间" else None
            note = st.text_input("这次状态变化的备注")
            changed = st.form_submit_button("确认变更状态", type="primary")
        if changed:
            if status_id is None:
                st.error("请先选择新状态")
                return
            mapped = {"填写日期": "日期", "填写具体时间": "时间", "明确未知": "明确未知"}.get(precision)
            at = datetime.combine(submitted_at_date, submitted_at_time).isoformat(timespec="minutes") if submitted_at_date and submitted_at_time else None
            result = safe_action(lambda: store.change_status(selected, status_id, note=note,
                                submission_precision=mapped, submitted_on=submitted_on.isoformat() if submitted_on else None,
                                submitted_at=at, stage_times=stage_times), "申请状态已更新")
            if result:
                st.session_state["application_panel"] = "detail"
                st.rerun()
        return

    stages = store.stages(selected)
    if not stages:
        st.info("此申请尚无阶段。")
        return
    stage_lookup = {x["id"]: x for x in stages}
    if panel == "stage_event":
        event_stage_id = st.session_state.get("stage_event_stage_id")
        if event_stage_id not in stage_lookup:
            st.session_state["application_panel"] = "materials"
            st.rerun()
        render_stage_event_editor(store, app, stage_lookup[event_stage_id])
        return
    stage_options = [x["id"] for x in stages][::-1]
    preferred_stage = app["current_stage_id"] if app["current_stage_id"] in stage_options else stage_options[0]
    stage_key = f"stage_for_{panel}_{selected}"
    if st.session_state.get(stage_key) not in stage_options:
        st.session_state[stage_key] = preferred_stage
    stage_id = st.selectbox("选择申请阶段", stage_options,
                            format_func=lambda x: f"{stage_lookup[x]['status_name']} · {local_display(stage_lookup[x]['entered_at_utc'])}",
                            key=stage_key)
    if panel == "node":
        st.subheader("添加／编辑时间节点")
        st.caption("节点只记录当前所选阶段的具体时间，不会改变申请状态。")
        nodes = store.nodes(stage_id)
        if nodes:
            st.markdown(chinese_table_html([{"节点": n["label"], "类型": n["kind"],
                                            "时间": n["date_on"] or local_display(n["at_utc"]) or "未知"}
                                           for n in nodes]), unsafe_allow_html=True)
        node_map = {n["label"]: n for n in nodes}
        node_label = st.selectbox("选择要编辑的节点", ["＋ 新增时间节点"] + list(node_map),
                                  key=f"node_choice_{stage_id}")
        chosen_node = node_map.get(node_label, {})
        precisions = ("未知", "日期", "时间")
        precision = st.selectbox("日期精度", precisions,
                                 index=precisions.index(chosen_node.get("precision") or "未知"),
                                 key=f"node_precision_{stage_id}_{node_label}")
        with st.form(f"node_form_{stage_id}_{node_label}"):
            label = st.text_input("节点名称 *", value="" if node_label.startswith("＋") else node_label)
            kinds = ("通知", "截止", "安排", "完成")
            kind = st.selectbox("节点类型", kinds, index=kinds.index(chosen_node.get("kind") or "截止"))
            value = ""
            if precision == "日期":
                existing_date = chosen_node.get("date_on")
                node_date = st.date_input("节点日期", value=date.fromisoformat(existing_date) if existing_date else datetime.now(SHANGHAI).date())
                value = node_date.isoformat()
            elif precision == "时间":
                existing_time = local_display(chosen_node.get("at_utc"))
                base_time = datetime.strptime(existing_time, "%Y-%m-%d %H:%M") if existing_time else datetime.now(SHANGHAI).replace(second=0, microsecond=0)
                node_date = st.date_input("节点日期", value=base_time.date())
                node_time = st.time_input("节点时间", value=base_time.time())
                value = datetime.combine(node_date, node_time).isoformat(timespec="minutes")
            node_notes = st.text_input("节点备注", value=chosen_node.get("notes") or "")
            node_saved = st.form_submit_button("保存时间节点", type="primary")
        if node_saved:
            if safe_action(lambda: store.save_node(stage_id, label, kind, precision, value, node_notes), "时间节点已保存"):
                st.rerun()
        return

    if panel == "materials":
        st.subheader(f"{stage_lookup[stage_id]['status_name']} · 阶段详情与材料")
        stage_events = [row for row in store.list_events(include_archived=True) if row["stage_id"] == stage_id]
        stage_todos = store.query("SELECT * FROM todos WHERE stage_id=? ORDER BY created_at_utc DESC", (stage_id,))
        st.caption(f"已记录 {len(stage_events)} 条测评／面试等详情。通知、截止和安排时间可到“添加／编辑时间节点”补录。")
        for event in stage_events:
            event_time = event["event_on"] or local_display(event["event_at_utc"]) or "未设置"
            deadline = event["deadline_on"] or local_display(event["deadline_at_utc"]) or "未设置"
            with st.expander(f"{event['title']} · {event['type']} · {event['status']}"):
                st.write(f"事件时间：{event_time}　测评截止时间：{deadline}")
                st.write(f"链接或地点：{event['location_or_link'] or '未填写'}")
                st.write(f"具体要求：{event['requirements'] or '未填写'}")
                st.write(f"备注：{event['notes'] or '未填写'}")
                if event["notice_text"]:
                    with st.expander("查看通知原文"):
                        st.text(event["notice_text"])
                edit_action, delete_action = st.columns(2)
                edit_action.button("编辑这条详情", key=f"edit_stage_event_{event['id']}",
                                   on_click=open_stage_event_editor, args=(selected, stage_id, event["id"]))
                render_event_delete_controls(store, event["id"], delete_action)
        st.button("＋ 新增测评／面试详情", key=f"new_stage_event_{stage_id}",
                  on_click=open_stage_event_editor, args=(selected, stage_id))
        if stage_todos:
            with st.expander(f"历史待办记录（{len(stage_todos)} 条，只读）"):
                for todo in stage_todos:
                    st.write(f"{todo['title']} · 截止：{todo['due_on'] or local_display(todo['due_at_utc']) or '未设置'}")
        stage_files = store.query("SELECT f.* FROM files f JOIN stage_files x ON x.file_id=f.id WHERE x.stage_id=?", (stage_id,))
        stage_profiles = store.query("SELECT p.* FROM profiles p JOIN stage_profiles x ON x.profile_id=p.id WHERE x.stage_id=?", (stage_id,))
        st.write("文件：" + ("、".join(f["filename"] for f in stage_files) or "暂无"))
        st.write("个人资料：" + ("、".join(p["title"] for p in stage_profiles) or "暂无"))
        available_files = list_files(store)
        if available_files:
            file_id = st.selectbox("关联一个已有文件", [x["id"] for x in available_files],
                                   format_func=lambda x: next(f["filename"] for f in available_files if f["id"] == x))
            if st.button("关联文件到本阶段"):
                safe_action(lambda: store.link_file(file_id, "stage", stage_id), "文件已关联")
                st.rerun()
        available_profiles = store.list_profiles()
        if available_profiles:
            profile_id = st.selectbox("关联一条个人资料", [x["id"] for x in available_profiles],
                                      format_func=lambda x: next(p["title"] for p in available_profiles if p["id"] == x))
            if st.button("关联资料到本阶段"):
                safe_action(lambda: store.link_profile(profile_id, stage_id=stage_id), "资料已关联")
                st.rerun()
        for file in stage_files:
            if st.button(f"解除文件关联：{file['filename']}", key=f"unlink_stage_file_{file['id']}"):
                safe_action(lambda file_id=file["id"]: store.link_file(file_id, "stage", stage_id, False), "关联已解除，原文件未删除")
                st.rerun()
        for profile in stage_profiles:
            if st.button(f"解除资料关联：{profile['title']}", key=f"unlink_stage_profile_{profile['id']}"):
                safe_action(lambda profile_id=profile["id"]: store.link_profile(profile_id, stage_id=stage_id, link=False), "关联已解除")
                st.rerun()


def render_stage_event_editor(store: Store, application: dict, stage: dict) -> None:
    stage_id = stage["id"]
    event_id = st.session_state.get("stage_event_selected")
    old = store.get("recruitment_events", event_id) if event_id else None
    if event_id and (not old or old["stage_id"] != stage_id):
        st.error("这条详情不属于所选申请阶段。")
        return
    old = old or {}
    if st.button("← 返回阶段详情"):
        st.session_state["application_panel"] = "materials"
        st.rerun()
    st.subheader("编辑测评／面试详情" if event_id else "新增测评／面试详情")
    st.caption(f"对应阶段：{stage['status_name']} · {application['company_name']} · {application['position_title']}")
    prefix = f"stage_event_{stage_id}_{event_id or 'new'}"
    stage_name = stage["status_name"]
    assessment_stage = stage_name in ("测评准备", "AI 面试准备", "视频面试准备", "测评/AI 面试准备")
    default_type = ({"测评准备": "测评", "AI 面试准备": "AI 面试",
                     "视频面试准备": "视频面试", "测评/AI 面试准备": "测评"}.get(stage_name)
                    or ("真人面试" if "面试" in stage_name else "其他"))
    types = (("测评", "AI 面试", "笔试", "其他") if stage_name == "测评/AI 面试准备" else
             (default_type, "其他") if assessment_stage else
             ("真人面试", "笔试", "结果通知", "其他"))
    types = options_with_current(types, old.get("type"))
    event_type = st.selectbox("事件类型 *", types,
                              index=types.index(old.get("type") or default_type), key=f"{prefix}_type")
    if event_type in ("测评", "AI 面试", "视频面试"):
        with st.expander("粘贴测评通知并解析"):
            pasted = st.text_area("通知原文", value=old.get("notice_text") or "",
                                  key=f"{prefix}_notice_text", height=160)
            notice_time_input = st.text_input("收到通知的准确时间（用于推算“ N 天内”），格式 YYYY-MM-DD HH:MM",
                                               key=f"{prefix}_notice_at")
            if st.button("解析通知", key=f"{prefix}_parse"):
                suggestions = notice_suggestions(pasted, notice_time_input or None)
                st.session_state[f"{prefix}_suggestions"] = suggestions
                for field in ("requirements", "location_or_link", "notes"):
                    if field in suggestions:
                        st.session_state[f"{prefix}_{field}"] = suggestions[field]["value"]
            for field, suggestion in st.session_state.get(f"{prefix}_suggestions", {}).items():
                st.caption(f"{field} → {suggestion['value']}；依据：{suggestion['source']}")
            st.caption("推算截止时间必须核对；解析不会自动保存或打开链接。")
    else:
        pasted = old.get("notice_text") or ""
    suggestions = st.session_state.get(f"{prefix}_suggestions", {})
    existing_nodes = store.nodes(stage_id) if event_type in ("测评", "AI 面试", "视频面试") else []
    existing_notice = next((n["date_on"] or local_display(n["at_utc"]) or ""
                            for n in existing_nodes if n["label"] == "通知时间"), "")
    schedule_label = ("视频面试时间" if event_type == "视频面试" else
                      "AI 面试截止时间" if event_type == "AI 面试" and stage_name == "AI 面试准备" else
                      "测评截止时间")
    schedule_kind = "安排" if event_type == "视频面试" else "截止"
    existing_deadline = next((n["date_on"] or local_display(n["at_utc"]) or ""
                              for n in existing_nodes if n["label"] == schedule_label), "")
    precisions = ("未知", "日期", "时间")
    precision = st.selectbox("事件时间精度", precisions,
                             index=precisions.index(old.get("precision") or "未知"),
                             key=f"{prefix}_precision")
    with st.form(f"{prefix}_form"):
        title = st.text_input("事件标题 *", value=old.get("title") or "")
        status_options = ("已安排", "已完成", "已取消")
        status = st.selectbox("事件状态", status_options,
                              index=status_options.index(old.get("status") or "已安排"))
        event_date = (st.text_input("事件日期 YYYY-MM-DD", value=old.get("event_on") or "")
                      if precision == "日期" else None)
        event_time = (st.text_input("事件时间 YYYY-MM-DD HH:MM",
                                    value=local_display(old.get("event_at_utc")))
                      if precision == "时间" else None)
        round_label = st.text_input("轮次", value=old.get("round_label") or "")
        location_or_link = st.text_input("地点或测评／会议链接",
                                         value=old.get("location_or_link") or "",
                                         key=f"{prefix}_location_or_link")
        requirements = st.text_area("内容与要求", value=old.get("requirements") or "",
                                    key=f"{prefix}_requirements")
        notes = st.text_area("备注", value=old.get("notes") or "", key=f"{prefix}_notes")
        if event_type in ("测评", "AI 面试", "视频面试"):
            st.markdown("**确认后写入该申请阶段的时间节点**")
            notice_candidate = (suggestions.get("notice_time") or {}).get("value") or existing_notice
            deadline_candidate = (suggestions.get("deadline") or {}).get("value") or existing_deadline
            notice_value = st.text_input("通知时间 YYYY-MM-DD 或 YYYY-MM-DD HH:MM", value=notice_candidate)
            save_notice_node = st.checkbox("确认写入通知时间")
            deadline_value = st.text_input(f"{schedule_label} YYYY-MM-DD 或 YYYY-MM-DD HH:MM", value=deadline_candidate)
            save_deadline_node = st.checkbox(f"确认写入{schedule_label}")
            st.caption("只修改详情时，已有节点时间会保留；填写新的时间后请勾选确认写入。")
            save_original = st.checkbox("将通知原文保存到详情", value=bool(old.get("notice_text")))
        else:
            save_original = False
        save_event = st.form_submit_button("保存阶段详情", type="primary")
    if not save_event:
        return
    stage_nodes = []
    if event_type in ("测评", "AI 面试", "视频面试"):
        for confirmed, label, kind, value, previous in (
                (save_notice_node, "通知时间", "通知", notice_value, existing_notice),
                (save_deadline_node, schedule_label, schedule_kind, deadline_value, existing_deadline)):
            value = value.strip()
            if confirmed and not value:
                st.error(f"请填写{label}，或取消勾选确认。")
                return
            if value and value != previous and not confirmed:
                st.error(f"已填写{label}，请勾选“确认写入{label}”，或清空输入框。")
                return
            if confirmed:
                stage_nodes.append(dict(label=label, kind=kind,
                                        precision="时间" if " " in value else "日期", value=value))
    data = dict(company_id=application["company_id"], position_id=application["position_id"],
                application_id=application["id"], stage_id=stage_id, node_id=old.get("node_id"),
                title=title, type=event_type, status=status, precision=precision,
                event_on=event_date, event_at_utc=event_time, round_label=round_label,
                location_or_link=location_or_link, requirements=requirements,
                notice_text=pasted if save_original else old.get("notice_text") if event_type not in ("测评", "AI 面试", "视频面试") else None,
                notes=notes)
    if safe_action(lambda: store.save_event(data, event_id, stage_nodes), "阶段详情已保存"):
        st.session_state["application_panel"] = "materials"
        st.session_state["stage_event_selected"] = None
        st.rerun()


def page_files(store: Store):
    st.title("文件中心")
    with st.expander("扫描或更新资料目录"):
        st.caption("只索引你明确指定的目录。扫描不移动、复制或打开原文件；默认不扫描子文件夹，不跟随符号链接或目录联接。")
        folder = st.text_input("资料目录路径", key="scan_folder", placeholder=r"例如 D:\求职资料\2026秋招")
        recursive = st.checkbox("扫描子文件夹", value=False)
        excludes = st.text_input("额外排除的文件夹名（逗号分隔）")
        if folder:
            st.info(f"将扫描的绝对路径：{Path(folder).expanduser().resolve(strict=False)}")
        st.caption("支持 PDF、DOCX、PPTX、XLSX、CSV、TXT、MD、PNG、JPG、JPEG；跳过 .venv、venv、.git、__pycache__ 和项目数据目录。")
        if st.button("开始扫描", type="primary"):
            if not folder.strip():
                st.error("请先填写资料目录路径")
            else:
                result = safe_action(lambda: scan_directory(store, folder, recursive=recursive,
                                      exclude=[x.strip() for x in excludes.split(",")]), "扫描完成")
                if result:
                    st.write(f"检查 {result['scanned']} 个支持的文件，新增 {result['added']} 个索引，更新 {result['updated']} 个索引。")
                    for error in result["errors"][:30]:
                        st.warning(error)
    st.subheader("文件索引")
    search = st.text_input("按文件名搜索")
    exts = ["全部"] + sorted(SUPPORTED)
    selected_ext = st.selectbox("文件类型", exts)
    companies = store.list_companies(include_archived=True)
    company_map = {x["id"]: x for x in companies}
    company_filter = st.selectbox("关联公司筛选", [None] + list(company_map),
                                  format_func=lambda x: "全部公司" if x is None else company_map[x]["name"])
    positions = store.list_positions(include_archived=True)
    position_map = {x["id"]: x for x in positions}
    position_filter = st.selectbox("关联岗位筛选", [None] + list(position_map),
                                   format_func=lambda x: "全部岗位" if x is None else f"{position_map[x]['company_name']} · {position_map[x]['title']}")
    files = list_files(store, search, None if selected_ext == "全部" else selected_ext, company_filter, position_filter)
    if files:
        st.dataframe([{"文件名": f["filename"], "类型": f["ext"], "大小（字节）": f["size_bytes"],
                       "可用状态": f["availability"], "版本": f["version_name"], "路径": f["display_path"]} for f in files],
                     hide_index=True, width="stretch")
    else:
        st.caption("没有符合条件的文件索引。")
        return
    file_map = {x["id"]: x for x in files}
    file_id = st.selectbox("查看文件详情", list(file_map), format_func=lambda x: file_map[x]["filename"])
    file = file_map[file_id]
    exists = safe_action(lambda: refresh_availability(store, file_id), "")
    st.code(file["display_path"], language=None)
    if exists is False:
        st.warning("原文件已失效或路径离线。可手动重新定位；索引和关联仍保留。")
    with st.expander("编辑文件标签、版本和备注"):
        with st.form(f"file_notes_{file_id}"):
            tags = st.text_input("标签，例如“简历”", value=file["tags"] or "")
            version_name = st.text_input("版本名称", value=file["version_name"] or "")
            notes = st.text_area("备注", value=file["notes"] or "")
            saved = st.form_submit_button("保存文件备注")
        if saved:
            safe_action(lambda: update_file_notes(store, file_id, tags, version_name, notes), "文件备注已保存")
            st.rerun()
    a, b = st.columns(2)
    if a.button("工作台内只读预览"):
        st.session_state["preview_file_id"] = file_id
    if b.button("打开原文件"):
        if not exists:
            st.error("路径不存在，请先重新定位文件")
        elif os.name != "nt":
            st.warning("当前环境不支持 Windows 默认程序打开；请复制上方路径")
        else:
            safe_action(lambda: os.startfile(file["display_path"]), "已调用 Windows 默认程序")
    if st.session_state.get("preview_file_id") == file_id:
        data = safe_action(lambda: preview(store, file_id), "")
        if data:
            if data["kind"] == "pdf":
                st.pdf(data["path"], height=650)
            elif data["kind"] == "image":
                st.image(data["path"], caption=file["filename"])
            elif data["kind"] == "text":
                st.text_area("只读内容预览", data["content"], height=400, disabled=True)
            elif data["kind"] == "sheets":
                for name, rows in data["sheets"].items():
                    st.write(f"工作表：{name}")
                    st.dataframe(rows, hide_index=True)
            if data.get("truncated"):
                st.caption("文件较长，仅展示前一部分。")
    with st.expander("关联或解除关联"):
        targets = {
            "company": [(x["id"], x["name"]) for x in companies],
            "position": [(x["id"], f"{x['company_name']} · {x['title']}") for x in positions],
            "application": [(x["id"], f"{x['company_name']} · {x['position_title']} · {x['round_label'] or ''}") for x in store.list_applications(include_archived=True)],
            "profile": [(x["id"], x["title"]) for x in store.list_profiles(include_archived=True)],
            "stage": [(x["id"], f"{x['status_name']} · {x['entered_at_utc']}") for app in store.list_applications(include_archived=True) for x in store.stages(app["id"])],
        }
        kinds = {"company": "公司", "position": "岗位", "application": "申请", "profile": "个人资料", "stage": "申请阶段"}
        kind = st.selectbox("关联类型", list(kinds), format_func=lambda x: kinds[x])
        if targets[kind]:
            target_lookup = dict(targets[kind])
            target_id = st.selectbox("目标记录", list(target_lookup), format_func=lambda x: target_lookup[x])
            if st.button("建立关联"):
                safe_action(lambda: store.link_file(file_id, kind, target_id), "关联已建立")
                st.rerun()
            if st.button("解除此关联"):
                safe_action(lambda: store.link_file(file_id, kind, target_id, False), "关联已解除，原文件未删除")
                st.rerun()
        else:
            st.caption("没有可关联的此类记录。")
    with st.expander("重新定位或移除索引"):
        new_path = st.text_input("原文件的新路径", key=f"new_path_{file_id}")
        if st.button("重新定位索引"):
            if safe_action(lambda: relocate(store, file_id, new_path), "索引已重新定位；关联保留") is not None:
                st.rerun()
        confirm_remove = st.checkbox("我确认只移除索引及关联，不删除原文件", key=f"confirm_remove_{file_id}")
        if st.button("移除文件索引", disabled=not confirm_remove):
            safe_action(lambda: remove_index(store, file_id), "索引和关联已移除，原文件未删除")
            st.session_state.pop("preview_file_id", None)
            st.rerun()


def page_profiles(store: Store):
    editor = st.session_state.get("profile_editor_mode", False)
    st.title("编辑个人资料" if editor else "个人资料库")
    if not editor:
      with st.expander("粘贴大段文字，识别多个候选条目"):
        pasted = st.text_area("只处理你主动粘贴的文字；清晰小标题可拆成多条", height=180)
        if st.button("识别候选条目"):
            st.session_state["profile_drafts"] = profile_suggestions(pasted)
        drafts = st.session_state.get("profile_drafts", [])
        if drafts:
            with st.form("profile_drafts_form"):
                candidates = []
                for index, draft in enumerate(drafts):
                    st.markdown(f"**候选 {index + 1}**")
                    take = st.checkbox("保存此条", value=True, key=f"take_profile_{index}")
                    category = st.selectbox("分类", PROFILE_CATEGORIES,
                                            index=PROFILE_CATEGORIES.index(draft["category"]) if draft["category"] in PROFILE_CATEGORIES else 0,
                                            key=f"draft_category_{index}")
                    title = st.text_input("标题", value=draft["title"], key=f"draft_title_{index}")
                    body = st.text_area("正文", value=draft["body"], key=f"draft_body_{index}")
                    candidates.append((take, category, title, body))
                save_drafts = st.form_submit_button("保存已勾选的条目")
            if save_drafts:
                count = 0
                for take, category, title, body in candidates:
                    if take and safe_action(lambda c=category,t=title,b=body: store.save_profile(dict(category=c, title=t, body=b)), "候选已保存"):
                        count += 1
                st.session_state.pop("profile_drafts", None)
                st.info(f"已保存 {count} 条确认的资料。")
                st.rerun()
    if not editor:
        st.button("＋ 新增资料", type="primary",
                  on_click=lambda: st.session_state.update(profile_editor_mode=True, profile_selected=None))
        search = st.text_input("搜索标题、正文或标签")
        archived = st.checkbox("查看归档资料")
        rows = store.list_profiles(include_archived=archived, search=search)
        if rows:
            st.markdown(chinese_table_html([{"分类": x["category"], "标题": x["title"],
                                            "标签": x["tags"],
                                            "状态": "已归档" if x["archived_at_utc"] else "使用中"}
                                           for x in rows]), unsafe_allow_html=True)
            profile_map = {row["id"]: row for row in rows}
            profile_id = st.selectbox("选择已有资料", list(profile_map),
                                      format_func=lambda x: profile_map[x]["title"])
            if st.button("查看／编辑所选资料"):
                st.session_state["profile_selected"] = profile_id
                st.session_state["profile_editor_mode"] = True
                st.rerun()
        return
    if st.button("← 返回个人资料库"):
        st.session_state["profile_editor_mode"] = False
        st.rerun()
    rows = store.list_profiles(include_archived=True)
    profile_map = {row["id"]: row for row in rows}
    profile_id = st.session_state.get("profile_selected")
    old = profile_map.get(profile_id, {})
    with st.form(f"profile_form_{profile_id or 'new'}"):
        category = st.selectbox("分类 *", PROFILE_CATEGORIES,
                                index=PROFILE_CATEGORIES.index(old["category"]) if old.get("category") in PROFILE_CATEGORIES else 0)
        title = st.text_input("标题 *", value=old.get("title") or "")
        body = st.text_area("正文 *", value=old.get("body") or "", height=260)
        tags = st.text_input("标签（逗号分隔）", value=old.get("tags") or "")
        notes = st.text_area("备注", value=old.get("notes") or "")
        start_on = st.text_input("开始日期 YYYY-MM-DD（可空）", value=old.get("start_on") or "")
        end_on = st.text_input("结束日期 YYYY-MM-DD（可空）", value=old.get("end_on") or "")
        with st.expander("可选 STAR 字段"):
            star_situation = st.text_area("情境", value=old.get("star_situation") or "")
            star_task = st.text_area("任务", value=old.get("star_task") or "")
            star_action = st.text_area("行动", value=old.get("star_action") or "")
            star_result = st.text_area("结果", value=old.get("star_result") or "")
        saved = st.form_submit_button("保存资料", type="primary")
    if saved:
        data = dict(category=category, title=title, body=body, tags=tags, notes=notes,
                    start_on=start_on, end_on=end_on, star_situation=star_situation,
                    star_task=star_task, star_action=star_action, star_result=star_result)
        if safe_action(lambda: store.save_profile(data, profile_id), "资料已保存"):
            st.session_state["profile_editor_mode"] = False
            st.rerun()
    if profile_id:
        applications = store.list_applications()
        if applications:
            app_map = {x["id"]: x for x in applications}
            app_id = st.selectbox("关联一条申请", list(app_map),
                                  format_func=lambda x: f"{app_map[x]['company_name']} · {app_map[x]['position_title']}")
            if st.button("关联到该申请"):
                safe_action(lambda: store.link_profile(profile_id, application_id=app_id), "资料已关联申请")
        if st.button("恢复资料" if old["archived_at_utc"] else "归档资料"):
            safe_action(lambda: store.archive("profiles", profile_id, not bool(old["archived_at_utc"])), "状态已更新")
            st.rerun()


def page_ai_trial(store: Store):
    st.title("AI 岗位分析试用")
    st.caption("试用智谱 GLM-4.7-Flash。只发送你选中的岗位字段；不会发送申请记录、简历文件，也不会自动修改或归档。")
    st.link_button("注册智谱并创建模型标准 API Key", "https://open.bigmodel.cn/", width="stretch")
    st.link_button("核对当前模型价格", "https://docs.bigmodel.cn/cn/guide/start/pricing", width="stretch")
    api_key = st.text_input("智谱模型标准 API Key", type="password", key="zhipu_trial_key",
                            help="仅在当前工作台会话中使用；不会写入数据库或导出文件。不要在聊天中发送密钥。")
    search = st.text_input("先搜索要分析的公司或岗位", key="zhipu_trial_search")
    rows = store.list_positions(search=search) if search.strip() else []
    if not search.strip():
        st.info("输入公司或岗位关键词，再选择 1 至 3 条岗位分析。")
        return
    lookup = {row["id"]: row for row in rows[:100]}
    if len(rows) > 100:
        st.caption("匹配结果较多，当前显示前 100 条；请缩小搜索范围。")
    selected = st.multiselect("选择岗位（最多 3 条）", list(lookup), max_selections=3,
                              format_func=lambda key: " · ".join(filter(None, (
                                  lookup[key]["company_name"], lookup[key]["title"],
                                  lookup[key]["department"], lookup[key]["city"]))))
    question = st.text_area("你想让 AI 分析什么？", value="比较这些岗位是否为同一招聘岗位，并说明应保留哪些记录及依据。",
                            height=90)
    if selected:
        with st.expander("查看将发送给智谱的岗位资料"):
            st.json(position_payload([lookup[key] for key in selected]))
    st.caption("分析内容仅供核对；本试用不联网检索招聘官网。模型免费政策与实际额度以智谱账户为准。")
    if st.button("发送所选岗位并分析", type="primary", disabled=not (api_key and selected and question.strip())):
        with st.spinner("正在分析所选岗位…"):
            try:
                result = analyze_positions(api_key, [lookup[key] for key in selected], question)
                result["selection"] = tuple(selected)
                result["question"] = question.strip()
                st.session_state["zhipu_trial_result"] = result
            except ValueError as exc:
                st.session_state.pop("zhipu_trial_result", None)
                st.error(str(exc))
    result = st.session_state.get("zhipu_trial_result")
    if result and result.get("selection") == tuple(selected) and result.get("question") == question.strip():
        st.subheader("分析建议")
        st.write(result["answer"])
        usage = result["usage"]
        if usage:
            st.caption(f"本次用量：输入 {usage.get('prompt_tokens', '—')}、输出 {usage.get('completion_tokens', '—')} tokens。")


def page_settings(store: Store):
    st.title("设置与备份")
    st.write("业务时区：**Asia/Shanghai**。应用只在本机回环地址提供页面。")
    st.code(str(store.path), language=None)
    st.caption("数据目录相对于项目位置计算，与启动时所在文件夹无关。关闭启动窗口即可停止应用。")
    if st.toggle("管理自定义申请状态和时间节点", key="show_status_settings"):
        status_rows = store.statuses(active_only=False)
        st.dataframe([{"状态": x["name"], "总标签": x["category"], "内置": bool(x["is_builtin"]),
                       "可选": bool(x["is_active"])} for x in status_rows], hide_index=True)
        with st.form("new_status_form"):
            name = st.text_input("新状态名称")
            category = st.selectbox("状态总标签 *", ("未投递", "推进中", "已结束"))
            fields_text = st.text_area("默认时间节点，每行“节点名称|类型”\n类型：通知、截止、安排、完成",
                                       placeholder="例如：通知时间|通知\n面试时间|安排")
            add = st.form_submit_button("添加状态")
        if add:
            try:
                fields = []
                for line in fields_text.splitlines():
                    if line.strip():
                        parts = [x.strip() for x in line.split("|", 1)]
                        if len(parts) != 2:
                            raise ValueError("时间节点每行须写成“节点名称|类型”")
                        fields.append((parts[0], parts[1]))
                if safe_action(lambda: store.add_status(name, category, fields), "自定义状态已添加"):
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        custom = [row for row in status_rows if not row["is_builtin"] and row["is_active"]]
        if custom:
            status_id = st.selectbox("停用一个自定义状态", [x["id"] for x in custom],
                                     format_func=lambda x: next(s["name"] for s in custom if s["id"] == x))
            if st.button("停用该状态（历史保留）"):
                safe_action(lambda: store.deactivate_status(status_id), "状态已停用，历史保留")
                st.rerun()
    st.subheader("导出")
    st.caption("JSON/CSV 是数据副本和 Excel 阅读文件，不是数据库恢复文件；不会包含被索引文件的正文。")
    export_dir = store.path.parent / "exports"
    if st.button("生成完整 JSON 导出"):
        path = safe_action(lambda: export_json(store.path, export_dir), "JSON 已导出")
        if path:
            st.code(str(path), language=None)
    if st.button("生成主要列表 CSV 导出"):
        paths = safe_action(lambda: export_csv(store.path, export_dir), "CSV 已导出")
        if paths:
            for path in paths:
                st.code(str(path), language=None)
    st.subheader("数据库备份")
    st.info("备份包含业务数据库、状态与时间节点、文件路径索引及关联；不包含外部简历/JD/图片原文件、虚拟环境或依赖。换电脑时须另行保管原文件。")
    backup_dir = store.path.parent / "backups"
    if st.button("创建一致性备份", type="primary"):
        path = safe_action(lambda: backup_database(store.path, backup_dir), "数据库备份已创建")
        if path:
            st.code(str(path), language=None)
    backups = sorted(backup_dir.glob("careerdesk-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.is_dir() else []
    if backups:
        with st.expander("查看、验证或恢复已有备份"):
            st.markdown(chinese_table_html([{"备份文件": x.name, "大小（字节）": x.stat().st_size}
                                           for x in backups]), unsafe_allow_html=True)
            candidate = st.selectbox("选择要恢复的备份", backups, format_func=lambda x: x.name)
            if st.button("先验证此备份"):
                result = safe_action(lambda: validate_backup(candidate), "备份完整；旧版备份将在恢复时自动升级")
                if result:
                    st.write(result)
            st.warning("恢复会替换当前工作数据。确认后系统先自动备份当前数据库；验证或恢复失败时保留原数据。")
            confirm = st.checkbox("我确认要用所选备份替换当前工作数据", key="restore_confirm")
            if st.button("确认恢复所选备份", disabled=not confirm):
                result = safe_action(lambda: restore_database(store.path, candidate, backup_dir), "恢复完成；原数据已自动备份")
                if result:
                    st.code(f"恢复前的自动备份：{result}", language=None)
                    st.session_state["restore_confirm"] = False
                    st.rerun()


def main():
    inject_theme()
    try:
        store = open_store(str(database_path()), max(Path(_core.__file__).stat().st_mtime_ns,
                                                     Path(_db.__file__).stat().st_mtime_ns))
    except Exception as exc:
        st.error(f"无法打开工作台数据库：{exc}")
        st.stop()
    sidebar_brand()
    if st.session_state.get("navigation") == "公司管理":
        st.session_state["navigation"] = "招聘信息"
    page = st.sidebar.radio("导航", PAGES, key="navigation")
    st.sidebar.caption("个人求职管理 · 本机运行")
    scroll_to_top_on_navigation(page)
    if not is_nested_page(page):
        banner(page)
    pages = {
        "求职总览": page_overview,
        "招聘信息": page_companies,
        "申请进度": page_applications,
        "AI 试用": page_ai_trial,
        "文件中心": page_files,
        "个人资料库": page_profiles,
        "设置与备份": page_settings,
    }
    pages[page](store)
    footer()


if __name__ == "__main__":
    main()
