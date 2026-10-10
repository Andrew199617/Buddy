"""Isolated Runner HTTP/process boundaries; no accounts or saved credentials."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.utils.subscriptions import runner


class RunnerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'fixture.txt').write_text('fixture', encoding='utf-8')
        self.origin = 'http://127.0.0.1:39991'
        self.key = 'isolated-provider-execution-key'
        self.app = runner.build_app(
            self.key, self.root / 'state',
            roots=[{'path': str(self.project), 'write': True, 'execute': True}],
            origins=[self.origin],
        )
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.temporary.cleanup()

    async def request(self, method, path, headers=None, body=None):
        async with self.client.request(
            method, self.server.make_url(path), headers=headers, json=body
        ) as response:
            return response.status, await response.json()

    async def pair(self):
        status, body = await self.request(
            'POST', '/v1/pair', {'Origin': self.origin},
            {'code': self.app['workspace_capabilities'].pairing_code},
        )
        self.assertEqual(status, 200, body)
        return {'Origin': self.origin, 'Authorization': f"Bearer {body['token']}"}

    async def test_credentials_do_not_cross_api_boundaries(self):
        provider = {'Authorization': f'Bearer {self.key}'}
        status, info = await self.request('GET', '/health', provider)
        self.assertEqual(status, 200)
        status, discovery = await self.request('GET', '/v1/capabilities', {'Origin': self.origin})
        self.assertEqual(status, 200)
        self.assertEqual(discovery['host'], info['host'])
        self.assertNotIn(self.key, json.dumps(discovery))
        status, _ = await self.request('GET', '/v1/grants', {**provider, 'Origin': self.origin})
        self.assertEqual(status, 401)
        paired = await self.pair()
        status, _ = await self.request('GET', '/health', {'Authorization': paired['Authorization']})
        self.assertEqual(status, 401)
        status, _ = await self.request('GET', '/health', {**provider, 'Origin': self.origin})
        self.assertEqual(status, 403)
        status, _ = await self.request('GET', '/workspace-grants', paired)
        self.assertEqual(status, 403)

    async def test_operator_revocation_invalidates_browser_grant(self):
        paired = await self.pair()
        status, grants = await self.request('GET', '/v1/grants', paired)
        self.assertEqual(status, 200)
        grant_id = grants['grants'][0]['id']
        provider = {'Authorization': f'Bearer {self.key}'}
        status, operator = await self.request('GET', '/workspace-grants', provider)
        self.assertEqual(status, 200)
        self.assertEqual(operator, grants)
        status, result = await self.request('DELETE', f'/workspace-grants/{grant_id}', provider)
        self.assertEqual((status, result), (200, {'revoked': True}))
        status, _ = await self.request(
            'GET', f'/v1/files?grantId={grant_id}&path=fixture.txt', paired
        )
        self.assertEqual(status, 403)

    async def test_operator_can_pair_another_device_without_stopping_provider(self):
        paired = await self.pair()
        provider = {'Authorization': f'Bearer {self.key}'}
        socket = await self.client.ws_connect(self.server.make_url('/process'), headers=provider)
        await socket.send_json({'type': 'start', 'args': [sys.executable, '-c', 'import time; time.sleep(30)']})
        self.assertEqual((await socket.receive_json(timeout=10))['type'], 'started')
        process = list(self.app['runner'].processes)[0]
        status, refreshed = await self.request('POST', '/workspace-pairing', provider)
        self.assertEqual(status, 200)
        self.assertIsNone(process.returncode)
        status, _ = await self.request('GET', '/v1/grants', paired)
        self.assertEqual(status, 200)
        status, _ = await self.request('POST', '/workspace-pairing', paired)
        self.assertEqual(status, 403)
        status, second = await self.request(
            'POST', '/v1/pair', {'Origin': self.origin}, {'code': refreshed['code']}
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(second['token'], paired['Authorization'][7:])
        self.assertEqual(second['host'], refreshed['host'])
        self.assertIsNone(process.returncode)
        await socket.close()

    async def test_short_command_timeouts_are_finite_and_bounded(self):
        for timeout in ('NaN', 'Infinity', -1, 121, []):
            status, _ = await self.request(
                'POST', '/run', {'Authorization': f'Bearer {self.key}'},
                {'args': [sys.executable, '-c', 'raise AssertionError()'], 'timeout': timeout},
            )
            self.assertEqual(status, 400, repr(timeout))

    async def test_startup_acknowledgement_disconnect_disposes_process(self):
        class FakeProcess:
            pid = 123
            closed = False

            async def close(self):
                self.closed = True

        class DisconnectedSocket:
            closed = False

            async def prepare(self, request):
                pass

            async def receive_json(self, timeout):
                return {'type': 'start', 'args': ['synthetic-fixture']}

            async def send_json(self, message):
                raise ConnectionResetError('synthetic startup disconnect')

            async def close(self):
                self.closed = True

        process = FakeProcess()
        socket = DisconnectedSocket()
        host = self.app['runner']
        with patch.object(runner.web, 'WebSocketResponse', return_value=socket), patch.object(
            runner, 'start_local_process', return_value=process
        ):
            await host.process(object())
        self.assertTrue(process.closed)
        self.assertTrue(socket.closed)
        self.assertEqual(host.processes, set())
        self.assertEqual(host.websockets, set())

    async def test_active_websocket_disconnect_closes_registered_process(self):
        socket = await self.client.ws_connect(
            self.server.make_url('/process'), headers={'Authorization': f'Bearer {self.key}'}
        )
        await socket.send_json({'type': 'start', 'args': [sys.executable, '-c', 'import time; time.sleep(30)']})
        started = await socket.receive_json(timeout=10)
        self.assertEqual(started['type'], 'started')
        host = self.app['runner']
        processes = list(host.processes)
        self.assertEqual(len(processes), 1)
        await socket.close()
        try:
            async with asyncio.timeout(10):
                while host.processes:
                    await asyncio.sleep(0.02)
        except TimeoutError:
            process = processes[0]
            self.fail(
                f'Disconnect left a registered process: returncode={process.returncode}, '
                f'tree_stopped={process._tree_stopped}, monitor_alive={process._monitor.is_alive()}'
            )
        self.assertIsNotNone(processes[0].returncode)
        await processes[0].close()

    async def test_kill_acknowledges_only_after_native_cleanup(self):
        socket = await self.client.ws_connect(
            self.server.make_url('/process'), headers={'Authorization': f'Bearer {self.key}'}
        )
        await socket.send_json({'type': 'start', 'args': [sys.executable, '-c', 'import time; time.sleep(30)']})
        started = await socket.receive_json(timeout=10)
        self.assertEqual(started['type'], 'started')
        process = list(self.app['runner'].processes)[0]
        await socket.send_json({'type': 'kill'})
        acknowledgement = await socket.receive_json(timeout=10)
        self.assertIn(acknowledgement['type'], ('stopped', 'exit'))
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process._tree_stopped)
        await socket.close()

    async def test_exit_code_is_observed_after_native_cleanup(self):
        class EarlyStdoutClose:
            pid = 123
            returncode = None

            async def read_text(self):
                return None

            async def finish_output(self, timeout):
                pass

            async def close(self):
                self.returncode = 7

            def stderr_text(self):
                return 'synthetic stderr'

        class RecordingSocket:
            def __init__(self):
                self.closed = False
                self.completed = asyncio.Event()
                self.messages = []

            async def prepare(self, request):
                pass

            async def receive_json(self, timeout):
                return {'type': 'start', 'args': ['synthetic-fixture']}

            async def send_json(self, message):
                self.messages.append(message)

            async def close(self):
                self.closed = True
                self.completed.set()

            def __aiter__(self):
                return self

            async def __anext__(self):
                await self.completed.wait()
                raise StopAsyncIteration

        socket = RecordingSocket()
        with patch.object(runner.web, 'WebSocketResponse', return_value=socket), patch.object(
            runner, 'start_local_process', return_value=EarlyStdoutClose()
        ):
            await self.app['runner'].process(object())
        exit_message = next(message for message in socket.messages if message['type'] == 'exit')
        self.assertEqual(exit_message['code'], 7)
        self.assertEqual(exit_message['stderr'], 'synthetic stderr')

    async def test_shutdown_closes_active_process_and_rejects_more_commands(self):
        socket = await self.client.ws_connect(
            self.server.make_url('/process'), headers={'Authorization': f'Bearer {self.key}'}
        )
        await socket.send_json({'type': 'start', 'args': [sys.executable, '-c', 'import time; time.sleep(30)']})
        self.assertEqual((await socket.receive_json(timeout=10))['type'], 'started')
        host = self.app['runner']
        processes = list(host.processes)
        await host.close(self.app)
        self.assertIsNotNone(processes[0].returncode)
        status, _ = await self.request(
            'POST', '/run', {'Authorization': f'Bearer {self.key}'},
            {'args': [sys.executable, '-c', 'raise AssertionError()']},
        )
        self.assertEqual(status, 503)
        await socket.close()

    async def test_shutdown_failure_still_disposes_other_owned_work(self):
        class BrokenProcess:
            calls = 0
            closed = False

            async def close(self):
                self.calls += 1
                if self.calls == 1:
                    raise OSError('synthetic native cleanup failure')
                self.closed = True

        class GoodProcess:
            closed = False

            async def close(self):
                self.closed = True

        class GoodSocket:
            closed = False

            async def close(self, code):
                self.closed = True

        host = self.app['runner']
        initially_broken = BrokenProcess()
        process = GoodProcess()
        socket = GoodSocket()
        command = asyncio.create_task(asyncio.sleep(30))
        host.processes.update((process, initially_broken))
        host.websockets.add(socket)
        host.command_requests.add(command)
        try:
            with self.assertRaisesRegex(OSError, 'synthetic native cleanup failure'):
                await host.close(self.app)
            self.assertTrue(process.closed)
            self.assertTrue(socket.closed)
            self.assertTrue(command.cancelled())
            self.assertTrue(host.closing)
            self.assertTrue(self.app['workspace_capabilities']._closing)
            await host.close(self.app)
            self.assertTrue(initially_broken.closed)
        finally:
            host.processes.clear()
            host.websockets.clear()
            host.command_requests.clear()
            host._shutdown_task = None


if __name__ == '__main__':
    unittest.main()
