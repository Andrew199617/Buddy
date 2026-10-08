import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "local-outlook-research.py"
SPEC = importlib.util.spec_from_file_location("outlook_research_tool", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OutlookResearchToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tool = MODULE.Tools()
        self.calls = []

    def completed(self, payload, code=0, stderr=""):
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(["m365"], code, stdout, stderr)

    def fake_run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return self.responses.pop(0)

    async def test_missing_cli_returns_install_instruction(self):
        result = json.loads(
            await self.tool.check_outlook_connection(
                __user__={"valves": {"M365_CLI_PATH": "C:\\does-not-exist\\m365.cmd"}}
            )
        )
        self.assertEqual(result["error"], "m365_cli_not_found")
        self.assertIn("M365_CLI_PATH", result["instruction"])

    async def test_check_uses_status_then_read_only_graph_request(self):
        self.responses = [
            self.completed(
                {
                    "connectedAs": "ada@example.com",
                    "authType": "DeviceCode",
                    "refresh_token": "should-not-appear",
                }
            ),
            self.completed(
                {
                    "displayName": "Ada",
                    "mail": "ada@example.com",
                    "userPrincipalName": "ada@example.com",
                }
            ),
        ]
        with patch.object(MODULE.shutil, "which", return_value="m365"), patch.object(
            MODULE.subprocess, "run", side_effect=self.fake_run
        ):
            result = json.loads(await self.tool.check_outlook_connection())
        self.assertTrue(result["ok"])
        self.assertNotIn("should-not-appear", json.dumps(result))
        self.assertEqual(self.calls[0][0][1:3], ["status", "--output"])
        self.assertEqual(self.calls[1][0][1:3], ["request", "--url"])
        self.assertIn("/me?%24select=", self.calls[1][0][3])
        self.assertEqual(self.calls[1][0][-2:], ["--output", "json"])

    async def test_search_builds_fixed_get_request_and_limits_results(self):
        self.responses = [
            self.completed(
                {
                    "value": [
                        {
                            "id": "m1",
                            "subject": "Quarterly plan",
                            "bodyPreview": "Preview",
                            "webLink": "https://outlook.office.com/mail/m1",
                        }
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=abc",
                }
            )
        ]
        with patch.object(MODULE.shutil, "which", return_value="m365"), patch.object(
            MODULE.subprocess, "run", side_effect=self.fake_run
        ):
            result = json.loads(
                await self.tool.search_outlook_messages(
                    'quarterly\n"plan"', limit=500, __user__={}
                )
            )
        argv = self.calls[0][0]
        self.assertEqual(argv[1:3], ["request", "--url"])
        self.assertIn("%24search=%22quarterly++plan%22", argv[3])
        self.assertEqual(argv[4:6], ["--method", "get"])
        self.assertEqual(result["items"][0]["web_link"], "https://outlook.office.com/mail/m1")
        self.assertTrue(result["has_more"])

    async def test_next_link_rejects_non_graph_hosts_without_running_cli(self):
        with patch.object(MODULE.shutil, "which", return_value="m365"), patch.object(
            MODULE.subprocess, "run", side_effect=self.fake_run
        ):
            result = json.loads(
                await self.tool.list_recent_outlook_messages(
                    next_link="https://evil.example/v1.0/me/messages"
                )
            )
        self.assertEqual(result["error"], "invalid_next_link")
        self.assertEqual(self.calls, [])

    async def test_cli_error_is_compact_and_actionable(self):
        self.responses = [self.completed("", 1, "ErrorAccessDenied: Mail.Read required")]
        with patch.object(MODULE.shutil, "which", return_value="m365"), patch.object(
            MODULE.subprocess, "run", side_effect=self.fake_run
        ):
            result = json.loads(await self.tool.list_recent_outlook_messages())
        self.assertEqual(result["error"], "m365_cli_command_failed")
        self.assertIn("Mail.Read required", result["details"])
        self.assertIn("m365 login", result["hint"])

    async def test_read_message_truncates_body_and_marks_it_untrusted(self):
        self.responses = [self.completed({"id": "m1", "body": {"content": "x" * 21000}})]
        with patch.object(MODULE.shutil, "which", return_value="m365"), patch.object(
            MODULE.subprocess, "run", side_effect=self.fake_run
        ):
            result = json.loads(await self.tool.read_outlook_message("m1"))
        self.assertEqual(len(result["body"]), 20012)  # includes the explicit truncation marker
        self.assertTrue(result["body_truncated"])
        self.assertIn("untrusted", result["security_note"])

    async def test_calendar_validates_range_before_running_cli(self):
        with patch.object(MODULE.shutil, "which", return_value="m365"), patch.object(
            MODULE.subprocess, "run", side_effect=self.fake_run
        ):
            result = json.loads(
                await self.tool.list_outlook_calendar(
                    "2026-09-12T00:00:00Z", "2026-09-11T00:00:00Z"
                )
            )
        self.assertEqual(result["error"], "end_must_be_after_start")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
