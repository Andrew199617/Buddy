"""The throttle on Buddy's unauthenticated enrollment endpoint stays bounded.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_enroll_throttle.py -v
"""

import ast
import time
import unittest
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_throttle_class():
    """EnrollThrottle from routers/runners.py, without importing the server."""
    path = ROOT / 'backend' / 'open_webui' / 'routers' / 'runners.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if getattr(node, 'name', None) == 'EnrollThrottle']
    namespace = {'time': time, 'OrderedDict': OrderedDict}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['EnrollThrottle']


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class EnrollThrottleTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.throttle = load_throttle_class()(per_address=20, max_addresses=100, window_seconds=600, clock=self.clock)

    def test_one_address_is_limited_until_its_window_passes(self):
        allowed = [self.throttle.allow('192.0.2.1') for _ in range(21)]
        self.assertEqual(allowed, [True] * 20 + [False])
        self.clock.now += 601
        self.assertTrue(self.throttle.allow('192.0.2.1'))

    def test_rotating_forwarded_addresses_never_block_anyone_else(self):
        for index in range(5000):
            self.throttle.allow(f'spoofed-{index}')
        self.assertTrue(self.throttle.allow('192.0.2.1'))

    def test_memory_stays_bounded_however_many_addresses_are_used(self):
        for index in range(5000):
            self.throttle.allow(f'spoofed-{index}')
        self.assertEqual(len(self.throttle.by_address), 100)
        self.assertNotIn('spoofed-0', self.throttle.by_address)
        self.assertIn('spoofed-4999', self.throttle.by_address)

    def test_a_limited_address_seen_again_stays_limited(self):
        for _ in range(25):
            self.throttle.allow('192.0.2.1')
        for index in range(50):
            self.throttle.allow(f'spoofed-{index}')
        self.assertFalse(self.throttle.allow('192.0.2.1'))

    def test_long_forwarded_addresses_are_kept_short(self):
        self.throttle.allow('x' * 10000)
        self.assertEqual([len(key) for key in self.throttle.by_address], [64])


if __name__ == '__main__':
    unittest.main()
