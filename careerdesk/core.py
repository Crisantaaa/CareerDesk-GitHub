"""Business operations. Every multi-row change has one transaction."""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from .db import connect, initialize, transaction
from .timeutil import SHANGHAI

COMPANY_FIELDS = ("name", "english_name", "industry", "website", "recruitment_url", "cities", "research_notes", "evaluation", "priority")
POSITION_FIELDS = ("company_id", "title", "recruitment_project", "department", "city", "recruitment_batch", "major_requirements", "graduation_time_requirement", "jd", "apply_url", "deadline_on", "interest", "priority", "match_rating", "strengths", "gaps", "questions", "notes")
PROFILE_FIELDS = ("category", "title", "body", "tags", "notes", "start_on", "end_on", "star_situation", "star_task", "star_action", "star_result")
EVENT_FIELDS = ("title", "type", "status", "company_id", "position_id", "application_id", "stage_id", "node_id", "event_on", "event_at_utc", "precision", "round_label", "location_or_link", "requirements", "notice_text", "notes")
TODO_FIELDS = ("title", "company_id", "position_id", "application_id", "stage_id", "due_on", "due_at_utc", "precision", "priority", "completed_at_utc", "notes")
LINK_TABLES = {
    "company": ("file_companies", "company_id"),
    "position": ("file_positions", "position_id"),
    "application": ("file_applications", "application_id"),
    "profile": ("file_profiles", "profile_id"),
    "stage": ("stage_files", "stage_id"),
}


def uid() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def name_key(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def required(value, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field}不能为空")
    return result


def check_date(value, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}必须是 YYYY-MM-DD 日期") from exc


def local_to_utc(value, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError as exc:
        raise ValueError(f"{field}必须是有效日期时间") from exc


def _select(con: sqlite3.Connection, sql: str, args=()) -> list[dict]:
    return [dict(row) for row in con.execute(sql, args).fetchall()]


def _one(con: sqlite3.Connection, sql: str, args=()) -> dict | None:
    row = con.execute(sql, args).fetchone()
    return dict(row) if row else None


def _insert(con, table: str, values: dict) -> None:
    cols = ",".join(values)
    marks = ",".join("?" for _ in values)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def _update(con, table: str, row_id: str, values: dict) -> None:
    sets = ",".join(f"{key}=?" for key in values)
    con.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*values.values(), row_id))


def _applications_in_round(con, position_id: str, round_label: str) -> list[sqlite3.Row]:
    """Find active applications for the same position and normalized batch label."""
    key = name_key(str(round_label or ""))
    rows = con.execute("SELECT * FROM applications WHERE position_id=? AND archived_at_utc IS NULL",
                       (position_id,)).fetchall()
    return [row for row in rows if name_key(row["round_label"] or "") == key]


class Store:
    def __init__(self, path: Path | None = None):
        self.path = initialize(path)

    def query(self, sql: str, args=()) -> list[dict]:
        with closing(connect(self.path)) as con:
            return _select(con, sql, args)

    def one(self, sql: str, args=()) -> dict | None:
        with closing(connect(self.path)) as con:
            return _one(con, sql, args)

    def get(self, table: str, row_id: str) -> dict | None:
        allowed = {"companies", "positions", "applications", "recruitment_events", "todos", "profiles", "files", "application_stages"}
        if table not in allowed:
            raise ValueError("未知记录类型")
        return self.one(f"SELECT * FROM {table} WHERE id=?", (row_id,))

    def list_companies(self, include_archived=False, search="") -> list[dict]:
        where = "WHERE (name LIKE ? OR COALESCE(english_name,'') LIKE ? OR COALESCE(industry,'') LIKE ?)"
        if not include_archived:
            where += " AND archived_at_utc IS NULL"
        term = f"%{search.strip()}%"
        return self.query(f"SELECT * FROM companies {where} ORDER BY updated_at_utc DESC", (term, term, term))

    def save_company(self, data: dict, row_id: str | None = None) -> str:
        values = {key: data.get(key) for key in COMPANY_FIELDS}
        values["name"] = required(values["name"], "公司名称")
        values["name_key"] = name_key(values["name"])
        values["priority"] = values["priority"] or "未评估"
        stamp = now()
        with transaction(self.path) as con:
            duplicate = _one(con, "SELECT id FROM companies WHERE name_key=?", (values["name_key"],))
            if duplicate and duplicate["id"] != row_id:
                raise ValueError("已有相同名称的公司，包括已归档记录；请先查看或恢复")
            if row_id:
                if not _one(con, "SELECT id FROM companies WHERE id=?", (row_id,)):
                    raise ValueError("公司不存在")
                values["updated_at_utc"] = stamp
                _update(con, "companies", row_id, values)
            else:
                row_id = uid()
                values.update(id=row_id, created_at_utc=stamp, updated_at_utc=stamp)
                _insert(con, "companies", values)
        return row_id

    def list_positions(self, include_archived=False, search="", company_id=None) -> list[dict]:
        sql = """SELECT p.*,c.name AS company_name,c.archived_at_utc AS company_archived
                 FROM positions p JOIN companies c ON c.id=p.company_id
                 WHERE (p.title LIKE ? OR c.name LIKE ? OR COALESCE(c.english_name,'') LIKE ?
                 OR COALESCE(p.recruitment_project,'') LIKE ?
                 OR COALESCE(p.major_requirements,'') LIKE ?
                 OR COALESCE(p.graduation_time_requirement,'') LIKE ?)"""
        args: list = [f"%{search.strip()}%"] * 6
        if company_id:
            sql += " AND p.company_id=?"
            args.append(company_id)
        if not include_archived:
            sql += " AND p.archived_at_utc IS NULL AND c.archived_at_utc IS NULL"
        return self.query(sql + " ORDER BY p.updated_at_utc DESC", args)

    def save_position(self, data: dict, row_id: str | None = None) -> str:
        values = {key: data.get(key) for key in POSITION_FIELDS}
        values["company_id"] = required(values["company_id"], "所属公司")
        values["title"] = required(values["title"], "岗位名称")
        values["deadline_on"] = check_date(values["deadline_on"], "岗位截止日期")
        for key in ("interest", "priority", "match_rating"):
            values[key] = values[key] or "未评估"
        with transaction(self.path) as con:
            company = _one(con, "SELECT * FROM companies WHERE id=?", (values["company_id"],))
            if not company or company["archived_at_utc"]:
                raise ValueError("所属公司不存在或已归档")
            stamp = now()
            if row_id:
                if not _one(con, "SELECT id FROM positions WHERE id=?", (row_id,)):
                    raise ValueError("岗位不存在")
                values["updated_at_utc"] = stamp
                _update(con, "positions", row_id, values)
            else:
                row_id = uid()
                values.update(id=row_id, created_at_utc=stamp, updated_at_utc=stamp)
                _insert(con, "positions", values)
        return row_id

    def statuses(self, active_only=True) -> list[dict]:
        sql = "SELECT * FROM status_definitions"
        if active_only:
            sql += " WHERE is_active=1"
        return self.query(sql + " ORDER BY sort_order,name")

    def status_fields(self, status_id: str) -> list[dict]:
        return self.query("SELECT * FROM status_time_fields WHERE status_id=? ORDER BY sort_order,label", (status_id,))

    def add_status(self, name: str, category: str, fields: list[tuple[str, str]]) -> str:
        name = required(name, "状态名称")
        if category not in ("未投递", "推进中", "已结束"):
            raise ValueError("请选择状态总标签")
        if len({name_key(label) for label, _ in fields}) != len(fields):
            raise ValueError("同一状态的时间节点名称不能重复")
        with transaction(self.path) as con:
            if _one(con, "SELECT id FROM status_definitions WHERE name=?", (name,)):
                raise ValueError("状态名称已存在")
            row_id = uid()
            order = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM status_definitions").fetchone()[0]
            _insert(con, "status_definitions", dict(id=row_id, name=name, category=category, is_builtin=0, is_active=1, sort_order=order))
            for index, (label, kind) in enumerate(fields):
                if kind not in ("通知", "截止", "安排", "完成"):
                    raise ValueError("时间节点类型无效")
                _insert(con, "status_time_fields", dict(id=uid(), status_id=row_id, label=required(label, "节点名称"), kind=kind, sort_order=index))
        return row_id

    def deactivate_status(self, status_id: str) -> None:
        with transaction(self.path) as con:
            row = _one(con, "SELECT * FROM status_definitions WHERE id=?", (status_id,))
            if not row or row["is_builtin"]:
                raise ValueError("只允许停用自定义状态")
            con.execute("UPDATE status_definitions SET is_active=0 WHERE id=?", (status_id,))

    def list_applications(self, include_archived=False, search="") -> list[dict]:
        sql = """SELECT a.*,p.title AS position_title,p.company_id,c.name AS company_name,
                 s.name AS status_name,s.category AS status_category,
                 c.archived_at_utc AS company_archived,p.archived_at_utc AS position_archived
                 FROM applications a JOIN positions p ON p.id=a.position_id
                 JOIN companies c ON c.id=p.company_id
                 JOIN status_definitions s ON s.id=a.current_status_id
                 WHERE (p.title LIKE ? OR c.name LIKE ? OR COALESCE(a.round_label,'') LIKE ?)"""
        args = [f"%{search.strip()}%"] * 3
        if not include_archived:
            sql += " AND a.archived_at_utc IS NULL AND p.archived_at_utc IS NULL AND c.archived_at_utc IS NULL"
        return self.query(sql + " ORDER BY a.updated_at_utc DESC", args)

    def create_application(self, position_id: str, round_label="", notes="") -> str:
        with transaction(self.path) as con:
            position = _one(con, "SELECT p.*,c.archived_at_utc AS company_archived FROM positions p JOIN companies c ON c.id=p.company_id WHERE p.id=?", (position_id,))
            if not position or position["archived_at_utc"] or position["company_archived"]:
                raise ValueError("岗位不存在或已归档")
            if _applications_in_round(con, position_id, round_label):
                raise ValueError("这个岗位和申请轮次/批次已有申请；请打开已有记录继续填写。如确实是新一轮申请，请填写不同的轮次/批次。")
            status = _one(con, "SELECT * FROM status_definitions WHERE name='待研究'")
            stamp, app_id, stage_id = now(), uid(), uid()
            _insert(con, "applications", dict(id=app_id, position_id=position_id, round_label=round_label,
                    current_status_id=status["id"], current_stage_id=None, submission_precision="无", notes=notes,
                    created_at_utc=stamp, updated_at_utc=stamp))
            _insert(con, "application_stages", dict(id=stage_id, application_id=app_id, status_id=status["id"], entered_at_utc=stamp))
            con.execute("UPDATE applications SET current_stage_id=? WHERE id=?", (stage_id, app_id))
            _insert(con, "status_history", dict(id=uid(), application_id=app_id, old_status_id=None,
                    new_status_id=status["id"], old_name_snapshot=None, new_name_snapshot=status["name"],
                    stage_id=stage_id, changed_at_utc=stamp, note="初始状态"))
        return app_id

    def create_submission(self, position_id: str, round_label="", notes="", *,
                          precision: str = "日期", submitted_on=None, submitted_at=None) -> str:
        """Create a submitted application and its initial history in one transaction."""
        if precision not in ("日期", "时间", "明确未知"):
            raise ValueError("请选择实际投递时间的记录方式")
        date_value = check_date(submitted_on, "投递日期") if precision == "日期" else None
        time_value = local_to_utc(submitted_at, "投递时间") if precision == "时间" else None
        if precision == "日期" and not date_value:
            raise ValueError("请选择实际投递日期")
        if precision == "时间" and not time_value:
            raise ValueError("请选择实际投递时间")
        with transaction(self.path) as con:
            position = _one(con, """SELECT p.*,c.archived_at_utc AS company_archived FROM positions p
                                  JOIN companies c ON c.id=p.company_id WHERE p.id=?""", (position_id,))
            if not position or position["archived_at_utc"] or position["company_archived"]:
                raise ValueError("岗位不存在或已归档")
            for existing in _applications_in_round(con, position_id, round_label):
                same_submission = (existing["submission_precision"] == precision
                                   and existing["submitted_on"] == date_value
                                   and existing["submitted_at_utc"] == time_value)
                if existing["submission_precision"] == "无" or same_submission:
                    raise ValueError("这个岗位、申请轮次/批次和投递时间已有申请；请打开已有记录编辑。如确实再次投递，请填写不同的轮次/批次。")
            status = _one(con, "SELECT * FROM status_definitions WHERE name='已投递' AND is_active=1")
            if not status:
                raise ValueError("找不到可用的“已投递”状态")
            stamp, app_id, stage_id = now(), uid(), uid()
            _insert(con, "applications", dict(id=app_id, position_id=position_id, round_label=round_label,
                    current_status_id=status["id"], current_stage_id=None,
                    submission_precision=precision, submitted_on=date_value,
                    submitted_at_utc=time_value, notes=notes,
                    created_at_utc=stamp, updated_at_utc=stamp))
            _insert(con, "application_stages", dict(id=stage_id, application_id=app_id,
                    status_id=status["id"], entered_at_utc=stamp))
            con.execute("UPDATE applications SET current_stage_id=? WHERE id=?", (stage_id, app_id))
            _insert(con, "status_history", dict(id=uid(), application_id=app_id,
                    old_status_id=None, new_status_id=status["id"], old_name_snapshot=None,
                    new_name_snapshot=status["name"], stage_id=stage_id,
                    changed_at_utc=stamp, note="新建投递记录"))
        return app_id

    def update_application(self, app_id: str, *, round_label: str, notes: str) -> None:
        with transaction(self.path) as con:
            if not _one(con, "SELECT id FROM applications WHERE id=?", (app_id,)):
                raise ValueError("申请不存在")
            _update(con, "applications", app_id, dict(round_label=round_label, notes=notes, updated_at_utc=now()))

    def change_status(self, app_id: str, status_id: str, *, note="", submission_precision=None,
                      submitted_on=None, submitted_at=None, stage_times: list[dict] | None = None) -> bool:
        with transaction(self.path) as con:
            app = _one(con, "SELECT * FROM applications WHERE id=?", (app_id,))
            status = _one(con, "SELECT * FROM status_definitions WHERE id=? AND is_active=1", (status_id,))
            if not app or not status:
                raise ValueError("申请或状态不存在")
            if app["current_status_id"] == status_id:
                return False
            prior = _one(con, "SELECT * FROM status_definitions WHERE id=?", (app["current_status_id"],))
            precision = submission_precision or app["submission_precision"]
            date_value = check_date(submitted_on, "投递日期") if submission_precision == "日期" else app["submitted_on"]
            time_value = local_to_utc(submitted_at, "投递时间") if submission_precision == "时间" else app["submitted_at_utc"]
            if submission_precision == "日期":
                time_value = None
            if submission_precision == "时间":
                date_value = None
            if submission_precision == "明确未知":
                if app["submitted_on"] or app["submitted_at_utc"]:
                    # A later status change must not erase a known historical submission.
                    precision = app["submission_precision"]
                    date_value, time_value = app["submitted_on"], app["submitted_at_utc"]
                else:
                    date_value = time_value = None
            if submission_precision == "日期" and not date_value:
                raise ValueError("请选择实际投递日期")
            if submission_precision == "时间" and not time_value:
                raise ValueError("请选择实际投递时间")
            if status["category"] == "推进中" or status["name"] in ("已录用", "已拒绝"):
                if precision == "无":
                    raise ValueError("请填写实际投递日期/时间，或明确选择未知")
            fields = _select(con, "SELECT * FROM status_time_fields WHERE status_id=? ORDER BY sort_order,label", (status_id,))
            field_lookup = {field["label"]: field for field in fields}
            supplied_times = {}
            for item in stage_times or []:
                label = required(item.get("label"), "节点名称")
                field = field_lookup.get(label)
                if label in supplied_times or (field is None and (label != "状态发生时间" or fields)):
                    raise ValueError("状态时间节点无效或重复")
                kind = field["kind"] if field else "完成"
                if item.get("kind") != kind or item.get("precision") not in ("日期", "时间"):
                    raise ValueError("状态时间节点类型或精度无效")
                date_on = check_date(item.get("value"), label) if item["precision"] == "日期" else None
                at_utc = local_to_utc(item.get("value"), label) if item["precision"] == "时间" else None
                if not (date_on or at_utc):
                    raise ValueError(f"请填写{label}")
                supplied_times[label] = (item["precision"], date_on, at_utc)
            stamp, stage_id = now(), uid()
            con.execute("UPDATE application_stages SET exited_at_utc=? WHERE id=?", (stamp, app["current_stage_id"]))
            _insert(con, "application_stages", dict(id=stage_id, application_id=app_id,
                    status_id=status_id, entered_at_utc=stamp))
            _update(con, "applications", app_id, dict(current_status_id=status_id,
                    current_stage_id=stage_id, submission_precision=precision,
                    submitted_on=date_value, submitted_at_utc=time_value, updated_at_utc=stamp))
            _insert(con, "status_history", dict(id=uid(), application_id=app_id,
                    old_status_id=prior["id"], new_status_id=status_id,
                    old_name_snapshot=prior["name"], new_name_snapshot=status["name"],
                    stage_id=stage_id, changed_at_utc=stamp, note=note))
            for field in fields:
                node_precision, node_date, node_at = supplied_times.get(field["label"], ("未知", None, None))
                _insert(con, "stage_time_nodes", dict(id=uid(), stage_id=stage_id,
                        field_id=field["id"], label=field["label"], kind=field["kind"],
                        precision=node_precision, date_on=node_date, at_utc=node_at))
            if "状态发生时间" in supplied_times and "状态发生时间" not in field_lookup:
                node_precision, node_date, node_at = supplied_times["状态发生时间"]
                _insert(con, "stage_time_nodes", dict(id=uid(), stage_id=stage_id,
                        label="状态发生时间", kind="完成", precision=node_precision,
                        date_on=node_date, at_utc=node_at))
        return True

    def history(self, app_id: str) -> list[dict]:
        return self.query("SELECT * FROM status_history WHERE application_id=? ORDER BY changed_at_utc,id", (app_id,))

    def stages(self, app_id: str) -> list[dict]:
        return self.query("""SELECT x.*,s.name AS status_name,s.category FROM application_stages x
                             JOIN status_definitions s ON s.id=x.status_id
                             WHERE x.application_id=? ORDER BY x.entered_at_utc,x.id""", (app_id,))

    def nodes(self, stage_id: str) -> list[dict]:
        return self.query("SELECT * FROM stage_time_nodes WHERE stage_id=? ORDER BY label", (stage_id,))

    def save_node(self, stage_id: str, label: str, kind: str, precision="未知", value=None, notes="") -> str:
        label = required(label, "节点名称")
        if kind not in ("通知", "截止", "安排", "完成") or precision not in ("未知", "日期", "时间"):
            raise ValueError("时间节点类型或精度无效")
        date_on = check_date(value, "节点日期") if precision == "日期" else None
        at_utc = local_to_utc(value, "节点时间") if precision == "时间" else None
        if precision != "未知" and not (date_on or at_utc):
            raise ValueError("请选择节点日期或时间")
        with transaction(self.path) as con:
            stage = _one(con, "SELECT * FROM application_stages WHERE id=?", (stage_id,))
            if not stage:
                raise ValueError("申请阶段不存在")
            existing = _one(con, "SELECT * FROM stage_time_nodes WHERE stage_id=? AND label=?", (stage_id, label))
            if existing:
                row_id = existing["id"]
                _update(con, "stage_time_nodes", row_id, dict(kind=kind, precision=precision,
                        date_on=date_on, at_utc=at_utc, notes=notes))
            else:
                row_id = uid()
                _insert(con, "stage_time_nodes", dict(id=row_id, stage_id=stage_id, label=label,
                        kind=kind, precision=precision, date_on=date_on, at_utc=at_utc, notes=notes))
            con.execute("UPDATE applications SET updated_at_utc=? WHERE id=?", (now(), stage["application_id"]))
        return row_id

    def _resolve_relation(self, con, company_id=None, position_id=None, application_id=None, stage_id=None):
        if stage_id:
            stage = _one(con, "SELECT * FROM application_stages WHERE id=?", (stage_id,))
            if not stage:
                raise ValueError("申请阶段不存在")
            if application_id and application_id != stage["application_id"]:
                raise ValueError("申请阶段与申请不一致")
            application_id = stage["application_id"]
        if application_id:
            app = _one(con, "SELECT * FROM applications WHERE id=?", (application_id,))
            if not app:
                raise ValueError("申请不存在")
            if position_id and position_id != app["position_id"]:
                raise ValueError("申请与岗位不一致")
            position_id = app["position_id"]
        if position_id:
            pos = _one(con, "SELECT * FROM positions WHERE id=?", (position_id,))
            if not pos:
                raise ValueError("岗位不存在")
            if company_id and company_id != pos["company_id"]:
                raise ValueError("公司与岗位不一致")
            company_id = pos["company_id"]
        if not company_id or not _one(con, "SELECT id FROM companies WHERE id=?", (company_id,)):
            raise ValueError("至少选择有效公司、岗位或申请")
        return company_id, position_id, application_id, stage_id

    def save_event(self, data: dict, row_id: str | None = None, stage_nodes: list[dict] | None = None) -> str:
        values = {key: data.get(key) for key in EVENT_FIELDS}
        values["title"] = required(values["title"], "事件标题")
        values["type"] = required(values["type"], "事件类型")
        values["status"] = values["status"] or "已安排"
        values["precision"] = values["precision"] or "未知"
        values["event_on"] = check_date(values["event_on"], "事件日期") if values["precision"] == "日期" else None
        values["event_at_utc"] = local_to_utc(values["event_at_utc"], "事件时间") if values["precision"] == "时间" else None
        if values["precision"] != "未知" and not (values["event_on"] or values["event_at_utc"]):
            raise ValueError("请选择事件日期或时间")
        with transaction(self.path) as con:
            relation = self._resolve_relation(con, values["company_id"], values["position_id"], values["application_id"], values["stage_id"])
            values.update(zip(("company_id", "position_id", "application_id", "stage_id"), relation))
            if values["type"] in ("测评", "AI 面试", "视频面试"):
                stage = _one(con, "SELECT s.name FROM application_stages x JOIN status_definitions s ON s.id=x.status_id WHERE x.id=?", (values["stage_id"],))
                allowed = {"测评": ("测评准备", "测评/AI 面试准备"),
                           "AI 面试": ("AI 面试准备", "测评/AI 面试准备"),
                           "视频面试": ("视频面试准备",)}
                if not stage or stage["name"] not in allowed[values["type"]]:
                    raise ValueError("事件须关联相应申请的测评或面试阶段")
            if stage_nodes and not values["stage_id"]:
                raise ValueError("请先选择申请阶段，再保存阶段时间节点")
            for item in stage_nodes or []:
                label = required(item.get("label"), "节点名称")
                kind = item.get("kind")
                precision = item.get("precision")
                if kind not in ("通知", "截止", "安排", "完成") or precision not in ("日期", "时间"):
                    raise ValueError("时间节点类型或精度无效")
                node_date = check_date(item.get("value"), "节点日期") if precision == "日期" else None
                node_at = local_to_utc(item.get("value"), "节点时间") if precision == "时间" else None
                prior_node = _one(con, "SELECT id FROM stage_time_nodes WHERE stage_id=? AND label=?",
                                  (values["stage_id"], label))
                if prior_node:
                    node_id = prior_node["id"]
                    _update(con, "stage_time_nodes", node_id, dict(kind=kind, precision=precision,
                            date_on=node_date, at_utc=node_at))
                else:
                    node_id = uid()
                    _insert(con, "stage_time_nodes", dict(id=node_id, stage_id=values["stage_id"],
                            label=label, kind=kind, precision=precision, date_on=node_date, at_utc=node_at))
                if kind == "截止" and values["event_on"] == node_date and values["event_at_utc"] == node_at:
                    values["node_id"] = node_id
            if stage_nodes:
                con.execute("UPDATE applications SET updated_at_utc=? WHERE id=?", (now(), values["application_id"]))
            if values["node_id"]:
                node = _one(con, "SELECT stage_id FROM stage_time_nodes WHERE id=?", (values["node_id"],))
                if not node or node["stage_id"] != values["stage_id"]:
                    raise ValueError("事件节点与申请阶段不一致")
            stamp = now()
            if row_id:
                if not _one(con, "SELECT id FROM recruitment_events WHERE id=?", (row_id,)):
                    raise ValueError("事件不存在")
                values["updated_at_utc"] = stamp
                _update(con, "recruitment_events", row_id, values)
            else:
                row_id = uid()
                values.update(id=row_id, created_at_utc=stamp, updated_at_utc=stamp)
                _insert(con, "recruitment_events", values)
        return row_id

    def list_events(self, include_archived=False) -> list[dict]:
        sql = """SELECT e.*,c.name AS company_name,p.title AS position_title,
                 deadline.date_on AS deadline_on, deadline.at_utc AS deadline_at_utc
                 FROM recruitment_events e JOIN companies c ON c.id=e.company_id
                 LEFT JOIN positions p ON p.id=e.position_id
                 LEFT JOIN applications a ON a.id=e.application_id
                 LEFT JOIN stage_time_nodes deadline ON deadline.id=(
                     SELECT n.id FROM stage_time_nodes n
                     WHERE n.stage_id=e.stage_id AND n.kind='截止'
                       AND (n.date_on IS NOT NULL OR n.at_utc IS NOT NULL)
                     ORDER BY CASE WHEN n.label='测评截止时间' THEN 0 ELSE 1 END, n.rowid DESC
                     LIMIT 1
                 ) WHERE 1=1"""
        if not include_archived:
            sql += " AND c.archived_at_utc IS NULL AND (p.id IS NULL OR p.archived_at_utc IS NULL) AND (a.id IS NULL OR a.archived_at_utc IS NULL)"
        return self.query(sql + " ORDER BY e.updated_at_utc DESC")

    def delete_event(self, event_id: str) -> None:
        """Delete only the detail record; stage time nodes remain independent."""
        with transaction(self.path) as con:
            if not _one(con, "SELECT id FROM recruitment_events WHERE id=?", (event_id,)):
                raise ValueError("招聘事件不存在")
            con.execute("DELETE FROM recruitment_events WHERE id=?", (event_id,))

    def save_todo(self, data: dict, row_id: str | None = None) -> str:
        values = {key: data.get(key) for key in TODO_FIELDS}
        values["title"] = required(values["title"], "待办标题")
        values["precision"] = values["precision"] or "未知"
        values["priority"] = values["priority"] or "中"
        values["due_on"] = check_date(values["due_on"], "待办截止日期") if values["precision"] == "日期" else None
        values["due_at_utc"] = local_to_utc(values["due_at_utc"], "待办截止时间") if values["precision"] == "时间" else None
        if values["precision"] != "未知" and not (values["due_on"] or values["due_at_utc"]):
            raise ValueError("请选择待办截止日期或时间")
        with transaction(self.path) as con:
            relation = self._resolve_relation(con, values["company_id"], values["position_id"], values["application_id"], values["stage_id"])
            values.update(zip(("company_id", "position_id", "application_id", "stage_id"), relation))
            stamp = now()
            if row_id:
                if not _one(con, "SELECT id FROM todos WHERE id=?", (row_id,)):
                    raise ValueError("待办不存在")
                values["updated_at_utc"] = stamp
                _update(con, "todos", row_id, values)
            else:
                row_id = uid()
                values.update(id=row_id, created_at_utc=stamp, updated_at_utc=stamp)
                _insert(con, "todos", values)
        return row_id

    def complete_todo(self, todo_id: str, completed=True) -> None:
        with transaction(self.path) as con:
            if not _one(con, "SELECT id FROM todos WHERE id=?", (todo_id,)):
                raise ValueError("待办不存在")
            _update(con, "todos", todo_id, dict(completed_at_utc=now() if completed else None, updated_at_utc=now()))

    def list_todos(self, include_archived=False) -> list[dict]:
        sql = """SELECT t.*,c.name AS company_name,p.title AS position_title
                 FROM todos t JOIN companies c ON c.id=t.company_id
                 LEFT JOIN positions p ON p.id=t.position_id
                 LEFT JOIN applications a ON a.id=t.application_id WHERE 1=1"""
        if not include_archived:
            sql += " AND c.archived_at_utc IS NULL AND (p.id IS NULL OR p.archived_at_utc IS NULL) AND (a.id IS NULL OR a.archived_at_utc IS NULL)"
        return self.query(sql + " ORDER BY t.updated_at_utc DESC")

    def save_profile(self, data: dict, row_id: str | None = None) -> str:
        values = {key: data.get(key) for key in PROFILE_FIELDS}
        for key, label in (("category", "分类"), ("title", "标题"), ("body", "正文")):
            values[key] = required(values[key], label)
        for key in ("start_on", "end_on"):
            values[key] = check_date(values[key], key)
        with transaction(self.path) as con:
            stamp = now()
            if row_id:
                if not _one(con, "SELECT id FROM profiles WHERE id=?", (row_id,)):
                    raise ValueError("资料不存在")
                values["updated_at_utc"] = stamp
                _update(con, "profiles", row_id, values)
            else:
                row_id = uid()
                values.update(id=row_id, created_at_utc=stamp, updated_at_utc=stamp)
                _insert(con, "profiles", values)
        return row_id

    def list_profiles(self, include_archived=False, search="") -> list[dict]:
        sql = "SELECT * FROM profiles WHERE (title LIKE ? OR body LIKE ? OR COALESCE(tags,'') LIKE ?)"
        if not include_archived:
            sql += " AND archived_at_utc IS NULL"
        term = f"%{search.strip()}%"
        return self.query(sql + " ORDER BY updated_at_utc DESC", (term, term, term))

    def archive(self, table: str, row_id: str, archived=True) -> None:
        if table not in ("companies", "positions", "applications", "profiles"):
            raise ValueError("此记录不能归档")
        with transaction(self.path) as con:
            if not _one(con, f"SELECT id FROM {table} WHERE id=?", (row_id,)):
                raise ValueError("记录不存在")
            con.execute(f"UPDATE {table} SET archived_at_utc=?,updated_at_utc=? WHERE id=?",
                        (now() if archived else None, now(), row_id))

    def link_file(self, file_id: str, kind: str, target_id: str, link=True) -> None:
        if kind not in LINK_TABLES:
            raise ValueError("文件关联类型无效")
        table, col = LINK_TABLES[kind]
        target_table = {"company": "companies", "position": "positions", "application": "applications", "profile": "profiles", "stage": "application_stages"}[kind]
        with transaction(self.path) as con:
            if not _one(con, "SELECT id FROM files WHERE id=?", (file_id,)) or not _one(con, f"SELECT id FROM {target_table} WHERE id=?", (target_id,)):
                raise ValueError("文件或关联记录不存在")
            if link:
                con.execute(f"INSERT OR IGNORE INTO {table}(file_id,{col}) VALUES(?,?)", (file_id, target_id))
            else:
                con.execute(f"DELETE FROM {table} WHERE file_id=? AND {col}=?", (file_id, target_id))

    def link_profile(self, profile_id: str, application_id=None, stage_id=None, link=True) -> None:
        if bool(application_id) == bool(stage_id):
            raise ValueError("请选择申请或阶段中的一项")
        table, col, target = ("profile_applications", "application_id", application_id) if application_id else ("stage_profiles", "stage_id", stage_id)
        target_table = "applications" if application_id else "application_stages"
        with transaction(self.path) as con:
            if not _one(con, "SELECT id FROM profiles WHERE id=?", (profile_id,)) or not _one(con, f"SELECT id FROM {target_table} WHERE id=?", (target,)):
                raise ValueError("资料或关联记录不存在")
            if link:
                con.execute(f"INSERT OR IGNORE INTO {table}(profile_id,{col}) VALUES(?,?)", (profile_id, target))
            else:
                con.execute(f"DELETE FROM {table} WHERE profile_id=? AND {col}=?", (profile_id, target))
