from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import p4_comms


def make_settings(accounts=None):
    accounts = accounts or [
        p4_comms.Account("outlook", "outlook", "outlook", "outlook", "m365", "", "user@example.com"),
        p4_comms.Account("gmail", "gmail", "gmail", "gmail", "calendula", "", ""),
        p4_comms.Account("yahoo", "yahoo", "yahoo", "yahoo", "calendula", "", ""),
    ]
    return p4_comms.Settings(
        config_path=Path("config.json"),
        accounts=tuple(accounts),
        executables={"himalaya": "fake-himalaya", "calendula": "fake-calendula", "m365": "fake-m365"},
        timezone="UTC",
    )


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.settings = make_settings()

    def run_json(self, operation, name, arguments):
        self.calls.append((operation, name, list(arguments)))
        response = self.responses.get(operation, {"ok": True, "data": []})
        return response() if callable(response) else response

    def available(self, name):
        return True


class BridgeTests(unittest.TestCase):
    def test_parse_json_with_cli_banner(self):
        self.assertEqual(p4_comms._parse_json_output("notice\n[{\"id\": 1}]"), [{"id": 1}])

    def test_himalaya_search_uses_argv_not_shell_text(self):
        runner = FakeRunner({"email.search": {"ok": True, "data": []}})
        adapter = p4_comms.HimalayaAdapter(runner)
        adapter.search(runner.settings.accounts[1], 'from:"a&b@example.com"')
        operation, name, args = runner.calls[-1]
        self.assertEqual((operation, name), ("email.search", "himalaya"))
        self.assertTrue(any('a&b@example.com' in token for token in args))
        self.assertNotIn("&", " ".join(args[:2]))

    def test_himalaya_rejects_option_injection(self):
        runner = FakeRunner({})
        with self.assertRaises(p4_comms.BridgeError):
            p4_comms.HimalayaAdapter(runner).search(runner.settings.accounts[1], "--output json")

    def test_positional_ids_cannot_be_options(self):
        runner = FakeRunner({})
        with self.assertRaises(p4_comms.BridgeError):
            p4_comms.HimalayaAdapter(runner).read(runner.settings.accounts[1], "--output")

    def test_himalaya_mailbox_flag_precedes_trailing_query(self):
        runner = FakeRunner({"email.search": {"ok": True, "data": []}})
        p4_comms.HimalayaAdapter(runner).search(runner.settings.accounts[1], "from alice", "Archive")
        args = runner.calls[-1][2]
        self.assertEqual(args[3:6], ["envelope", "search", "--mailbox"])
        self.assertEqual(args[6], "Archive")

    def test_unified_email_normalizes_and_labels_accounts(self):
        runner = FakeRunner(
            {
                "email.recent": {
                    "ok": True,
                    "data": [
                        {"id": "2", "subject": "new", "receivedDateTime": "2026-09-11T12:00:00Z"},
                        {"id": "1", "subject": "old", "receivedDateTime": "2026-09-10T12:00:00Z"},
                    ],
                }
            }
        )
        comms = p4_comms.UnifiedComms(make_settings([runner.settings.accounts[1]]), runner)
        result = comms.recent_email("gmail", limit=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["account"], "gmail")
        self.assertEqual(result["items"][0]["id"], "2")

    def test_normalizer_handles_himalaya_address_arrays(self):
        account = make_settings().accounts[1]
        item = p4_comms._normalize_email(
            {
                "id": "42",
                "from": [{"name": "Ada", "email": "ada@example.com"}],
                "to": [{"name": "Grace", "email": "grace@example.com"}],
                "has_attachment": True,
            },
            account,
        )
        self.assertEqual(item["from"], "Ada <ada@example.com>")
        self.assertEqual(item["to"], ["Grace <grace@example.com>"])
        self.assertTrue(item["has_attachments"])

    def test_redactor_handles_camelcase_token_keys(self):
        self.assertEqual(
            p4_comms._redact_json({"accessToken": "secret", "clientSecret": "secret", "subject": "ok"}),
            {"accessToken": "[REDACTED]", "clientSecret": "[REDACTED]", "subject": "ok"},
        )

    def test_partial_results_keep_provider_error(self):
        runner = FakeRunner(
            {
                "email.recent": lambda: {"ok": True, "data": [{"id": "1", "subject": "ok"}]},
            }
        )
        accounts = [
            runner.settings.accounts[1],
            runner.settings.accounts[2],
        ]
        original = runner.responses["email.recent"]
        calls = {"count": 0}

        def mixed_response():
            calls["count"] += 1
            return original() if calls["count"] == 1 else {"ok": False, "error": "auth", "message": "not logged in"}

        runner.responses["email.recent"] = mixed_response
        comms = p4_comms.UnifiedComms(make_settings(accounts), runner)
        result = comms.recent_email("all")
        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["errors"][0]["account"], "yahoo")

    def test_calendar_backend_commands_are_separate(self):
        runner = FakeRunner({"calendar.upcoming": {"ok": True, "data": []}})
        comms = p4_comms.UnifiedComms(make_settings(), runner)
        comms.upcoming_calendar("all", "2026-09-11", "2026-09-18")
        calls = [(name, args) for _, name, args in runner.calls]
        # Both backends are called, but the provider-specific command families remain distinct.
        self.assertTrue(any(args[:3] == ["outlook", "event", "list"] for _, args in calls))
        self.assertTrue(any(args[:3] == ["--account", "gmail", "--json"] for _, args in calls))

    def test_m365_calendar_requires_username(self):
        runner = FakeRunner({})
        account = p4_comms.Account("outlook", "outlook", "outlook", "outlook", "m365", "", "")
        comms = p4_comms.UnifiedComms(make_settings([account]), runner)
        result = comms.upcoming_calendar("outlook", "2026-09-11", "2026-09-18")
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["error"], "m365_username_required")
        self.assertEqual(runner.calls, [])

    def test_teams_commands_are_fixed_and_read_only(self):
        runner = FakeRunner({"teams.list": {"ok": True, "data": []}})
        comms = p4_comms.UnifiedComms(make_settings(), runner)
        comms.teams_list()
        _, name, args = runner.calls[-1]
        self.assertEqual(name, "m365")
        self.assertEqual(args[:4], ["teams", "team", "list", "--output"])
        self.assertNotIn("send", args)
        self.assertNotIn("remove", args)

    def test_teams_since_is_date_only(self):
        runner = FakeRunner({"teams.messages": {"ok": True, "data": []}})
        comms = p4_comms.UnifiedComms(make_settings(), runner)
        comms.teams_messages("team", "channel", "2026-09-11T12:00:00Z")
        args = runner.calls[-1][2]
        self.assertEqual(args[-4:-2], ["--since", "2026-09-11"])

    def test_config_file_has_no_secret_requirement(self):
        path = Path(__file__).resolve().parents[1] / "config.example.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("password", json.dumps(document).lower())
        settings = p4_comms.load_settings(path)
        self.assertEqual(settings.accounts[1].provider, "gmail")
        self.assertEqual(settings.accounts[1].calendar_backend, "calendula")


if __name__ == "__main__":
    unittest.main()
