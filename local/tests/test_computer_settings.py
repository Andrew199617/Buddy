"""What a person keeps for each computer: reading it leniently, and ignoring entries from before a computer was set up again."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions import computer_settings  # noqa: E402
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402

ComputerSettings = computer_settings.ComputerSettings


class ComputerSettingsTests(unittest.TestCase):
    def test_saved_settings_read_back_as_written(self):
        saved = {
            'pc-1': ComputerSettings('rev-1', 'C:\\code', {'claude': 'C:\\tools\\claude.exe'}),
            'local': ComputerSettings('local'),
        }
        stored = computer_settings.serialize(saved)
        self.assertEqual(stored['version'], computer_settings.VERSION)
        self.assertEqual(computer_settings.parse(stored), saved)

    def test_unreadable_entries_read_as_nothing_saved(self):
        stored = {'computers': {
            'no-revision': {'start_folder': 'C:\\code'},
            'not-a-dict': 'C:\\code',
            'odd-fields': {'revision': 'rev-1', 'start_folder': 5, 'cli_paths': {'claude': 7, 'codex': ''}},
        }}
        self.assertEqual(computer_settings.parse(stored), {'odd-fields': ComputerSettings('rev-1')})
        self.assertEqual(computer_settings.parse(None), {})
        self.assertEqual(computer_settings.parse({'computers': []}), {})

    def test_an_entry_counts_only_at_its_computers_current_revision(self):
        saved = {'pc-1': ComputerSettings('rev-1', 'C:\\code')}
        self.assertEqual(computer_settings.current(saved, 'pc-1', 'rev-1'), saved['pc-1'])
        self.assertIsNone(computer_settings.current(saved, 'pc-1', 'rev-2'))
        self.assertIsNone(computer_settings.current(saved, 'pc-1', None))
        self.assertIsNone(computer_settings.current(saved, 'pc-2', 'rev-1'))

    def test_a_missing_cli_path_lets_buddy_find_the_cli(self):
        self.assertEqual(ComputerSettings('rev-1').cli_path('codex'), '')
        self.assertEqual(computer_settings.clean_cli_path(None), '')
        self.assertEqual(computer_settings.clean_cli_path('  C:\\tools\\codex.exe '), 'C:\\tools\\codex.exe')

    def test_cli_paths_buddy_cannot_use_are_refused(self):
        for path in ('C:\\tools\nclaude.exe', 'C:\\' + 'a' * 1100, 5):
            with self.subTest(path=path):
                with self.assertRaises(SubscriptionError):
                    computer_settings.clean_cli_path(path)


if __name__ == '__main__':
    unittest.main()
