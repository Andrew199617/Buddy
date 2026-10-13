"""Which Buddy addresses a Runner may send its machine credential to.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_urls.py -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner.urls import normalize_server_url, websocket_url  # noqa: E402


class ServerAddressTests(unittest.TestCase):
    def test_https_anywhere_and_http_only_to_this_computer(self):
        accepted = {
            'https://buddy.example.com': 'https://buddy.example.com',
            'https://Buddy.Example.com/': 'https://buddy.example.com',
            'https://buddy.example.com:8443': 'https://buddy.example.com:8443',
            'http://localhost:8082': 'http://localhost:8082',
            'http://127.0.0.1:8092/': 'http://127.0.0.1:8092',
            'http://[::1]:8082': 'http://[::1]:8082',
        }
        for url, expected in accepted.items():
            with self.subTest(url=url):
                self.assertEqual(normalize_server_url(url), expected)

    def test_addresses_that_could_expose_the_credential_are_refused(self):
        refused = [
            '',
            'buddy.example.com',
            'http://buddy.example.com',
            'http://192.168.1.20:8082',
            'http://host.docker.internal:8081',
            'https://user:password@buddy.example.com',
            'https://buddy.example.com/path',
            'https://buddy.example.com/?next=x',
            'ftp://buddy.example.com',
        ]
        for url in refused:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    normalize_server_url(url)

    def test_websocket_scheme_follows_the_server_scheme(self):
        self.assertEqual(websocket_url('https://buddy.example.com', '/api/v1/runners/connect'),
                         'wss://buddy.example.com/api/v1/runners/connect')
        self.assertEqual(websocket_url('http://localhost:8082', '/api/v1/runners/connect'),
                         'ws://localhost:8082/api/v1/runners/connect')


if __name__ == '__main__':
    unittest.main()
