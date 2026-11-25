import json
from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from careerdesk.zhipu_trial import analyze_positions, position_payload


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return json.dumps({"choices": [{"message": {"content": "两条岗位部门不同，应核对后分别保留"}}],
                           "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()


class EmptyResponse(FakeResponse):
    def read(self, *_):
        return json.dumps({"choices": [{"message": {"content": "", "reasoning_content": "思考中"},
                                         "finish_reason": "length"}]}).encode()


class ZhipuTrialTests(unittest.TestCase):
    def test_sends_only_selected_position_fields_and_never_writes(self):
        position = {"company_name": "太古", "title": "管培生", "department": "海运",
                    "jd": "职责", "notes": "私人备注", "application_id": "secret"}
        with patch("careerdesk.zhipu_trial.urlopen", return_value=FakeResponse()) as call:
            answer = analyze_positions("test-key", [position], "是否重复？")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        sent = payload["messages"][1]["content"]
        self.assertIn("海运", sent)
        self.assertNotIn("私人备注", sent)
        self.assertNotIn("secret", sent)
        self.assertEqual("glm-4.7-flash", payload["model"])
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        self.assertEqual(100, answer["usage"]["prompt_tokens"])

    def test_empty_answer_reports_output_limit_without_exposing_reasoning(self):
        with patch("careerdesk.zhipu_trial.urlopen", return_value=EmptyResponse()):
            with self.assertRaisesRegex(ValueError, "输出上限") as error:
                analyze_positions("test-key", [{"title": "岗位"}], "分析")
        self.assertNotIn("思考中", str(error.exception))

    def test_requires_key_and_at_most_three_positions(self):
        with self.assertRaises(ValueError):
            analyze_positions("", [{"title": "岗位"}], "分析")
        with self.assertRaises(ValueError):
            position_payload([{"title": "岗位"}] * 4)

    def test_429_shows_provider_code_without_exposing_response_or_key(self):
        error_body = json.dumps({"error": {"code": "1302", "message":
            "High concurrency; Bearer test-key"}}).encode()
        response = HTTPError("https://example.invalid", 429, "Too Many Requests", {}, BytesIO(error_body))
        with patch("careerdesk.zhipu_trial.urlopen", side_effect=response):
            with self.assertRaises(ValueError) as error:
                analyze_positions("test-key", [{"title": "岗位"}], "分析")
        self.assertIn("1302", str(error.exception))
        self.assertIn("并发限制", str(error.exception))
        self.assertNotIn("test-key", str(error.exception))

    def test_429_balance_message_points_to_account_resources(self):
        response = HTTPError("https://example.invalid", 429, "Too Many Requests", {},
                             BytesIO(b'{"error":{"code":"1304","message":"Insufficient balance"}}'))
        with patch("careerdesk.zhipu_trial.urlopen", side_effect=response):
            with self.assertRaises(ValueError) as error:
                analyze_positions("test-key", [{"title": "岗位"}], "分析")
        self.assertIn("可用额度", str(error.exception))


if __name__ == "__main__":
    unittest.main()
