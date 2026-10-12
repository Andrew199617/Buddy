"""Blocking file-system calls that may hang on a sleeping drive, kept to a few threads."""

import asyncio
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner.disk_calls import DiskCalls, DiskCallsBusy  # noqa: E402


class DiskCallsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = DiskCalls(2, 'test-disk')
        self.addCleanup(self.calls.shutdown)

    async def test_a_call_returns_its_answer_and_frees_its_slot(self):
        self.assertEqual(await self.calls.run(1, lambda first, second: first + second, 2, 3), 5)
        self.assertEqual(self.calls.busy, 0)

    async def test_a_call_that_times_out_keeps_its_slot_until_the_drive_answers(self):
        drive = threading.Event()
        self.addCleanup(drive.set)
        for _ in range(2):
            with self.assertRaises(asyncio.TimeoutError):
                await self.calls.run(0.05, drive.wait, 5)
        self.assertEqual(self.calls.busy, 2)
        # Every slot waits on the drive, so another call is refused at once instead of queueing.
        with self.assertRaises(DiskCallsBusy):
            await self.calls.run(1, lambda: True)
        drive.set()
        for _ in range(100):
            if self.calls.busy == 0:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.calls.busy, 0)
        self.assertTrue(await self.calls.run(1, lambda: True))

    async def test_a_failing_call_reports_its_error_and_frees_its_slot(self):
        def fail():
            raise OSError('Fixture drive error')

        with self.assertRaisesRegex(OSError, 'Fixture drive error'):
            await self.calls.run(1, fail)
        self.assertEqual(self.calls.busy, 0)

    async def test_stuck_calls_never_take_the_shared_threads(self):
        drive = threading.Event()
        self.addCleanup(drive.set)
        with self.assertRaises(asyncio.TimeoutError):
            await self.calls.run(0.05, drive.wait, 5)
        # asyncio's own worker threads, which move process output, still answer at once.
        self.assertTrue(await asyncio.wait_for(asyncio.to_thread(lambda: True), 1))


if __name__ == '__main__':
    unittest.main()
