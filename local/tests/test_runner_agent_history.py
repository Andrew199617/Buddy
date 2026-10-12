"""Folders Claude Code and Codex worked in, read from their session history without secrets.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_agent_history.py -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner import agent_history  # noqa: E402
from buddy_runner.agent_history import suggest_folders  # noqa: E402

SECRET = 'sk-ant-oat01-fixture-secret-in-a-transcript'
DAY = 86400


def far_future() -> float:
    return time.monotonic() + 60


class AgentHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-history-test-')
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.claude = self.base / 'claude-home'
        self.codex = self.base / 'codex-home'
        self.site = self.make_folder('code/site')
        self.docs = self.make_folder('code/docs')
        self.notes = self.make_folder('code/notes')
        # Credential files sit at the top level of each home; they must never be opened.
        self.write(self.claude / '.credentials.json', '{"claudeAiOauth": "' + SECRET + '"}')
        self.write(self.codex / 'auth.json', '{"tokens": "' + SECRET + '"}')

    def make_folder(self, relative: str) -> Path:
        folder = self.base / relative
        folder.mkdir(parents=True)
        return folder

    def write(self, path: Path, text: str, modified: float | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        if modified is not None:
            os.utime(path, (modified, modified))
        return path

    def claude_session(self, project: str, name: str, cwd: str, modified: float | None = None) -> Path:
        lines = [
            json.dumps({'type': 'summary', 'summary': 'Talk about ' + SECRET}),
            json.dumps({'type': 'user', 'cwd': cwd, 'message': {'content': SECRET}}),
        ]
        return self.write(self.claude / 'projects' / project / name, '\n'.join(lines) + '\n', modified)

    def codex_session(self, relative: str, cwd: str, modified: float | None = None) -> Path:
        first = json.dumps({'type': 'session_meta', 'payload': {'cwd': cwd, 'base_instructions': SECRET}})
        rest = json.dumps({'type': 'response_item', 'payload': {'content': SECRET}})
        return self.write(self.codex / relative, first + '\n' + rest + '\n', modified)

    def suggest(self, exclusions=None):
        return suggest_folders(self.claude, self.codex, exclusions or [], far_future())

    def test_folders_from_both_tools_come_newest_first(self):
        now = time.time()
        self.claude_session('C--code-site', 'a.jsonl', str(self.site), now - 3 * DAY)
        self.codex_session('sessions/2026/10/12/rollout-1.jsonl', str(self.docs), now)
        self.codex_session('archived_sessions/rollout-2.jsonl', str(self.site), now - DAY)
        result = self.suggest()
        self.assertEqual([folder['path'] for folder in result['folders']], [str(self.docs), str(self.site)])
        site = result['folders'][1]
        self.assertEqual(site['sources'], ['claude', 'codex'])
        # Rounded down to the day, never the exact time.
        self.assertEqual(site['last_used'] % DAY, 0)
        self.assertFalse(result['truncated'])

    def test_only_paths_leave_never_transcript_text(self):
        self.claude_session('C--code-site', 'a.jsonl', str(self.site))
        self.codex_session('sessions/2026/10/12/rollout-1.jsonl', str(self.docs))
        result = self.suggest()
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn('a.jsonl', json.dumps(result))
        self.assertEqual(set(result['folders'][0]), {'path', 'sources', 'last_used'})

    def test_credential_files_are_never_opened(self):
        self.claude_session('C--code-site', 'a.jsonl', str(self.site))
        self.codex_session('sessions/2026/10/12/rollout-1.jsonl', str(self.docs))
        opened = []
        real_open = os.open

        def recording_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with patch.object(agent_history.os, 'open', recording_open):
            self.suggest()
        self.assertTrue(opened)
        for path in opened:
            self.assertNotIn('.credentials.json', path)
            self.assertNotIn('auth.json', path)
            self.assertTrue(path.endswith('.jsonl'))

    def test_folders_that_no_longer_exist_or_are_excluded_are_left_out(self):
        self.claude_session('gone', 'a.jsonl', str(self.base / 'deleted-project'))
        self.claude_session('buddy', 'b.jsonl', str(self.notes))
        self.claude_session('relative', 'c.jsonl', 'code/site')
        result = self.suggest(exclusions=[str(self.notes.parent / 'notes')])
        self.assertEqual(result['folders'], [])

    def test_subagent_sessions_count_when_a_project_has_no_other(self):
        self.write(
            self.claude / 'projects' / 'C--code-site' / 'session-1' / 'subagents' / 'agent.jsonl',
            json.dumps({'cwd': str(self.site)}) + '\n',
        )
        self.assertEqual([folder['path'] for folder in self.suggest()['folders']], [str(self.site)])

    def test_hard_linked_session_files_are_not_read(self):
        outside = self.write(self.base / 'elsewhere.jsonl', json.dumps({'cwd': str(self.site)}) + '\n')
        target = self.claude / 'projects' / 'linked' / 'a.jsonl'
        target.parent.mkdir(parents=True)
        os.link(outside, target)
        result = self.suggest()
        self.assertEqual(result['folders'], [])
        self.assertEqual(result['skipped'], 1)

    def test_symlinked_session_files_are_not_read(self):
        outside = self.write(self.base / 'elsewhere.jsonl', json.dumps({'cwd': str(self.site)}) + '\n')
        target = self.claude / 'projects' / 'linked' / 'a.jsonl'
        target.parent.mkdir(parents=True)
        try:
            os.symlink(outside, target)
        except OSError:
            self.skipTest('This account cannot create symlinks.')
        self.assertEqual(self.suggest()['folders'], [])

    def test_reading_stops_at_its_limits(self):
        huge = json.dumps({'type': 'summary', 'summary': 'x' * 2000}) + '\n'
        long_session = self.claude / 'projects' / 'long' / 'a.jsonl'
        self.write(long_session, huge + json.dumps({'cwd': str(self.site)}) + '\n')
        with patch.object(agent_history, 'CLAUDE_MAX_BYTES', 1000):
            self.assertEqual(self.suggest()['folders'], [])
        long_session.unlink()
        # Codex reads only a first line that describes the session.
        not_meta = json.dumps({'type': 'response_item', 'payload': {'cwd': str(self.docs)}}) + '\n'
        self.write(self.codex / 'sessions/2026/10/12/rollout-1.jsonl', not_meta)
        self.assertEqual(self.suggest()['folders'], [])

    def test_a_scan_past_its_deadline_says_so(self):
        self.claude_session('C--code-site', 'a.jsonl', str(self.site))
        result = suggest_folders(self.claude, self.codex, [], time.monotonic() - 1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['folders'], [])

    def test_missing_homes_give_nothing(self):
        result = suggest_folders(self.base / 'none', self.base / 'nothing', [], far_future())
        self.assertEqual(result, {'folders': [], 'skipped': 0, 'truncated': False})

    def test_buddy_and_system_folders_are_excluded_by_default(self):
        state = self.base / 'runner-state'
        excluded = agent_history.default_exclusions(state)
        self.assertIn(str(state), excluded)


if __name__ == '__main__':
    unittest.main()
