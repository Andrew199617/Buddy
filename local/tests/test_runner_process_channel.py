"""The Runner's process streams over a transport other than its own WebSocket.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_process_channel.py -v

Every process is this test's Python interpreter running a short script.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner.runner import Runner  # noqa: E402
from buddy_runner.workspace_capabilities import WorkspaceCapabilities  # noqa: E402


class MemoryProcessChannel:
    """A process channel whose messages pass through in-memory queues."""

    def __init__(self):
        self.closed = False
        self.sent = asyncio.Queue()
        self.incoming = asyncio.Queue()

    async def receive_json(self, timeout):
        return await asyncio.wait_for(self.incoming.get(), timeout)

    async def commands(self):
        while True:
            command = await self.incoming.get()
            if command is None:
                return
            yield command

    async def send_json(self, message):
        if self.closed:
            raise ConnectionResetError('channel closed')
        await self.sent.put(message)

    async def close(self, code=None):
        if not self.closed:
            self.closed = True
            self.incoming.put_nowait(None)

    async def next_sent(self, timeout=10):
        return await asyncio.wait_for(self.sent.get(), timeout)


def start_message(script, start_id=None):
    message = {'type': 'start', 'args': [sys.executable, '-c', script]}
    if start_id is not None:
        message['start_id'] = start_id
    return message


class RunnerProcessChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-runner-channel-')
        self.addCleanup(directory.cleanup)
        self.runner = Runner(Path(directory.name), WorkspaceCapabilities())
        self.addAsyncCleanup(self.runner.close)

    async def test_process_runs_and_exits_over_a_supplied_channel(self):
        channel = MemoryProcessChannel()
        served = asyncio.create_task(
            self.runner.serve_process(channel, start_message('print("hello from channel")', 'start-1'))
        )
        started = await channel.next_sent()
        self.assertEqual(started['type'], 'started')
        output = ''
        while True:
            message = await channel.next_sent()
            if message['type'] == 'exit':
                break
            output += message['data']
        self.assertIn('hello from channel', output)
        self.assertEqual(message['code'], 0)
        await served
        self.assertEqual(await self.runner.stop_named_start('start-1'), {'stopped': True, 'known': True})
        self.assertEqual(self.runner.processes, set())
        self.assertEqual(self.runner.channels, set())

    async def test_named_start_of_running_process_is_stopped_and_confirmed(self):
        channel = MemoryProcessChannel()
        served = asyncio.create_task(
            self.runner.serve_process(channel, start_message('import time; time.sleep(30)', 'start-2'))
        )
        started = await channel.next_sent()
        self.assertEqual(started['type'], 'started')
        self.assertEqual(await self.runner.stop_named_start('start-2'), {'stopped': True, 'known': True})
        self.assertEqual(self.runner.processes, set())
        await channel.close()
        await served
        self.assertEqual(await self.runner.stop_named_start('start-2'), {'stopped': True, 'known': True})

    async def test_refused_start_is_remembered_as_never_started(self):
        channel = MemoryProcessChannel()
        await self.runner.serve_process(channel, {'type': 'start', 'args': [], 'start_id': 'start-3'})
        refused = await channel.next_sent()
        self.assertEqual(refused['type'], 'start_failed')
        self.assertEqual(await self.runner.stop_named_start('start-3'), {'stopped': True, 'known': True})

    async def test_unknown_start_name_reports_nothing_known(self):
        self.assertEqual(await self.runner.stop_named_start('never-sent'), {'stopped': True, 'known': False})

    async def test_repeated_start_name_is_refused_without_starting_anything(self):
        first = MemoryProcessChannel()
        await self.runner.serve_process(first, {'type': 'start', 'args': [], 'start_id': 'start-4'})
        await first.next_sent()
        second = MemoryProcessChannel()
        await self.runner.serve_process(second, start_message('print("must not run")', 'start-4'))
        refused = await second.next_sent()
        self.assertEqual(refused['type'], 'start_failed')
        self.assertEqual(self.runner.processes, set())


if __name__ == '__main__':
    unittest.main()
