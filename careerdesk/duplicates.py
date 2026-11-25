"""Review existing same-name positions without silently merging user data."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from .core import POSITION_FIELDS, Store, name_key, now
from .db import transaction

REVIEW_PREFIX = "position_duplicate_review:"
LINK_TABLES = ("applications", "recruitment_events", "todos", "file_positions")
COMPARE_FIELDS = tuple(field for field in POSITION_FIELDS if field != "company_id")
IDENTITY_FIELDS = ("title", "recruitment_project", "recruitment_batch", "department", "city")


def _identity(position: dict) -> tuple[str, ...]:
    return (position["company_id"], *(name_key(position.get(field) or "") for field in IDENTITY_FIELDS))


def _signature(positions: list[dict]) -> str:
    payload = [(row["id"], row["company_id"], [str(row.get(field) or "").strip() for field in COMPARE_FIELDS])
               for row in sorted(positions, key=lambda item: item["id"])]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return REVIEW_PREFIX + hashlib.sha256(encoded).hexdigest()


def _link_counts(store: Store) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for table in LINK_TABLES:
        for row in store.query(f"SELECT position_id,COUNT(*) AS count FROM {table} "
                               "WHERE position_id IS NOT NULL GROUP BY position_id"):
            result[row["position_id"]][table] = row["count"]
    return result


def position_duplicate_groups(store: Store) -> list[dict]:
    """Group active records sharing the same company and core opening identity."""
    by_name: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for position in store.list_positions():
        by_name[_identity(position)].append(position)
    reviewed = {row["key"] for row in store.query(
        "SELECT key FROM app_meta WHERE key LIKE ?", (f"{REVIEW_PREFIX}%",))}
    links = _link_counts(store)
    groups = []
    for positions in by_name.values():
        if len(positions) < 2:
            continue
        positions.sort(key=lambda row: (row["created_at_utc"], row["id"]))
        signatures = {tuple(str(row.get(field) or "").strip() for field in COMPARE_FIELDS)
                      for row in positions}
        key = _signature(positions)
        groups.append({
            "company_id": positions[0]["company_id"],
            "company_name": positions[0]["company_name"],
            "title": positions[0]["title"],
            "positions": positions,
            "identical": len(signatures) == 1,
            "review_key": key,
            "reviewed": key in reviewed,
            "links": {row["id"]: links.get(row["id"], {}) for row in positions},
        })
    return groups


def set_group_reviewed(store: Store, positions: list[dict], reviewed: bool) -> None:
    ids = {row["id"] for row in positions}
    current = [row for row in store.list_positions() if row["id"] in ids]
    if (len(current) != len(ids)
            or len({_identity(row) for row in current}) != 1
            or _signature(current) != _signature(positions)):
        raise ValueError("岗位资料已变化，请重新检查这组记录")
    key = _signature(current)
    with transaction(store.path) as con:
        if reviewed:
            con.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES(?,?)", (key, now()))
        else:
            con.execute("DELETE FROM app_meta WHERE key=?", (key,))


def archive_unlinked_position(store: Store, position_id: str) -> None:
    """Archive a candidate only when no user record points to it."""
    with transaction(store.path) as con:
        position = con.execute("SELECT id,archived_at_utc FROM positions WHERE id=?", (position_id,)).fetchone()
        if not position or position["archived_at_utc"]:
            raise ValueError("岗位不存在或已归档，请刷新检查结果")
        for table in LINK_TABLES:
            if con.execute(f"SELECT 1 FROM {table} WHERE position_id=? LIMIT 1", (position_id,)).fetchone():
                raise ValueError("这条岗位关联了申请、招聘事件、待办或文件；请先核对关联记录，不能直接归档")
        stamp = now()
        con.execute("UPDATE positions SET archived_at_utc=?,updated_at_utc=? WHERE id=?", (stamp, stamp, position_id))
