"""Conservative offline suggestions from text explicitly pasted by the user."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .core import local_to_utc
from .timeutil import SHANGHAI

URL = re.compile(r"https?://[^\s<>'\"，。；;）)]+", re.I)
LABEL = re.compile(r"^\s*([^：:\n]{2,16})\s*[：:]\s*(.+?)\s*$")
DATE = re.compile(r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?(?:\s*(\d{1,2})[:：时](\d{1,2}))?")
DAY_SPAN = re.compile(r"(?:通知(?:后|起)?|收到(?:通知)?(?:后|起)?|自通知起)?\s*(\d{1,2})\s*天内")
CATEGORIES = ("教育", "科研", "项目", "实习", "技能", "获奖与论文", "志愿及其他", "网申问答", "自我介绍", "面试复盘")


def _label_values(text: str) -> dict[str, tuple[str, str]]:
    values = {}
    for line in text.splitlines():
        match = LABEL.match(line)
        if match:
            values[match.group(1).strip()] = (match.group(2).strip(), line.strip())
    return values


def _pick(labels, names):
    for name in names:
        if name in labels:
            return {"value": labels[name][0], "source": labels[name][1]}
    return None


def company_suggestions(text: str) -> dict:
    labels = _label_values(text)
    result = {}
    fields = {
        "name": ("公司名称", "企业名称", "公司"),
        "english_name": ("公司英文名", "英文名", "英文名称", "英文公司名"),
        "industry": ("所属行业", "行业"),
        "cities": ("主要招聘城市", "招聘城市", "城市"),
        "website": ("公司官网", "官网"),
        "recruitment_url": ("招聘官网", "招聘网址", "招聘链接"),
    }
    for field, names in fields.items():
        found = _pick(labels, names)
        if found:
            result[field] = found
    urls = URL.findall(text)
    if urls and "recruitment_url" not in result and "website" not in result:
        result["website"] = {"value": urls[0], "source": urls[0]}
    return result


def position_suggestions(text: str) -> dict:
    labels = _label_values(text)
    fields = {
        "title": ("岗位名称", "职位名称", "岗位", "职位"),
        "recruitment_project": ("招聘项目名称", "招聘项目", "项目名称"),
        "department": ("部门", "招聘部门"),
        "city": ("工作城市", "工作地点", "城市"),
        "recruitment_batch": ("招聘批次", "批次"),
        "major_requirements": ("专业要求", "所需专业", "专业限制"),
        "graduation_time_requirement": ("毕业时间要求", "毕业时间", "毕业年份要求", "毕业届别要求"),
        "deadline_on": ("截止日期", "投递截止日期", "申请截止日期"),
        "apply_url": ("投递链接", "申请链接", "网申链接"),
    }
    result = {}
    for field, names in fields.items():
        found = _pick(labels, names)
        if found:
            result[field] = found
    if "deadline_on" in result:
        match = DATE.search(result["deadline_on"]["value"])
        if match:
            result["deadline_on"]["value"] = f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"
        else:
            result.pop("deadline_on")
    urls = URL.findall(text)
    if urls and "apply_url" not in result:
        result["apply_url"] = {"value": urls[0], "source": urls[0]}
    return result


def notice_suggestions(text: str, notification_at: str | None = None) -> dict:
    labels = _label_values(text)
    result = {}
    fields = {
        "requirements": ("测评要求", "作答要求", "要求", "测评内容", "内容"),
        "location_or_link": ("测评链接", "作答链接", "链接"),
        "notes": ("备注", "注意事项"),
        "notice_time": ("通知时间", "收到时间"),
        "deadline": ("测评截止时间", "截止时间", "作答截止时间"),
    }
    for field, names in fields.items():
        found = _pick(labels, names)
        if found:
            result[field] = found
    urls = URL.findall(text)
    if urls and "location_or_link" not in result:
        result["location_or_link"] = {"value": urls[0], "source": urls[0]}
    for field in ("notice_time", "deadline"):
        if field in result:
            match = DATE.search(result[field]["value"])
            if match:
                year, month, day = map(int, match.group(1, 2, 3))
                hour, minute = match.group(4, 5)
                result[field]["value"] = f"{year:04d}-{month:02d}-{day:02d}" + (f" {int(hour):02d}:{int(minute):02d}" if hour else "")
            else:
                result.pop(field)
    if "deadline" not in result and "工作日" not in text and "自然日" not in text:
        span = DAY_SPAN.search(text)
        source_time = notification_at or (result.get("notice_time") or {}).get("value")
        if span and source_time:
            try:
                start = datetime.fromisoformat(source_time).replace(tzinfo=SHANGHAI)
                if "T" in source_time or " " in source_time:
                    end = start + timedelta(days=int(span[1]))
                    result["deadline"] = {"value": end.strftime("%Y-%m-%d %H:%M"),
                                          "source": f"通知时间 {source_time} + {span[1]}×24 小时（推算，需确认）",
                                          "inferred": True}
            except ValueError:
                pass
    return result


def profile_suggestions(text: str) -> list[dict]:
    """Only explicit section headings split one pasted block into several drafts."""
    sections = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:#{1,3}\s*)?(教育|科研|项目|实习|技能|获奖与论文|志愿及其他|网申问答|自我介绍|面试复盘)\s*[：:]\s*(.*)$", stripped)
        if match:
            if current:
                sections.append(current)
            current = {"category": match[1], "title": match[2].strip() or match[1], "body": ""}
        elif current:
            current["body"] += line + "\n"
    if current:
        sections.append(current)
    if not sections:
        return [{"category": "志愿及其他", "title": "待整理的经历", "body": text.strip()}] if text.strip() else []
    for section in sections:
        section["body"] = section["body"].strip()
    return sections
