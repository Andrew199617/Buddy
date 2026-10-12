"""Remote stream liveness and verified-cleanup admission on isolated loopback hosts.

No native commands, provider accounts, database, or persistent Runner keys are used.
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.utils.subscriptions import codex, machines
from buddy_runner.process import ProcessClosedError
from local.tests.test_subscription_machines import MemoryConfig, load_service


class RemoteLivenessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.key = 'synthetic-liveness-fixture-key'
        self.servers = []
        self.clients = []
        self.gates = []
        self.readers = []
        self.starts = []
        self.stop_requests = []

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        for client in self.clients:
            await client.close()
        for server in self.servers:
            await server.close()
        if self.readers:
            await asyncio.wait_for(asyncio.gather(*self.readers, return_exceptions=True), 2)
        self.temporary.cleanup()

    def gate(self):
        event = asyncio.Event()
        self.gates.append(event)
        return event

    async def initialize_codex(self, socket):
        """Reply to Codex's actual initialization messages without running a CLI."""
        message = await socket.receive_json(timeout=2)
        self.assertEqual(message['type'], 'stdin')
        initialize = json.loads(message['data'])
        self.assertEqual(initialize['method'], 'initialize')
        response = {'id': initialize['id'], 'result': {}}
        await socket.send_json({'type': 'stdout', 'data': json.dumps(response) + '\n'})
        message = await socket.receive_json(timeout=2)
        self.assertEqual(message['type'], 'stdin')
        self.assertEqual(json.loads(message['data'])['method'], 'initialized')

    async def remote(self, exchange, initialize=False, confirms_stop=None):
        """A fixture Runner. With confirms_stop, it also answers cleanup checks by process ID."""
        async def process(request):
            self.assertEqual(request.headers.get('Authorization'), f'Bearer {self.key}')
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            start = await socket.receive_json(timeout=2)
            self.assertEqual(start['type'], 'start')
            self.starts.append(start)
            index = len(self.starts) - 1
            pid = 12345 + index
            started = {'type': 'started', 'pid': pid}
            if confirms_stop is not None:
                started['id'] = f'fixture-process-{index}'
            await socket.send_json(started)
            if initialize:
                await self.initialize_codex(socket)
            await exchange(socket, pid, index)
            return socket

        async def stop(request):
            self.assertEqual(request.headers.get('Authorization'), f'Bearer {self.key}')
            self.stop_requests.append(request.match_info['process_id'])
            if confirms_stop():
                return web.json_response({'stopped': True})
            return web.json_response({'error': 'This Runner has no record of that process.'}, status=404)

        app = web.Application()
        app.router.add_get('/process', process)
        if confirms_stop is not None:
            app.router.add_post('/processes/{process_id}/stop', stop)
        server = TestServer(app)
        await server.start_server()
        self.servers.append(server)
        remote = machines.RemoteMachine(
            'fixture', 'Isolated liveness fixture', str(server.make_url('')), self.key
        )
        self.clients.append(remote)
        return remote

    async def join_receiver(self, process):
        await asyncio.wait_for(process._receiver, 2)
        self.assertTrue(process._exited.is_set())
        self.assertTrue(process._channel.closed)

    def tracked(self, remote):
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        return service, service.TrackingMachine(remote)

    async def test_transport_eof_sets_failed_status_without_confirming_cleanup(self):
        disconnect = self.gate()

        async def exchange(socket, pid, index):
            await disconnect.wait()
            await socket.close()

        remote = await self.remote(exchange)
        process = await remote.start_process(['synthetic-command'], str(self.root))
        self.assertIsNone(process.returncode)
        disconnect.set()
        await self.join_receiver(process)
        self.assertEqual(process.returncode, -1)
        self.assertFalse(process.cleanup_confirmed)
        self.assertFalse(process._confirmed_stopped.is_set())
        self.assertTrue(process.disposal_started)
        self.assertIsNone(await process.read_line())
        with self.assertRaisesRegex(ProcessClosedError, 'confirm'):
            await process.wait(1)
        with self.assertRaisesRegex(ProcessClosedError, 'confirm'):
            await process.close(1)

    async def assert_exit_status(self, code):
        finish = self.gate()

        async def exchange(socket, pid, index):
            await finish.wait()
            await socket.send_json({'type': 'exit', 'code': code, 'stderr': 'fixture stderr'})
            await socket.close()

        remote = await self.remote(exchange)
        process = await remote.start_process(['synthetic-command'], str(self.root))
        finish.set()
        await self.join_receiver(process)
        self.assertEqual(process.returncode, code)
        self.assertEqual(await process.wait(1), code)
        self.assertEqual(process.stderr_text(), 'fixture stderr')
        self.assertTrue(process.cleanup_confirmed)
        await process.close(1)

    async def test_received_success_status_is_preserved(self):
        await self.assert_exit_status(0)

    async def test_received_failure_status_is_preserved(self):
        await self.assert_exit_status(7)

    async def test_received_negative_status_is_preserved(self):
        await self.assert_exit_status(-9)

    async def test_stopped_only_ack_confirms_cleanup_with_unknown_failed_status(self):
        async def exchange(socket, pid, index):
            self.assertEqual((await socket.receive_json(timeout=2))['type'], 'kill')
            await socket.send_json({'type': 'stopped', 'pid': pid})
            async for _ in socket:
                pass

        remote = await self.remote(exchange)
        process = await remote.start_process(['synthetic-command'], str(self.root))
        await process.close(1)
        self.assertTrue(process.cleanup_confirmed)
        self.assertTrue(process._confirmed_stopped.is_set())
        self.assertEqual(process.returncode, -1)
        self.assertEqual(await process.wait(1), -1)

    async def test_stop_on_a_silent_connection_is_confirmed_over_http(self):
        received_kill = asyncio.Event()

        async def exchange(socket, pid, index):
            # The connection went silent: the kill arrives, nothing answers,
            # and the socket stays open as it does before a heartbeat notices.
            self.assertEqual((await socket.receive_json(timeout=2))['type'], 'kill')
            received_kill.set()
            async for _ in socket:
                pass

        remote = await self.remote(exchange, confirms_stop=lambda: True)
        process = await remote.start_process(['synthetic-command'], str(self.root))
        with patch.object(machines, 'STOP_REPLY_WAIT_SECONDS', 0.1):
            await asyncio.wait_for(process.close(5), 2)
        self.assertTrue(received_kill.is_set())
        self.assertEqual(self.stop_requests, ['fixture-process-0'])
        self.assertTrue(process.cleanup_confirmed)

    async def test_a_late_stop_reply_still_confirms_when_http_cannot(self):
        async def exchange(socket, pid, index):
            self.assertEqual((await socket.receive_json(timeout=2))['type'], 'kill')
            # A slow stop answers on the connection after Buddy asked over HTTP.
            await asyncio.sleep(0.3)
            await socket.send_json({'type': 'stopped', 'pid': pid})
            async for _ in socket:
                pass

        remote = await self.remote(exchange, confirms_stop=lambda: False)
        process = await remote.start_process(['synthetic-command'], str(self.root))
        with patch.object(machines, 'STOP_REPLY_WAIT_SECONDS', 0.1):
            await asyncio.wait_for(process.close(5), 2)
        self.assertEqual(self.stop_requests, ['fixture-process-0'])
        self.assertTrue(process.cleanup_confirmed)

    async def test_cleanup_after_the_machine_was_dropped_leaves_no_session_open(self):
        async def exchange(socket, pid, index):
            async for _ in socket:
                pass

        remote = await self.remote(exchange, confirms_stop=lambda: True)
        process = await remote.start_process(['synthetic-command'], str(self.root))
        first_session = remote._session
        # Buddy drops the machine, for example after its key changed, before
        # the process's cleanup was confirmed.
        await remote.close()
        self.assertFalse(process.cleanup_confirmed)

        opened = []
        new_session = remote._new_session

        def record_session():
            session = new_session()
            opened.append(session)
            return session

        with patch.object(remote, '_new_session', record_session):
            with patch.object(machines, 'STOP_REPLY_WAIT_SECONDS', 0.1):
                await asyncio.wait_for(process.close(5), 2)
            with self.assertRaisesRegex(machines.SubscriptionError, 'changed or removed'):
                await remote.start_process(['another-synthetic-command'], str(self.root))
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(self.stop_requests, ['fixture-process-0'])
        self.assertIs(remote._session, first_session)
        self.assertTrue(first_session.closed)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)
        self.assertEqual(len(self.starts), 1)

    async def test_codex_eof_restarts_once_the_runner_confirms_cleanup(self):
        ready = [self.gate(), self.gate()]
        disconnect = self.gate()

        async def exchange(socket, pid, index):
            ready[index].set()
            if index == 0:
                await disconnect.wait()
                await socket.close()
                return
            async for message in socket:
                if json.loads(message.data)['type'] == 'kill':
                    await socket.send_json({'type': 'stopped', 'pid': pid})

        # The Runner stopped the CLI when the connection dropped and says so.
        remote = await self.remote(exchange, initialize=True, confirms_stop=lambda: True)
        _, tracked = self.tracked(remote)
        server = codex.CodexAppServer(tracked, 'synthetic-codex', str(self.root), lambda _: None)
        await asyncio.wait_for(server.ensure_started(), 2)
        await asyncio.wait_for(ready[0].wait(), 2)
        first = server._process
        self.readers.append(server._reader)
        disconnect.set()
        await self.join_receiver(first)
        await asyncio.wait_for(server._reader, 2)
        self.assertFalse(server.running)
        self.assertFalse(first.cleanup_confirmed)

        await asyncio.wait_for(server.ensure_started(), 2)
        await asyncio.wait_for(ready[1].wait(), 2)
        self.readers.append(server._reader)
        self.assertEqual(self.stop_requests, ['fixture-process-0'])
        self.assertTrue(first.cleanup_confirmed)
        self.assertNotIn(first, tracked.processes)
        self.assertIsNone(tracked.cleanup_error)
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(server.generation, 2)
        self.assertTrue(server.running)
        await tracked.revoke()
        self.assertFalse(tracked.processes)

    async def test_codex_eof_debt_blocks_restart_until_the_runner_confirms_cleanup(self):
        ready = self.gate()
        disconnect = self.gate()
        self.runner_confirms_stop = False

        async def exchange(socket, pid, index):
            ready.set()
            await disconnect.wait()
            await socket.close()

        # The Runner cannot confirm cleanup yet, for example while it is out of reach.
        remote = await self.remote(exchange, initialize=True, confirms_stop=lambda: self.runner_confirms_stop)
        service, tracked = self.tracked(remote)
        server = codex.CodexAppServer(tracked, 'synthetic-codex', str(self.root), lambda _: None)
        await asyncio.wait_for(server.ensure_started(), 2)
        await asyncio.wait_for(ready.wait(), 2)
        process = server._process
        self.readers.append(server._reader)
        self.assertTrue(server.running)
        self.assertEqual(server.generation, 1)
        disconnect.set()
        await self.join_receiver(process)
        await asyncio.wait_for(server._reader, 2)
        self.assertFalse(server.running)
        self.assertFalse(process.cleanup_confirmed)
        self.assertEqual(process.returncode, -1)

        with self.assertRaisesRegex(ProcessClosedError, 'confirm'):
            await asyncio.wait_for(server.ensure_started(), 2)
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(server.generation, 1)
        self.assertIs(server._process, process)
        self.assertIn(process, tracked.processes)
        self.assertIsNotNone(tracked.cleanup_error)
        self.assertIsNone(tracked.startup_cleanup_error)
        with self.assertRaisesRegex(service.SubscriptionError, 'confirm'):
            await tracked.start_process(['another-synthetic-command'], str(self.root))
        self.assertEqual(len(self.starts), 1)
        self.assertIn(process, tracked.processes)

        # Retrying cleanup succeeds once the Runner confirms that the CLI stopped.
        self.runner_confirms_stop = True
        await tracked.revoke()
        self.assertTrue(process.cleanup_confirmed)
        self.assertFalse(tracked.processes)
        self.assertIsNone(tracked.cleanup_error)
        self.assertEqual(self.stop_requests, ['fixture-process-0', 'fixture-process-0'])

    async def test_confirmed_codex_exit_allows_a_fresh_tracked_start(self):
        ready = [self.gate(), self.gate()]
        finish = [self.gate(), self.gate()]

        async def exchange(socket, pid, index):
            self.assertLess(index, 2)
            ready[index].set()
            await finish[index].wait()
            await socket.send_json({'type': 'exit', 'code': 0, 'stderr': ''})
            await socket.close()

        remote = await self.remote(exchange, initialize=True)
        _, tracked = self.tracked(remote)
        server = codex.CodexAppServer(tracked, 'synthetic-codex', str(self.root), lambda _: None)
        await asyncio.wait_for(server.ensure_started(), 2)
        await asyncio.wait_for(ready[0].wait(), 2)
        first = server._process
        first_reader = server._reader
        self.readers.append(first_reader)
        finish[0].set()
        await self.join_receiver(first)
        await asyncio.wait_for(first_reader, 2)
        self.assertTrue(first.cleanup_confirmed)
        self.assertEqual(first.returncode, 0)
        self.assertFalse(server.running)

        await asyncio.wait_for(server.ensure_started(), 2)
        await asyncio.wait_for(ready[1].wait(), 2)
        second = server._process
        self.readers.append(server._reader)
        self.assertIsNot(second, first)
        self.assertNotIn(first, tracked.processes)
        self.assertIn(second, tracked.processes)
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(server.generation, 2)
        self.assertTrue(server.running)
        self.assertIsNone(tracked.cleanup_error)
        finish[1].set()
        await self.join_receiver(second)
        await asyncio.wait_for(server._reader, 2)
        await tracked.revoke()
        self.assertFalse(tracked.processes)
        self.assertIsNone(tracked.cleanup_error)


if __name__ == '__main__':
    unittest.main()
