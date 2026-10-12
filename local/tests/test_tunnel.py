"""The Runner tunnel end to end: the real Runner and Buddy's hub over an in-memory connection.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_tunnel.py -v

Processes are this test's Python interpreter running short scripts.
"""

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner.runner import Runner  # noqa: E402
from buddy_runner.tunnel import Tunnel  # noqa: E402
from buddy_runner.workspace_capabilities import WorkspaceCapabilities  # noqa: E402
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402
from open_webui.utils.subscriptions.machines import (  # noqa: E402
    StartupCleanupUnconfirmedError,
    TunnelMachine,
)
from open_webui.utils.subscriptions.tunnel_hub import RunnerConnection, RunnerHub, TunnelStream  # noqa: E402

CLOSED = object()


class ServerSide:
    """What RunnerConnection reads and writes: text in, text out, close."""

    def __init__(self, link):
        self.link = link

    async def receive_text(self):
        text = await self.link.to_server.get()
        if text is CLOSED:
            raise ConnectionResetError('in-memory connection closed')
        return text

    async def send_text(self, text):
        if self.link.closed:
            raise ConnectionResetError('in-memory connection closed')
        self.link.to_runner.put_nowait(text)

    async def close(self, code, reason=''):
        self.link.drop()


class RunnerSide:
    """What the Runner's Tunnel reads and writes, shaped like an aiohttp WebSocket."""

    def __init__(self, link):
        self.link = link

    @property
    def closed(self):
        return self.link.closed

    def __aiter__(self):
        return self._messages()

    async def _messages(self):
        while True:
            text = await self.link.to_runner.get()
            if text is CLOSED:
                return
            yield types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=text)

    async def send_str(self, text):
        if self.link.closed:
            raise ConnectionResetError('in-memory connection closed')
        self.link.to_server.put_nowait(text)


class Link:
    def __init__(self):
        self.to_server = asyncio.Queue()
        self.to_runner = asyncio.Queue()
        self.closed = False

    def drop(self):
        if not self.closed:
            self.closed = True
            self.to_server.put_nowait(CLOSED)
            self.to_runner.put_nowait(CLOSED)


class TunnelEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-tunnel-test-')
        self.addCleanup(directory.cleanup)
        self.state_dir = Path(directory.name)
        self.runner = Runner(self.state_dir, WorkspaceCapabilities())
        self.addAsyncCleanup(self.runner.close)
        self.hub = RunnerHub()
        self.machine = TunnelMachine('machine-1', 'Fixture PC', self.hub)
        self.tasks = []
        self.connect()

    async def asyncTearDown(self):
        for link in self.links:
            link.drop()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def connect(self, runner=None):
        runner = runner or self.runner
        link = Link()
        self.links = getattr(self, 'links', []) + [link]
        connection = RunnerConnection(
            ServerSide(link), 'machine-1', 'owner-1', runner.workspaces.host['id'], runner.describe(),
        )
        self.hub.attach(connection)
        self.tasks.append(asyncio.create_task(connection.serve()))
        self.tasks.append(asyncio.create_task(Tunnel(RunnerSide(link), runner).serve()))
        return link, connection

    async def read_all(self, process):
        output = ''
        while True:
            chunk = await process.read_text()
            if chunk is None:
                return output
            output += chunk

    async def test_calls_reach_the_runner(self):
        self.assertTrue(await self.machine.path_exists(str(self.state_dir), 'dir'))
        self.assertFalse(await self.machine.path_exists(str(self.state_dir / 'missing'), 'dir'))
        self.assertEqual(await self.machine.chat_dir(), str(self.state_dir / 'chat'))

    async def test_a_process_runs_and_its_exit_confirms_cleanup(self):
        process = await self.machine.start_process(
            [sys.executable, '-c', 'import sys; print(sys.stdin.readline().strip().upper())'], str(self.state_dir)
        )
        await process.write('hello over the tunnel\n')
        process.close_stdin()
        self.assertIn('HELLO OVER THE TUNNEL', await self.read_all(process))
        self.assertEqual(await process.wait(10), 0)
        self.assertTrue(process.cleanup_confirmed)

    async def test_large_input_arrives_whole_in_bounded_pieces(self):
        text = 'x' * (600 * 1024) + '\n'
        script = 'import sys; data = sys.stdin.read(); print(len(data))'
        process = await self.machine.start_process([sys.executable, '-c', script], str(self.state_dir))
        await process.write(text)
        process.close_stdin()
        self.assertEqual((await self.read_all(process)).strip(), str(len(text)))
        await process.wait(10)

    async def test_kill_is_acknowledged_only_after_the_runner_stopped_it(self):
        process = await self.machine.start_process(
            [sys.executable, '-c', 'import time; time.sleep(30)'], str(self.state_dir)
        )
        self.assertEqual(len(self.runner.processes), 1)
        native = next(iter(self.runner.processes))
        await process.close()
        self.assertTrue(process.cleanup_confirmed)
        # The acknowledgement came only after the Runner's own cleanup finished.
        self.assertIsNotNone(native.returncode)
        async with asyncio.timeout(10):
            while self.runner.processes:
                await asyncio.sleep(0.02)

    async def test_a_dropped_connection_stops_the_process_and_reconnecting_confirms_it(self):
        process = await self.machine.start_process(
            [sys.executable, '-c', 'import time; time.sleep(30)'], str(self.state_dir)
        )
        first_link = self.links[0]
        first_link.drop()
        # The Runner stops processes whose connection ended.
        async with asyncio.timeout(10):
            while self.runner.processes:
                await asyncio.sleep(0.05)
        reconnect = asyncio.get_running_loop().call_later(0.2, self.connect)
        self.addCleanup(reconnect.cancel)
        await process.close()
        # The old stream is gone; the reconnected Runner confirmed the stop.
        self.assertTrue(process.cleanup_confirmed)

    async def test_a_restarted_runner_cannot_confirm_an_old_process(self):
        process = await self.machine.start_process(
            [sys.executable, '-c', 'import time; time.sleep(30)'], str(self.state_dir)
        )
        self.links[0].drop()
        async with asyncio.timeout(10):
            while self.runner.processes:
                await asyncio.sleep(0.05)
        restarted = Runner(self.state_dir, WorkspaceCapabilities())
        self.addAsyncCleanup(restarted.close)
        self.connect(restarted)
        with self.assertRaises((SubscriptionError, TimeoutError, Exception)):
            await process.close()
        self.assertFalse(process._confirmed_stopped.is_set())

    async def test_an_offline_machine_says_so(self):
        for link in self.links:
            link.drop()
        await asyncio.sleep(0)
        machine = TunnelMachine('machine-1', 'Fixture PC', self.hub)
        with self.assertRaisesRegex(SubscriptionError, 'offline'):
            await machine._connection(wait=0.1)


class StartupCleanupTests(unittest.IsolatedAsyncioTestCase):
    """A startup whose reply never came is cleaned up by its name."""

    class FakeConnection:
        def __init__(self, instance_id, answer):
            self.instance_id = instance_id
            self.answer = answer
            self.calls = []
            self.closed = False

        async def call(self, method, params, timeout=30):
            self.calls.append((method, params))
            return self.answer

    class FakeHub:
        def __init__(self, connection):
            self.connection_to_return = connection

        async def wait_connection(self, machine_id, timeout):
            return self.connection_to_return

    def stream(self, instance_id):
        stream = TunnelStream.__new__(TunnelStream)
        stream.start_id = 'start-1'
        stream.instance_id = instance_id
        stream._closed = True
        stream._connection = types.SimpleNamespace(forget_stream=lambda number: None)
        stream.number = 1
        return stream

    async def test_the_same_runner_answers_for_the_startup_name(self):
        connection = self.FakeConnection('instance-a', {'stopped': True, 'known': False})
        machine = TunnelMachine('machine-1', 'Fixture PC', self.FakeHub(connection))
        await machine.dispose_failed_start(self.stream('instance-a'), ['claude'], None)
        self.assertEqual(connection.calls, [('process.stop', {'start_id': 'start-1'})])

    async def test_a_restarted_runner_leaves_the_startup_unconfirmed(self):
        connection = self.FakeConnection('instance-b', {'stopped': True})
        machine = TunnelMachine('machine-1', 'Fixture PC', self.FakeHub(connection))
        with self.assertRaises(StartupCleanupUnconfirmedError):
            await machine.dispose_failed_start(self.stream('instance-a'), ['claude'], None)
        self.assertEqual(connection.calls, [])

    async def test_an_offline_machine_leaves_the_startup_unconfirmed(self):
        machine = TunnelMachine('machine-1', 'Fixture PC', self.FakeHub(None))
        with self.assertRaises(StartupCleanupUnconfirmedError):
            await machine.dispose_failed_start(self.stream('instance-a'), ['claude'], None)


class HubTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, machine_id='machine-1', instance_id='instance-a'):
        socket = types.SimpleNamespace(close=self.record_close)
        return RunnerConnection(socket, machine_id, 'owner-1', instance_id, {})

    async def record_close(self, code, reason=''):
        self.closed_with.append(code)

    async def asyncSetUp(self):
        self.closed_with = []

    async def test_a_newer_connection_replaces_the_older_one(self):
        hub = RunnerHub()
        first = self.connection()
        second = self.connection(instance_id='instance-b')
        self.assertIsNone(hub.attach(first))
        self.assertIs(hub.attach(second), first)
        self.assertIs(hub.connection('machine-1'), second)
        hub.detach(first)
        self.assertIs(hub.connection('machine-1'), second)

    async def test_waiting_wakes_when_the_machine_connects(self):
        hub = RunnerHub()
        waiting = asyncio.create_task(hub.wait_connection('machine-1', 5))
        await asyncio.sleep(0)
        connection = self.connection()
        hub.attach(connection)
        self.assertIs(await waiting, connection)
        self.assertIsNone(await hub.wait_connection('machine-2', 0.05))

    async def test_disconnect_closes_with_the_given_code(self):
        hub = RunnerHub()
        hub.attach(self.connection())
        await hub.disconnect('machine-1', 4401, 'removed')
        self.assertEqual(self.closed_with, [4401])
        self.assertFalse(hub.is_online('machine-1'))

    async def test_a_call_fails_cleanly_when_the_runner_disconnects(self):
        sent = []

        async def send_text(text):
            sent.append(json.loads(text))

        connection = RunnerConnection(
            types.SimpleNamespace(send_text=send_text, close=self.record_close), 'machine-1', 'owner-1', 'a', {},
        )
        calling = asyncio.create_task(connection.call('health'))
        await asyncio.sleep(0)
        connection._shut()
        with self.assertRaisesRegex(SubscriptionError, 'disconnected'):
            await calling
        self.assertEqual(sent[0]['method'], 'health')


if __name__ == '__main__':
    unittest.main()
