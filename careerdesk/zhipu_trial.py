"""Small, read-only GLM trial for selected position records."""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4.7-flash"
FIELDS = ("company_name", "title", "recruitment_project", "recruitment_batch",
          "department", "city", "deadline_on", "apply_url", "jd")


def _http_error_message(exc: HTTPError) -> str:
    """Show a useful category and provider code, never the raw response or API key."""
    try:
        response = json.loads(exc.read(4096))
    except (OSError, UnicodeError, ValueError):
        response = {}
    error = response.get("error") if isinstance(response, dict) else None
    if not isinstance(error, dict):
        error = {}
    code = str(error.get("code", ""))
    code_note = f"（智谱错误码 {code}）" if re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", code) else ""
    detail = str(error.get("message", "")).lower()
    if exc.code == 429:
        if any(word in detail for word in ("balance", "credit", "quota", "余额", "额度", "欠费")):
            advice = "当前账号或模型的可用额度可能不足，请在智谱控制台查看该模型的可用资源包与余额。"
        else:
            advice = "请求受到频率或并发限制，请稍后再试；若持续出现，请在智谱控制台查看该模型的调用限制与可用额度。"
        return f"智谱接口返回 HTTP 429{code_note}：{advice}"
    if exc.code in (401, 403):
        return f"智谱接口返回 HTTP {exc.code}{code_note}：请核对 API Key 类型及该模型的使用权限。"
    return f"智谱接口返回 HTTP {exc.code}{code_note}：请核对模型权限或稍后再试。"


def position_payload(positions: list[dict]) -> list[dict]:
    if not 1 <= len(positions) <= 3:
        raise ValueError("请选择 1 至 3 条岗位")
    return [{field: str(row.get(field) or "")[:2000] for field in FIELDS}
            for row in positions]


def analyze_positions(api_key: str, positions: list[dict], question: str) -> dict:
    if not api_key.strip():
        raise ValueError("请先填写智谱模型标准 API Key")
    payload = position_payload(positions)
    question = question.strip()[:600]
    if not question:
        raise ValueError("请填写想分析的问题")
    body = {
        "model": MODEL,
        "stream": False,
        "thinking": {"type": "disabled"},
        "do_sample": False,
        "messages": [
            {"role": "system", "content": (
                "你是求职岗位整理助手。仅根据用户提供的岗位资料回答。"
                "先列出可核对的事实和字段，再给出整理建议；不确定时明确说明。"
                "相同投递链接不代表同一岗位；部门、城市、项目、批次不同应特别指出。"
                "不要声称已联网、已访问官网或已修改数据库。")},
            {"role": "user", "content": json.dumps({"问题": question, "岗位": payload}, ensure_ascii=False)},
        ],
        "max_tokens": 2400,
    }
    request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": f"Bearer {api_key.strip()}",
                               "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=40) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise ValueError(_http_error_message(exc)) from None
    except (URLError, TimeoutError) as exc:
        raise ValueError("无法连接智谱接口，请检查网络后重试") from exc
    try:
        choice = result["choices"][0]
        answer = choice["message"].get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("智谱返回了无法读取的结果") from exc
    if isinstance(answer, list):
        answer = "\n".join(part.get("text", "") for part in answer
                           if isinstance(part, dict) and part.get("type") == "text")
    if not isinstance(answer, str) or not answer.strip():
        reason = choice.get("finish_reason")
        if reason in ("length", "max_tokens"):
            raise ValueError("模型在输出上限内没有生成最终回答。请缩短问题或岗位描述后重试。")
        if reason in ("content_filter", "sensitive"):
            raise ValueError("模型未返回正文，可能触发内容过滤。请调整问题后重试。")
        raise ValueError("智谱已响应，但没有返回最终正文。请稍后重试；若持续出现，请提供错误文字，不要发送 API Key。")
    return {"answer": answer.strip(), "usage": result.get("usage") or {}, "model": MODEL}
