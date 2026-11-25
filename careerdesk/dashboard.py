"""Live overview numbers and deadline labels."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .core import Store
from .timeutil import SHANGHAI



def local_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(SHANGHAI)


def deadline_at(date_on: str | None, at_utc: str | None) -> datetime | None:
    if at_utc:
        return local_time(at_utc)
    if date_on:
        return datetime.combine(date.fromisoformat(date_on) + timedelta(days=1), time.min, SHANGHAI)
    return None


def urgency(date_on=None, at_utc=None, *, completed=False, current=None) -> dict:
    if completed:
        return {"label": "已完成/取消", "color": "gray", "rank": 5}
    target = deadline_at(date_on, at_utc)
    if not target:
        return {"label": "时间未知", "color": "gray", "rank": 5}
    current = current or datetime.now(SHANGHAI)
    seconds = (target - current).total_seconds()
    if seconds <= 0:
        return {"label": "已逾期", "color": "red", "rank": 0}
    if seconds <= 24 * 3600:
        return {"label": "24 小时内", "color": "orange", "rank": 1}
    if seconds <= 7 * 24 * 3600:
        return {"label": "未来 2–7 天", "color": "gold", "rank": 2}
    return {"label": "更晚", "color": "slategray", "rank": 3}


def overview(store: Store, current: datetime | None = None) -> dict:
    current = current or datetime.now(SHANGHAI)
    active_apps = """FROM applications a JOIN positions p ON p.id=a.position_id
                     JOIN companies c ON c.id=p.company_id
                     JOIN status_definitions s ON s.id=a.current_status_id
                     WHERE a.archived_at_utc IS NULL AND p.archived_at_utc IS NULL AND c.archived_at_utc IS NULL"""
    submitted = store.one(f"SELECT COUNT(*) AS n {active_apps} AND (a.submitted_on IS NOT NULL OR a.submitted_at_utc IS NOT NULL)")["n"]
    progressing = store.one(f"SELECT COUNT(*) AS n {active_apps} AND s.category='推进中'")["n"]
    todos = store.list_todos()
    open_todos = [row for row in todos if not row["completed_at_utc"]]
    overdue_todos = [row for row in open_todos if urgency(row["due_on"], row["due_at_utc"], current=current)["label"] == "已逾期"]
    items = []
    positions = store.list_positions()
    submitted_positions = store.query("""SELECT DISTINCT a.position_id,p.company_id
                                        FROM applications a JOIN positions p ON p.id=a.position_id
                                        WHERE a.submission_precision!='无'""")
    applied_position_ids = {row["position_id"] for row in submitted_positions}
    applied_company_ids = {row["company_id"] for row in submitted_positions}
    for row in positions:
        if row["deadline_on"]:
            application_rank = (2 if row["id"] in applied_position_ids else
                                1 if row["company_id"] in applied_company_ids else 0)
            items.append(dict(kind="岗位截止", title=f"{row['company_name']} · {row['title']}",
                              date_on=row["deadline_on"], at_utc=None, id=row["id"],
                              company_id=row["company_id"], company_name=row["company_name"],
                              position_title=row["title"], application_rank=application_rank,
                              source="position"))
    current_stage_ids = {row["current_stage_id"] for row in
                         store.query(f"SELECT a.current_stage_id {active_apps}")}
    nodes = store.query("""SELECT n.*,a.id AS application_id,p.title AS position_title,c.name AS company_name,
                          s.name AS status_name,e.id AS linked_event_id,e.title AS linked_event_title
                          FROM stage_time_nodes n JOIN application_stages x ON x.id=n.stage_id
                          JOIN applications a ON a.id=x.application_id
                          JOIN positions p ON p.id=a.position_id JOIN companies c ON c.id=p.company_id
                          JOIN status_definitions s ON s.id=x.status_id
                          LEFT JOIN recruitment_events e ON e.node_id=n.id AND e.status='已安排'
                          WHERE a.current_stage_id=x.id
                          AND a.archived_at_utc IS NULL AND p.archived_at_utc IS NULL
                          AND c.archived_at_utc IS NULL""")
    events = [row for row in store.list_events()
              if row["status"] == "已安排" and
              (not row["stage_id"] or row["stage_id"] in current_stage_ids)]

    def local_day(date_on: str | None, at_utc: str | None) -> str | None:
        moment = local_time(at_utc)
        return date_on or (moment.date().isoformat() if moment else None)

    event_by_stage_day = defaultdict(list)
    for event in events:
        day = local_day(event["event_on"], event["event_at_utc"])
        if event["stage_id"] and day:
            event_by_stage_day[(event["stage_id"], day)].append(event)
    represented_events = {row["linked_event_id"] for row in nodes if row["linked_event_id"]}
    for row in nodes:
        if row["precision"] == "未知":
            continue
        linked_event_id = row["linked_event_id"]
        linked_event_title = row["linked_event_title"]
        if linked_event_id:
            represented_events.add(linked_event_id)
        elif row["kind"] in ("截止", "安排"):
            # An event on the same stage and day is the detail behind this
            # reminder. Pair one detail with one node; retain distinct events.
            day = local_day(row["date_on"], row["at_utc"])
            candidates = event_by_stage_day.get((row["stage_id"], day), ())
            matched = next((event for event in candidates if event["id"] not in represented_events), None)
            if matched:
                linked_event_id = matched["id"]
                linked_event_title = matched["title"]
                represented_events.add(linked_event_id)
        items.append(dict(kind=f"申请节点 · {row['kind']}",
                          title=f"{row['company_name']} · {row['position_title']} · {row['label']}" +
                                (f"（{linked_event_title}）" if linked_event_title else ""),
                          date_on=row["date_on"], at_utc=row["at_utc"], id=row["application_id"], source="application",
                          stage_id=row["stage_id"], linked_event_id=linked_event_id,
                          completed=row["kind"] in ("通知", "完成")))
    for row in events:
        if row["id"] in represented_events:
            continue
        items.append(dict(kind="招聘事件", title=row["title"], date_on=row["event_on"],
                          at_utc=row["event_at_utc"], id=row["id"], source="event",
                          application_id=row["application_id"], stage_id=row["stage_id"]))
    start = current.date()
    end = start + timedelta(days=7)
    upcoming = []
    overdue = []
    for item in items:
        target = deadline_at(item["date_on"], item["at_utc"])
        if target is None:
            continue
        if item.get("completed"):
            continue
        item = {**item, "urgency": urgency(item["date_on"], item["at_utc"], current=current),
                "display_time": f"{item['date_on']}（当天结束）" if item["date_on"] else target.strftime("%Y-%m-%d %H:%M")}
        if target <= current:
            overdue.append(item)
        elif start <= (date.fromisoformat(item["date_on"]) if item["date_on"] else target.date()) <= end:
            upcoming.append(item)
    upcoming.sort(key=lambda item: item["display_time"])
    overdue.sort(key=lambda item: item["display_time"])
    recent = store.query("""SELECT kind,title,updated_at_utc FROM (
                         SELECT '公司' kind,name title,updated_at_utc FROM companies WHERE archived_at_utc IS NULL
                         UNION ALL SELECT '岗位',p.title,p.updated_at_utc FROM positions p
                             JOIN companies c ON c.id=p.company_id WHERE p.archived_at_utc IS NULL AND c.archived_at_utc IS NULL
                         UNION ALL SELECT '个人资料',title,updated_at_utc FROM profiles WHERE archived_at_utc IS NULL
                         UNION ALL SELECT '事件',e.title,e.updated_at_utc FROM recruitment_events e
                             JOIN companies c ON c.id=e.company_id LEFT JOIN positions p ON p.id=e.position_id
                             LEFT JOIN applications a ON a.id=e.application_id
                             WHERE c.archived_at_utc IS NULL AND (p.id IS NULL OR p.archived_at_utc IS NULL)
                             AND (a.id IS NULL OR a.archived_at_utc IS NULL)
                         ) ORDER BY updated_at_utc DESC LIMIT 10""")
    return {"submitted": submitted, "progressing": progressing, "open_todos": len(open_todos),
            "overdue_todos": len(overdue_todos), "upcoming": upcoming, "overdue": overdue, "recent": recent}
