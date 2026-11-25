"""Reviewable two-sheet Excel import for companies and positions."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from .core import Store, _insert, check_date, name_key, now, required, uid
from .db import transaction

COMPANY_COLUMNS = {
    "公司名称": "name", "公司英文名": "english_name", "行业": "industry", "官网": "website", "招聘官网": "recruitment_url",
    "主要招聘城市": "cities", "研究笔记": "research_notes", "个人评价": "evaluation", "关注优先级": "priority",
}
POSITION_COLUMNS = {
    "公司名称": "company_name", "招聘项目名称": "recruitment_project", "岗位名称": "title", "部门": "department", "城市": "city",
    "招聘批次": "recruitment_batch", "专业要求": "major_requirements", "毕业时间要求": "graduation_time_requirement",
    "岗位描述JD": "jd", "投递链接": "apply_url",
    "截止日期": "deadline_on", "兴趣程度": "interest", "申请优先级": "priority",
    "人工匹配度": "match_rating", "个人优势": "strengths", "能力短板": "gaps",
    "待核实事项": "questions", "备注": "notes",
}


@lru_cache(maxsize=1)
def template_bytes() -> bytes:
    wb = Workbook()
    instructions = wb.active
    instructions.title = "填写说明"
    for line in (
        "CareerDesk 公司与岗位导入模板：只填写“公司”和“岗位”工作表。",
        "公司表每家公司一行，仅公司名称必填；岗位表每岗位一行，公司名称和岗位名称必填。",
        "岗位可引用工作台已有的公司。日期请填写 YYYY-MM-DD；其余字段可以留空。",
        "不同岗位可以共用投递链接；仅凭链接相同不会判定岗位重复。",
        "导入前会预览并标红重复记录；只有确认后才会写入。此说明页不参与导入。",
    ):
        instructions.append([line])
    for title, columns, required_names in (
        ("公司", COMPANY_COLUMNS, {"公司名称"}),
        ("岗位", POSITION_COLUMNS, {"公司名称", "岗位名称"}),
    ):
        sheet = wb.create_sheet(title)
        sheet.append(list(columns))
        sheet.freeze_panes = "A2"
        for index, label in enumerate(columns, 1):
            cell = sheet.cell(1, index)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="A33A32" if label in required_names else "275D79")
            sheet.column_dimensions[cell.column_letter].width = 22 if label not in ("岗位描述JD", "研究笔记") else 46
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def existing_template_bytes(store: Store) -> bytes:
    """Export every saved company and position in the import template layout."""
    wb = load_workbook(io.BytesIO(template_bytes()))
    companies = store.list_companies(include_archived=True)
    positions = store.list_positions(include_archived=True)
    companies.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    positions.sort(key=lambda row: (row["company_name"].casefold(), row["title"].casefold(), row["id"]))
    wb["填写说明"].append(["此文件包含工作台中所有公司和岗位，包括已归档记录；灰色行表示已归档。"])
    wb["填写说明"].append(["用于对照和筛查重复。重新导入前请核对灰色行及需要保留的资料。"])
    archived_fill = PatternFill("solid", fgColor="E7E5DF")
    archived_font = Font(color="666666")

    for sheet_name, columns, records in (
        ("公司", COMPANY_COLUMNS, companies),
        ("岗位", POSITION_COLUMNS, positions),
    ):
        sheet = wb[sheet_name]
        for record in records:
            row_number = sheet.max_row + 1
            for column_number, field in enumerate(columns.values(), 1):
                value = record.get(field)
                cell = sheet.cell(row_number, column_number)
                if sheet_name == "岗位" and field == "deadline_on" and value:
                    cell.value = date.fromisoformat(value)
                    cell.number_format = "yyyy-mm-dd"
                elif value is not None:
                    cell.value = str(value)
                    # Imported text such as =SUM(...) must remain text in Excel.
                    cell.data_type = "s"
                if record["archived_at_utc"] or (sheet_name == "岗位" and record["company_archived"]):
                    cell.fill = archived_fill
                    cell.font = archived_font
        sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return value.casefold().rstrip("/")
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.casefold(), parts.path.rstrip("/"), urlencode(query), ""))


def _cell(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value).strip()


def _read_sheet(wb, title, columns):
    if title not in wb.sheetnames:
        raise ValueError(f"缺少“{title}”工作表")
    rows = wb[title].iter_rows(values_only=True)
    try:
        headers = [_cell(v) for v in next(rows)]
    except StopIteration:
        raise ValueError(f"“{title}”工作表为空")
    for mandatory in (("公司名称",) if title == "公司" else ("公司名称", "岗位名称")):
        if mandatory not in headers:
            raise ValueError(f"“{title}”缺少必填列：{mandatory}")
    result = []
    for row_number, cells in enumerate(rows, 2):
        data = {column: _cell(cells[headers.index(label)]) if headers.index(label) < len(cells) else ""
                for label, column in columns.items() if label in headers}
        if any(data.values()):
            result.append({"row_number": row_number, "data": data})
    return result


def _fingerprint(kind: str, data: dict) -> str:
    payload = json.dumps([kind, data], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stored_data(row, columns: dict[str, str]) -> dict:
    available = set(row.keys())
    return {field: row[field] if field in available else "" for field in columns.values()}


def _add_match(matches: list, source: str, data: dict, *, first: bool = False) -> None:
    if any(match["source"] == source for match in matches):
        return
    match = {"source": source, "data": dict(data)}
    if first:
        matches.insert(0, match)
    else:
        matches.append(match)


def preview_import(store: Store, workbook_bytes: bytes) -> dict:
    try:
        wb = load_workbook(io.BytesIO(workbook_bytes), read_only=True, data_only=True)
        companies = _read_sheet(wb, "公司", COMPANY_COLUMNS)
        positions = _read_sheet(wb, "岗位", POSITION_COLUMNS)
        wb.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"无法读取 Excel 模板：{exc}") from exc
    all_companies = store.query("SELECT * FROM companies")
    all_positions = store.query("SELECT * FROM positions")
    imported = {row["row_fingerprint"]: row["target_id"] for row in
                store.query("SELECT row_fingerprint,target_id FROM import_rows WHERE decision='新增'")}
    by_name = {row["name_key"]: row for row in all_companies}
    companies_by_id = {row["id"]: row for row in all_companies}
    positions_by_id = {row["id"]: row for row in all_positions}
    by_english = {name_key(row["english_name"]): row for row in all_companies if row["english_name"]}
    seen_names = {}
    seen_english = {}
    seen_urls = {}
    company_preview = []
    company_preview_by_row = {}
    for row in companies:
        data = row["data"]
        issues = []
        matches = []
        try:
            data["name"] = required(data.get("name"), "公司名称")
        except ValueError as exc:
            issues.append(str(exc))
        key = name_key(data.get("name", ""))
        english_key = name_key(data.get("english_name", ""))
        recruit_key = normalize_url(data.get("recruitment_url"))
        same_name = by_name.get(key)
        same_english = by_english.get(english_key) if english_key else None
        same_url = next((item for item in all_companies if recruit_key and normalize_url(item["recruitment_url"]) == recruit_key), None)
        if same_name:
            issues.append(f"公司名称与已有“{same_name['name']}”重复；可沿用已有公司")
            _add_match(matches, f"工作台已有公司“{same_name['name']}”", _stored_data(same_name, COMPANY_COLUMNS))
        if same_english:
            issues.append(f"公司英文名与已有“{same_english['name']}”一致：{data['english_name']}")
            _add_match(matches, f"工作台已有公司“{same_english['name']}”", _stored_data(same_english, COMPANY_COLUMNS))
        if same_url:
            issues.append(f"招聘官网与已有“{same_url['name']}”一致：{recruit_key}")
            _add_match(matches, f"工作台已有公司“{same_url['name']}”", _stored_data(same_url, COMPANY_COLUMNS))
        same_batch_name = key in seen_names
        same_batch_english = bool(english_key and english_key in seen_english)
        same_batch_url = bool(recruit_key and recruit_key in seen_urls)
        if same_batch_name:
            issues.append(f"与本次公司表第 {seen_names[key]} 行名称重复")
            previous = seen_names[key]
            _add_match(matches, f"本次公司表第 {previous} 行", company_preview_by_row[previous]["data"], first=True)
        elif key:
            seen_names[key] = row["row_number"]
        if same_batch_english:
            issues.append(f"与本次公司表第 {seen_english[english_key]} 行英文名一致")
            previous = seen_english[english_key]
            _add_match(matches, f"本次公司表第 {previous} 行", company_preview_by_row[previous]["data"])
        elif english_key:
            seen_english[english_key] = row["row_number"]
        if recruit_key:
            if same_batch_url:
                issues.append(f"与本次公司表第 {seen_urls[recruit_key]} 行招聘官网一致")
                previous = seen_urls[recruit_key]
                _add_match(matches, f"本次公司表第 {previous} 行", company_preview_by_row[previous]["data"])
            else:
                seen_urls[recruit_key] = row["row_number"]
        fingerprint = _fingerprint("company", data)
        if fingerprint in imported:
            issues.append("此公司行曾导入过")
            previous = companies_by_id.get(imported[fingerprint])
            if previous:
                _add_match(matches, f"工作台已有公司“{previous['name']}”", _stored_data(previous, COMPANY_COLUMNS))
        action = "沿用" if same_name else ("跳过" if same_english or same_url or same_batch_name
                                            or same_batch_english or same_batch_url or fingerprint in imported else "新增")
        if any("不能为空" in issue for issue in issues):
            action = "错误"
        entry = {**row, "issues": issues, "red": bool(issues), "action": action,
                 "candidate_id": (same_name or same_url or same_english or {}).get("id"),
                 "fingerprint": fingerprint, "matches": matches}
        company_preview.append(entry)
        company_preview_by_row[row["row_number"]] = entry
    seen_position_keys = {}
    position_preview = []
    position_preview_by_row = {}
    for row in positions:
        data = row["data"]
        issues = []
        matches = []
        try:
            data["company_name"] = required(data.get("company_name"), "公司名称")
            data["title"] = required(data.get("title"), "岗位名称")
            if data.get("deadline_on"):
                data["deadline_on"] = check_date(data["deadline_on"], "截止日期")
        except ValueError as exc:
            issues.append(str(exc))
        company_key = name_key(data.get("company_name", ""))
        company = by_name.get(company_key)
        if not company and company_key not in seen_names:
            issues.append("公司表和工作台中均找不到所属公司")
        exact = None
        for old in all_positions:
            if company and old["company_id"] == company["id"]:
                if (name_key(old["title"]) == name_key(data.get("title", ""))
                    and name_key(old["recruitment_project"] or "") == name_key(data.get("recruitment_project", ""))
                    and name_key(old["recruitment_batch"] or "") == name_key(data.get("recruitment_batch", ""))
                    and name_key(old["department"] or "") == name_key(data.get("department", ""))):
                    exact = old
                    issues.append(f"公司、岗位名、招聘项目、批次和部门与已有岗位“{old['title']}”一致")
                    _add_match(matches, f"工作台已有岗位“{old['title']}”",
                               {**_stored_data(old, POSITION_COLUMNS), "company_name": company["name"]})
                    break
                if name_key(old["title"]) == name_key(data.get("title", "")):
                    issues.append(f"与已有岗位“{old['title']}”同名；请核对批次等信息")
                    _add_match(matches, f"工作台已有岗位“{old['title']}”",
                               {**_stored_data(old, POSITION_COLUMNS), "company_name": company["name"]})
        row_key = (company_key, name_key(data.get("title", "")),
                   name_key(data.get("recruitment_project", "")), name_key(data.get("recruitment_batch", "")),
                   name_key(data.get("department", "")))
        same_batch_position = row_key in seen_position_keys
        if same_batch_position:
            previous = seen_position_keys[row_key]
            issues.append(f"与本次岗位表第 {previous} 行同公司同名同项目同批次同部门")
            _add_match(matches, f"本次岗位表第 {previous} 行", position_preview_by_row[previous]["data"], first=True)
        else:
            seen_position_keys[row_key] = row["row_number"]
        fingerprint = _fingerprint("position", data)
        if fingerprint in imported:
            issues.append("此岗位行曾导入过")
            previous = positions_by_id.get(imported[fingerprint])
            if previous:
                previous_company = companies_by_id[previous["company_id"]]
                _add_match(matches, f"工作台已有岗位“{previous['title']}”",
                           {**_stored_data(previous, POSITION_COLUMNS), "company_name": previous_company["name"]})
        action = "跳过" if exact or same_batch_position or fingerprint in imported else "新增"
        if any("找不到" in issue or "不能为空" in issue or "必须是" in issue for issue in issues):
            action = "错误"
        entry = {**row, "issues": issues, "red": bool(issues), "action": action,
                 "candidate_id": exact["id"] if exact else None, "fingerprint": fingerprint,
                 "matches": matches}
        position_preview.append(entry)
        position_preview_by_row[row["row_number"]] = entry
    return {"source_fingerprint": hashlib.sha256(workbook_bytes).hexdigest(),
            "companies": company_preview, "positions": position_preview}


def apply_import(store: Store, preview: dict, decisions: dict | None = None) -> dict:
    decisions = decisions or {}
    result = {"companies_added": 0, "companies_reused": 0, "positions_added": 0, "skipped": 0}
    with transaction(store.path) as con:
        if con.execute("SELECT 1 FROM import_batches WHERE source_fingerprint=?", (preview["source_fingerprint"],)).fetchone():
            raise ValueError("这份完全相同的模板已经导入过；没有重复写入")
        batch_id = uid()
        con.execute("INSERT INTO import_batches VALUES(?,?,?)", (batch_id, preview["source_fingerprint"], now()))
        company_map = {name_key(row["name"]): row["id"] for row in con.execute("SELECT id,name FROM companies")}
        for row in preview["companies"]:
            action = decisions.get(("company", row["row_number"]), row["action"])
            data = row["data"]
            target_id = None
            if action == "新增":
                key = name_key(data.get("name", ""))
                if not key or key in company_map:
                    raise ValueError(f"公司表第 {row['row_number']} 行名称空白或已存在")
                target_id = uid()
                stamp = now()
                _insert(con, "companies", dict(id=target_id, name=data["name"],
                        english_name=data.get("english_name"), name_key=key,
                        industry=data.get("industry"), website=data.get("website"),
                        recruitment_url=data.get("recruitment_url"), cities=data.get("cities"),
                        research_notes=data.get("research_notes"), evaluation=data.get("evaluation"),
                        priority=data.get("priority") or "未评估",
                        created_at_utc=stamp, updated_at_utc=stamp))
                company_map[key] = target_id
                result["companies_added"] += 1
            elif action == "沿用":
                target_id = row["candidate_id"] or company_map.get(name_key(data.get("name", "")))
                if not target_id:
                    raise ValueError(f"公司表第 {row['row_number']} 行没有可沿用的公司")
                company_map[name_key(data["name"])] = target_id
                result["companies_reused"] += 1
            elif action == "跳过":
                result["skipped"] += 1
            else:
                raise ValueError(f"公司表第 {row['row_number']} 行处理方式无效")
            con.execute("INSERT INTO import_rows VALUES(?,?,?,?,?,?)",
                        (batch_id, "company", row["row_number"], row["fingerprint"], action, target_id))
        for row in preview["positions"]:
            action = decisions.get(("position", row["row_number"]), row["action"])
            data = row["data"]
            target_id = None
            if action == "新增":
                company_id = company_map.get(name_key(data.get("company_name", "")))
                if not company_id:
                    raise ValueError(f"岗位表第 {row['row_number']} 行找不到所属公司")
                target_id = uid()
                stamp = now()
                _insert(con, "positions", dict(id=target_id, company_id=company_id,
                        title=required(data.get("title"), "岗位名称"),
                        recruitment_project=data.get("recruitment_project"),
                        department=data.get("department"), city=data.get("city"),
                        recruitment_batch=data.get("recruitment_batch"),
                        major_requirements=data.get("major_requirements"),
                        graduation_time_requirement=data.get("graduation_time_requirement"),
                        jd=data.get("jd"), apply_url=data.get("apply_url"),
                        deadline_on=check_date(data.get("deadline_on"), "截止日期"),
                        interest=data.get("interest") or "未评估",
                        priority=data.get("priority") or "未评估",
                        match_rating=data.get("match_rating") or "未评估",
                        strengths=data.get("strengths"), gaps=data.get("gaps"),
                        questions=data.get("questions"), notes=data.get("notes"),
                        created_at_utc=stamp, updated_at_utc=stamp))
                result["positions_added"] += 1
            elif action == "跳过":
                result["skipped"] += 1
            else:
                raise ValueError(f"岗位表第 {row['row_number']} 行处理方式无效")
            con.execute("INSERT INTO import_rows VALUES(?,?,?,?,?,?)",
                        (batch_id, "position", row["row_number"], row["fingerprint"], "新增" if action == "新增" else "跳过", target_id))
    return result
