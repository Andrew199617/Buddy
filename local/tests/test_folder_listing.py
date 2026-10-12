"""Folder listings from a computer, checked on the server before Buddy uses them.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_folder_listing.py -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402
from open_webui.utils.subscriptions.folder_listing import (  # noqa: E402
    checked_listing,
    checked_roots,
    checked_suggestions,
    clean_browse_path,
)


class ListingTests(unittest.TestCase):
    def test_child_paths_are_built_on_the_server(self):
        reply = {'entries': [
            {'name': 'site', 'hidden': False, 'link': False, 'path': 'D:\\elsewhere'},
            {'name': 'cache', 'hidden': True, 'link': True},
        ], 'truncated': False}
        listing = checked_listing(reply, 'C:\\Users\\sam\\code', 'win32')
        self.assertEqual([entry['path'] for entry in listing['entries']], ['C:\\Users\\sam\\code\\site', 'C:\\Users\\sam\\code\\cache'])
        self.assertEqual(listing['parent'], 'C:\\Users\\sam')
        self.assertTrue(listing['entries'][1]['hidden'])
        self.assertTrue(listing['entries'][1]['link'])
        self.assertTrue(listing['entries'][0]['selectable'])

    def test_hostile_names_are_dropped(self):
        names = ['ok', '..', '.', 'a/b', 'a\\b', 'bad\x00name', '', 'x' * 300, 42, None]
        reply = {'entries': [{'name': name} for name in names]}
        listing = checked_listing(reply, '/home/sam', 'linux')
        self.assertEqual([entry['name'] for entry in listing['entries']], ['ok'])
        self.assertEqual(listing['entries'][0]['path'], '/home/sam/ok')

    def test_a_drive_has_no_parent_and_its_folders_are_selectable(self):
        listing = checked_listing({'entries': [{'name': 'Work'}]}, 'D:\\', 'win32')
        self.assertIsNone(listing['parent'])
        self.assertEqual(listing['entries'][0]['path'], 'D:\\Work')
        root = checked_listing({'entries': [{'name': 'srv'}]}, '/', 'linux')
        self.assertIsNone(root['parent'])
        self.assertEqual(root['entries'][0]['path'], '/srv')

    def test_long_replies_are_capped_and_marked(self):
        reply = {'entries': [{'name': f'f{index}'} for index in range(1200)]}
        listing = checked_listing(reply, '/data', 'linux')
        self.assertEqual(len(listing['entries']), 1000)
        self.assertTrue(listing['truncated'])

    def test_a_reply_without_entries_is_refused(self):
        for reply in (None, [], {'entries': 'x'}):
            with self.subTest(reply=reply):
                with self.assertRaisesRegex(SubscriptionError, 'Update the Buddy app'):
                    checked_listing(reply, '/data', 'linux')


class BrowsePathTests(unittest.TestCase):
    def test_drives_can_be_browsed_but_network_paths_cannot(self):
        self.assertEqual(clean_browse_path('C:\\', 'win32'), 'C:\\')
        self.assertEqual(clean_browse_path('/', 'linux'), '/')
        for path in ('\\\\server\\share', '/\\server\\share', 'relative', None):
            with self.subTest(path=path):
                with self.assertRaises(SubscriptionError):
                    clean_browse_path(path, 'win32')


class RootTests(unittest.TestCase):
    def test_roots_are_checked_one_by_one(self):
        reply = {'roots': [
            {'name': 'C:', 'path': 'C:\\', 'kind': 'drive'},
            {'name': 'Share', 'path': '\\\\server\\share', 'kind': 'remote_drive'},
            {'name': 'Odd', 'path': 'C:\\x', 'kind': 'printer'},
            {'name': 'Home', 'path': 'C:\\Users\\sam', 'kind': 'place'},
        ]}
        roots = checked_roots(reply, 'win32')['roots']
        self.assertEqual([root['name'] for root in roots], ['C:', 'Home'])


class SuggestionTests(unittest.TestCase):
    def test_suggestions_must_be_clean_project_folders(self):
        reply = {'folders': [
            {'path': 'C:\\code\\site', 'sources': ['claude', 'codex', 'evil'], 'last_used': 1760300000},
            {'path': 'c:\\CODE\\site', 'sources': ['codex'], 'last_used': 1},
            {'path': 'C:\\', 'sources': ['claude'], 'last_used': 1},
            {'path': '\\\\server\\share', 'sources': ['claude'], 'last_used': 1},
            {'path': 'C:\\docs', 'sources': [], 'last_used': 1},
            {'path': 'C:\\notes', 'sources': ['codex'], 'last_used': True},
        ]}
        suggestions = checked_suggestions(reply, 'win32')
        self.assertEqual(suggestions, [
            {'path': 'C:\\code\\site', 'sources': ['claude', 'codex'], 'last_used': 1760300000},
            {'path': 'C:\\notes', 'sources': ['codex'], 'last_used': 0},
        ])


if __name__ == '__main__':
    unittest.main()
