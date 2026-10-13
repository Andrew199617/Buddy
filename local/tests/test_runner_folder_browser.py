"""Folder names on the Runner's computer, for choosing where a chat works.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_folder_browser.py -v
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner import folder_browser  # noqa: E402
from buddy_runner.folder_browser import FolderListingError, list_folders, list_roots  # noqa: E402

WINDOWS = sys.platform == 'win32'


def make_hidden(path: Path) -> Path:
    if WINDOWS:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)
        return path
    hidden = path.with_name('.' + path.name)
    path.rename(hidden)
    return hidden


class ListFoldersTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-folders-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ('beta', 'Alpha', 'gamma'):
            (self.root / name).mkdir()
        (self.root / 'notes.txt').write_text('private', encoding='utf-8')

    def names(self, listing):
        return [entry['name'] for entry in listing['entries']]

    def test_lists_only_folders_sorted_by_name(self):
        listing = list_folders(str(self.root))
        self.assertEqual(self.names(listing), ['Alpha', 'beta', 'gamma'])
        self.assertFalse(listing['truncated'])
        # Names and two flags only: no sizes, times or file names.
        self.assertEqual(set(listing['entries'][0]), {'name', 'hidden', 'link'})

    def test_hidden_folders_show_only_when_asked(self):
        hidden = make_hidden(self.root / 'gamma')
        self.assertEqual(self.names(list_folders(str(self.root))), ['Alpha', 'beta'])
        shown = list_folders(str(self.root), show_hidden=True)
        flags = {entry['name']: entry['hidden'] for entry in shown['entries']}
        self.assertTrue(flags[hidden.name])
        self.assertFalse(flags['Alpha'])

    def test_a_prefix_narrows_the_list(self):
        self.assertEqual(self.names(list_folders(str(self.root), prefix='B')), ['beta'])

    @unittest.skipUnless(WINDOWS, 'junctions are a Windows feature')
    def test_junctions_are_marked_as_links(self):
        import _winapi

        _winapi.CreateJunction(str(self.root / 'Alpha'), str(self.root / 'shortcut'))
        flags = {entry['name']: entry['link'] for entry in list_folders(str(self.root))['entries']}
        self.assertTrue(flags['shortcut'])
        self.assertFalse(flags['Alpha'])

    def test_long_listings_are_capped(self):
        with patch.object(folder_browser, 'MAX_LISTED_FOLDERS', 2):
            listing = list_folders(str(self.root))
        self.assertEqual(len(listing['entries']), 2)
        self.assertTrue(listing['truncated'])

    def test_a_scan_stops_at_its_deadline(self):
        listing = list_folders(str(self.root), deadline=time.monotonic() - 1)
        self.assertTrue(listing['truncated'])
        self.assertEqual(listing['entries'], [])

    def test_errors_never_repeat_the_path(self):
        missing = self.root / 'secret-project'
        with self.assertRaises(FolderListingError) as refused:
            list_folders(str(missing))
        self.assertEqual(str(refused.exception), "That folder doesn't exist.")
        with self.assertRaises(FolderListingError) as refused:
            list_folders(str(self.root / 'notes.txt'))
        self.assertNotIn('notes', str(refused.exception))

    def test_relative_and_odd_paths_are_refused(self):
        for path in ('relative', '', 'C:\\x\x00y', '/' + 'a' * 1100, None):
            with self.subTest(path=str(path)[:20]):
                with self.assertRaises(FolderListingError):
                    list_folders(path)

    @unittest.skipUnless(WINDOWS, 'network and device paths are Windows forms')
    def test_network_and_device_paths_are_refused(self):
        for path in ('\\\\server\\share', '//server/share', '\\\\?\\C:\\Windows', '\\\\.\\PhysicalDrive0'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(FolderListingError, 'Network paths'):
                    list_folders(path)


class RootTests(unittest.TestCase):
    def test_roots_include_a_drive_and_home(self):
        roots = list_roots()
        kinds = {root['kind'] for root in roots}
        self.assertIn('drive', kinds)
        home = [root for root in roots if root['name'] == 'Home']
        self.assertEqual(home[0]['path'], str(Path.home()))

    @unittest.skipUnless(WINDOWS, 'drive letters are a Windows feature')
    def test_windows_drives_are_listed_without_opening_them(self):
        with patch.object(Path, 'is_dir', side_effect=AssertionError('opened a drive')) as opened:
            drives = folder_browser._windows_drives()
        opened.assert_not_called()
        system_drive = os.environ.get('SystemDrive', 'C:')
        self.assertIn(system_drive, [drive['name'] for drive in drives])


if __name__ == '__main__':
    unittest.main()
