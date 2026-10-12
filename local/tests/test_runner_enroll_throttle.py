"""The throttle on Buddy's unauthenticated enrollment endpoint stays bounded.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_enroll_throttle.py -v
"""

import ast
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_throttle_class():
    """EnrollThrottle from routers/runners.py, without importing the server."""
    path = ROOT / 'backend' / 'open_webui' / 'routers' / 'runners.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if getattr(node, 'name', None) == 'EnrollThrottle']
    namespace = {'time': time}
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
        self.throttle = load_throttle_class()(per_address=20, overall=200, window_seconds=600, clock=self.clock)

    def test_one_address_is_limited_until_its_window_passes(self):
        allowed = [self.throttle.allow('192.0.2.1') for _ in range(21)]
        self.assertEqual(allowed, [True] * 20 + [False])
        self.clock.now += 601
        self.assertTrue(self.throttle.allow('192.0.2.1'))

    def test_rotating_forwarded_addresses_cannot_exceed_the_overall_ceiling(self):
        allowed = [self.throttle.allow(f'spoofed-{index}') for index in range(5000)]
        self.assertEqual(sum(allowed), 200)
        self.assertLessEqual(len(self.throttle.by_address), 200)

    def test_a_refused_attempt_remembers_nothing(self):
        for index in range(200):
            self.throttle.allow(f'spoofed-{index}')
        self.assertFalse(self.throttle.allow('one-more'))
        self.assertNotIn('one-more', self.throttle.by_address)

    def test_addresses_whose_window_passed_are_forgotten(self):
        for index in range(50):
            self.throttle.allow(f'spoofed-{index}')
        self.clock.now += 601
        self.assertTrue(self.throttle.allow('192.0.2.1'))
        self.assertEqual(list(self.throttle.by_address), ['192.0.2.1'])
        self.assertEqual(len(self.throttle.all_attempts), 1)


if __name__ == '__main__':
    unittest.main()
