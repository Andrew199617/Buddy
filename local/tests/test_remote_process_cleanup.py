"""Remote cleanup acknowledgements and bounded pipes using isolated fixtures."""

import asyncio
import ctypes
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.utils.subscriptions import machines, runner
from open_webui.utils.subscriptions.process import (
    MAX_CAPTURE_BYTES, MAX_PENDING_CHUNKS, MAX_STDERR_BYTES,
    OutputLimitError, ProcessClosedError,
)
from local.tests.test_subscription_machines import MemoryConfig, load_service


def is_running(pid):
    if sys.platform != 'win32':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, 0, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259
    finally:
        kernel32.CloseHandle(handle)


class RemoteCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.key = 'synthetic-remote-test-key'
        self.servers = []
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        for server in self.servers:
            await server.close()
        self.temporary.cleanup()

    async def remote(self, app):
        server = TestServer(app)
        await server.start_server()
        self.servers.append(server)
        machine = machines.RemoteMachine('fixture', 'Isolated fixture', str(server.make_url('')), self.key)
        self.clients.append(machine)
        return machine

    async def real_remote(self):
        app = runner.build_app(self.key, self.root / 'state')
        return await self.remote(app), app['runner']

    async def fake_remote(self, exchange, heartbeat=None):
        async def process(request):
            self.assertEqual(request.headers.get('Authorization'), f'Bearer {self.key}')
            socket = web.WebSocketResponse(heartbeat=heartbeat)
            await socket.prepare(request)
            start = await socket.receive_json()
            self.assertEqual(start['type'], 'start')
            await socket.send_json({'type': 'started', 'pid': 12345})
            await exchange(socket)
            return socket

        app = web.Application()
        app.router.add_get('/process', process)
        return await self.remote(app)

    async def assert_registry_empty(self, host):
        async with asyncio.timeout(10):
            while host.processes:
                await asyncio.sleep(0.01)

    async def test_active_close_confirms_real_runner_and_descendant_cleanup(self):
        remote, host = await self.real_remote()
        fixture = (
            'import json,subprocess,sys,time; '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print(json.dumps({"child":child.pid}),flush=True); time.sleep(30)'
        )
        process = await remote.start_process([sys.executable, '-c', fixture], str(self.root))
        native = next(iter(host.processes))
        child_pid = json.loads(await asyncio.wait_for(process.read_line(), 10))['child']
        self.assertTrue(is_running(child_pid))
        self.assertFalse(process.cleanup_confirmed)
        self.assertFalse(process.disposal_started)
        self.assertFalse(native.disposal_started)
        await process.close()
        self.assertTrue(process._confirmed_stopped.is_set())
        self.assertTrue(process._receiver.done())
        self.assertTrue(process._websocket.closed)
        self.assertTrue(process.cleanup_confirmed)
        self.assertTrue(process.disposal_started)
        self.assertTrue(native.disposal_started)
        self.assertFalse(is_running(native.pid))
        self.assertFalse(is_running(child_pid))
        self.assertTrue(native._tree_stopped)
        self.assertFalse(native._monitor.is_alive())
        await self.assert_registry_empty(host)
        await process.close()

    async def test_natural_exit_confirms_real_runner_cleanup_and_preserves_pipes(self):
        remote, host = await self.real_remote()
        process = await remote.start_process(
            [sys.executable, '-c', 'import sys; print(sys.stdin.readline().strip()); print("error tail",file=sys.stderr)'],
            str(self.root),
        )
        native = next(iter(host.processes))
        await process.write('fixture input\n')
        process.close_stdin()
        self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'fixture input')
        self.assertEqual(await process.wait(10), 0)
        self.assertEqual(process.stderr_text(), 'error tail')
        await process.close()
        self.assertTrue(process._confirmed_stopped.is_set())
        self.assertFalse(native._monitor.is_alive())
        await self.assert_registry_empty(host)

    async def test_natural_exit_after_leader_spawn_cleans_descendant(self):
        remote, host = await self.real_remote()
        fixture = (
            'import subprocess,sys; '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print(child.pid,flush=True)'
        )
        process = await remote.start_process([sys.executable, '-c', fixture], str(self.root))
        child_pid = int(await asyncio.wait_for(process.read_line(), 10))
        self.assertEqual(await process.wait(10), 0)
        await process.close()
        self.assertFalse(is_running(child_pid))
        await self.assert_registry_empty(host)

    async def test_transport_loss_without_acknowledgement_fails_cleanup_and_wait(self):
        async def exchange(socket):
            await socket.close()

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        with self.assertRaisesRegex(ProcessClosedError, 'confirm'):
            await process.close()
        with self.assertRaisesRegex(ProcessClosedError, 'confirm'):
            await process.wait(1)
        self.assertFalse(process._confirmed_stopped.is_set())
        self.assertFalse(process.cleanup_confirmed)
        self.assertTrue(process.disposal_started)

    async def test_delayed_ack_keeps_socket_open_and_concurrent_close_waits(self):
        received_kill = asyncio.Event()
        allow_ack = asyncio.Event()
        closed_before_ack = []

        async def exchange(socket):
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            received_kill.set()
            await allow_ack.wait()
            closed_before_ack.append(socket.closed)
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            async for _ in socket:
                pass

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        closes = [asyncio.create_task(process.close()) for _ in range(3)]
        await asyncio.wait_for(received_kill.wait(), 1)
        await asyncio.sleep(0.05)
        self.assertTrue(all(not close.done() for close in closes))
        self.assertTrue(process.disposal_started)
        self.assertIsNone(process.returncode)
        self.assertFalse(process._websocket.closed)
        allow_ack.set()
        await asyncio.gather(*closes)
        self.assertEqual(closed_before_ack, [False])
        self.assertTrue(process._receiver.done())
        self.assertTrue(process._websocket.closed)

    async def test_wrong_pid_ack_does_not_confirm_and_timeout_fails(self):
        async def exchange(socket):
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            await socket.send_json({'type': 'stopped', 'pid': 99999})
            async for _ in socket:
                pass

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        with self.assertRaisesRegex(TimeoutError, 'confirm'):
            await process.close(0.1)
        self.assertFalse(process._confirmed_stopped.is_set())
        self.assertTrue(process._websocket.closed)

    async def test_output_byte_limit_stops_remote_and_keeps_queue_bounded(self):
        async def exchange(socket):
            await socket.send_json({'type': 'stdout', 'data': 'x' * (machines.MAX_REMOTE_STDOUT_BYTES + 1)})
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            async for _ in socket:
                pass

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        await asyncio.wait_for(process._exited.wait(), 5)
        with self.assertRaises(OutputLimitError):
            await process.read_text()
        self.assertLessEqual(process._queued_bytes, machines.MAX_REMOTE_STDOUT_BYTES)
        self.assertEqual(process._queued_chunks, 0)
        self.assertEqual(process._chunks.qsize(), 1)
        await process.close()

    async def test_unread_output_is_buffered_while_heartbeats_continue(self):
        line_count = MAX_PENDING_CHUNKS * 3

        async def exchange(socket):
            for number in range(line_count):
                await socket.send_json({'type': 'stdout', 'data': f'{number}\n'})
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            async for _ in socket:
                pass

        # The fixture Runner pings every 0.5 seconds and drops the link when a
        # pong is 0.25 seconds late, like the real one with its 30 seconds.
        remote = await self.fake_remote(exchange, heartbeat=0.5)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        # The reader stalls across several heartbeats while output arrives.
        await asyncio.sleep(2)
        self.assertIsNone(process._output_error)
        self.assertFalse(process._exited.is_set())
        self.assertEqual(process._queued_chunks, line_count)
        lines = []
        for _ in range(line_count):
            lines.append(await asyncio.wait_for(process.read_line(), 1))
        self.assertEqual(lines, [str(number) for number in range(line_count)])
        await asyncio.wait_for(process.close(), 2)
        self.assertTrue(process.cleanup_confirmed)

    async def test_output_chunk_limit_stops_remote_and_confirms_cleanup_while_unread(self):
        chunk_limit = MAX_PENDING_CHUNKS + 72
        received_kill = asyncio.Event()

        async def exchange(socket):
            for _ in range(chunk_limit + 1):
                await socket.send_json({'type': 'stdout', 'data': 'x'})
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            received_kill.set()
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            async for _ in socket:
                pass

        remote = await self.fake_remote(exchange, heartbeat=0.5)
        with patch.object(machines, 'MAX_REMOTE_STDOUT_CHUNKS', chunk_limit):
            process = await remote.start_process(['synthetic-fixture'], str(self.root))
        # Nothing reads stdout, yet the stop reply is still read and confirms cleanup.
        await asyncio.wait_for(received_kill.wait(), 2)
        await asyncio.sleep(2)
        self.assertTrue(process.cleanup_confirmed)
        with self.assertRaises(OutputLimitError):
            await process.read_line()
        self.assertEqual(process._queued_chunks, chunk_limit)
        self.assertEqual(process._chunks.qsize(), chunk_limit + 1)
        await process.close()

    async def test_stdout_consumption_releases_pending_bytes_and_chunks(self):
        allow_exit = asyncio.Event()

        async def exchange(socket):
            await socket.send_json({'type': 'stdout', 'data': 'hello\n'})
            await allow_exit.wait()
            await socket.send_json({'type': 'exit', 'code': 0, 'stderr': 'x' * (MAX_STDERR_BYTES + 100)})
            await socket.close()

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        self.assertEqual(await process.read_text(1), 'hello\n')
        self.assertEqual((process._queued_bytes, process._queued_chunks), (0, 0))
        allow_exit.set()
        self.assertEqual(await process.wait(1), 0)
        self.assertEqual(len(process.stderr_text()), MAX_STDERR_BYTES)
        await process.close()

    async def test_cleanup_cancellation_waits_for_confirmation_before_reraising(self):
        received_kill = asyncio.Event()
        allow_ack = asyncio.Event()

        async def exchange(socket):
            await socket.receive_json()
            received_kill.set()
            await allow_ack.wait()
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            async for _ in socket:
                pass

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        close = asyncio.create_task(process.close())
        await asyncio.wait_for(received_kill.wait(), 1)
        close.cancel()
        await asyncio.sleep(0.05)
        self.assertFalse(close.done())
        close.cancel()
        await asyncio.sleep(0.05)
        self.assertFalse(close.done())
        allow_ack.set()
        with self.assertRaises(asyncio.CancelledError):
            await close
        self.assertTrue(process._confirmed_stopped.is_set())
        self.assertTrue(process._receiver.done())

    async def test_legacy_kill_uses_verified_close(self):
        async def exchange(socket):
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            async for _ in socket:
                pass

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        process.kill()
        await asyncio.wait_for(asyncio.shield(process._close_task), 2)
        self.assertTrue(process._confirmed_stopped.is_set())
        self.assertTrue(process._receiver.done())

    async def test_delayed_start_cancellation_closes_real_runner_process(self):
        remote, host = await self.real_remote()
        started = asyncio.Event()
        release_ack = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        original_send = runner.web.WebSocketResponse.send_json
        original_cleanup = remote._dispose_failed_start

        async def delayed_send(socket, event, *args, **kwargs):
            if event.get('type') == 'started':
                started.set()
                await release_ack.wait()
            return await original_send(socket, event, *args, **kwargs)

        async def delayed_cleanup(*args):
            cleanup_started.set()
            await release_cleanup.wait()
            await original_cleanup(*args)

        with patch.object(runner.web.WebSocketResponse, 'send_json', delayed_send), patch.object(
            remote, '_dispose_failed_start', delayed_cleanup
        ):
            startup = asyncio.create_task(remote.start_process(
                [sys.executable, '-c', 'import time; time.sleep(30)'], str(self.root)
            ))
            await asyncio.wait_for(started.wait(), 10)
            native = next(iter(host.processes))
            startup.cancel()
            with self.assertLogs(machines.log, level='WARNING') as captured:
                await asyncio.wait_for(cleanup_started.wait(), 1)
                self.assertFalse(startup.done())
                startup.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(startup.done())
                release_cleanup.set()
                with self.assertRaises(machines.StartupCleanupUnconfirmedError):
                    await startup
            self.assertIn('cleanup unconfirmed', '\n'.join(captured.output))
            release_ack.set()
            await self.assert_registry_empty(host)
        self.assertFalse(is_running(native.pid))
        self.assertFalse(native._monitor.is_alive())

    async def test_websocket_receive_size_is_bounded(self):
        async def exchange(socket):
            await socket.send_json({'type': 'exit', 'code': 0, 'stderr': ''})
            await socket.close()

        remote = await self.fake_remote(exchange)
        client = remote._client()
        original_connect = client.ws_connect
        with patch.object(client, 'ws_connect', wraps=original_connect) as connect:
            process = await remote.start_process(['synthetic-fixture'], str(self.root))
        self.assertEqual(connect.call_args.kwargs['max_msg_size'], 32 * 1024 * 1024)
        await process.wait(1)
        await process.close()

    async def test_failed_start_with_known_pid_requires_stop_acknowledgement(self):
        acknowledged = asyncio.Event()

        async def process(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.receive_json()
            await socket.send_json({'type': 'error', 'pid': 12345, 'message': 'synthetic startup failure'})
            self.assertEqual((await socket.receive_json())['type'], 'kill')
            await socket.send_json({'type': 'stopped', 'pid': 12345})
            acknowledged.set()
            async for _ in socket:
                pass
            return socket

        app = web.Application()
        app.router.add_get('/process', process)
        remote = await self.remote(app)
        with self.assertRaisesRegex(machines.SubscriptionError, 'synthetic startup failure'):
            await remote.start_process(['synthetic-fixture'], str(self.root))
        self.assertTrue(acknowledged.is_set())

    async def test_known_startup_pid_without_cleanup_ack_raises_typed_debt(self):
        async def process(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.receive_json()
            await socket.send_json({'type': 'error', 'pid': 12345, 'message': 'synthetic startup failure'})
            await socket.close()
            return socket

        app = web.Application()
        app.router.add_get('/process', process)
        remote = await self.remote(app)
        with self.assertRaisesRegex(machines.StartupCleanupUnconfirmedError, 'confirm cleanup'):
            await remote.start_process(['synthetic-fixture'], str(self.root))

    async def test_preconnect_failure_remains_ordinary_subscription_error(self):
        remote = machines.RemoteMachine('fixture', 'Isolated fixture', 'http://127.0.0.1:1', self.key)
        self.clients.append(remote)
        with patch.object(remote._client(), 'ws_connect', side_effect=aiohttp.ClientError('synthetic unavailable')):
            with self.assertRaises(machines.SubscriptionError) as captured:
                await remote.start_process(['synthetic-fixture'], str(self.root))
        self.assertNotIsInstance(captured.exception, machines.StartupCleanupUnconfirmedError)

    async def test_start_serialization_failure_is_ordinary_and_opens_no_socket(self):
        connections = []

        async def process(request):
            connections.append(request)
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            async for _ in socket:
                pass
            return socket

        app = web.Application()
        app.router.add_get('/process', process)
        remote = await self.remote(app)
        with self.assertRaises(machines.SubscriptionError) as captured:
            await remote.start_process(['synthetic-fixture'], str(self.root), {'fixture': object()})
        self.assertNotIsInstance(captured.exception, machines.StartupCleanupUnconfirmedError)
        self.assertEqual(connections, [])

    async def test_runner_start_refusals_are_ordinary_errors(self):
        remote, host = await self.real_remote()
        with self.assertRaises(machines.SubscriptionError) as captured:
            await remote.start_process([sys.executable, '-c', 'print(1)'], str(self.root / 'missing-folder'))
        self.assertNotIsInstance(captured.exception, machines.StartupCleanupUnconfirmedError)
        self.assertIn('Could not start', str(captured.exception))
        self.assertFalse(host.processes)

        original_prepare = runner.web.WebSocketResponse.prepare

        async def shutdown_after_handshake(socket, request):
            # Shutdown can begin after the handshake's own closing check.
            result = await original_prepare(socket, request)
            host.closing = True
            return result

        try:
            with patch.object(runner.web.WebSocketResponse, 'prepare', shutdown_after_handshake):
                with self.assertRaises(machines.SubscriptionError) as captured:
                    await remote.start_process([sys.executable, '-c', 'print(1)'], str(self.root))
        finally:
            host.closing = False
        self.assertNotIsInstance(captured.exception, machines.StartupCleanupUnconfirmedError)
        self.assertIn('process limit', str(captured.exception))
        self.assertFalse(host.processes)

    async def test_tracking_start_cancellation_waits_for_host_cleanup_before_releasing_operation(self):
        remote, host = await self.real_remote()
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        tracked = service.TrackingMachine(remote)
        started = asyncio.Event()
        release_ack = asyncio.Event()
        original_send = runner.web.WebSocketResponse.send_json

        async def delayed_send(socket, event, *args, **kwargs):
            if event.get('type') == 'started':
                started.set()
                await release_ack.wait()
            return await original_send(socket, event, *args, **kwargs)

        with patch.object(runner.web.WebSocketResponse, 'send_json', delayed_send):
            startup = asyncio.create_task(tracked.start_process(
                [sys.executable, '-c', 'import time; time.sleep(30)'], str(self.root)
            ))
            try:
                await asyncio.wait_for(started.wait(), 10)
                native = next(iter(host.processes))
                startup.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(startup.done())
                startup.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(startup.done())
                self.assertTrue(is_running(native.pid))
                self.assertTrue(tracked.startups)
            finally:
                release_ack.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(startup, 10)
        # Cancellation cannot finish before the actual host verifies cleanup.
        self.assertFalse(is_running(native.pid))
        self.assertTrue(native._tree_stopped)
        self.assertFalse(native._monitor.is_alive())
        self.assertFalse(tracked.processes)
        self.assertFalse(tracked.startups)
        await self.assert_registry_empty(host)
        second = await tracked.start_process(
            [sys.executable, '-c', 'print("next fixture",flush=True)'], str(self.root)
        )
        self.assertEqual(await asyncio.wait_for(second.read_line(), 10), 'next fixture')
        await second.wait(10)
        await tracked.revoke()
        await self.assert_registry_empty(host)

    async def test_runner_start_refusal_does_not_poison_actual_tracking_wrapper(self):
        remote, host = await self.real_remote()
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        tracked = service.TrackingMachine(remote)
        missing = self.root / 'renamed-project'
        with self.assertRaises(service.SubscriptionError) as captured:
            await tracked.start_process([sys.executable, '-c', 'print("fixture")'], str(missing))
        self.assertNotIsInstance(captured.exception, machines.StartupCleanupUnconfirmedError)
        self.assertIsNone(tracked.startup_cleanup_error)
        self.assertIsNone(tracked.cleanup_error)
        self.assertFalse(tracked.processes)
        # Restoring the folder is enough; the provider needs no backend restart.
        missing.mkdir()
        process = await tracked.start_process([sys.executable, '-c', 'print("fixture",flush=True)'], str(missing))
        self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'fixture')
        await process.wait(10)
        await tracked.revoke()
        await self.assert_registry_empty(host)

    async def test_connected_start_without_a_reply_poison_is_retained_by_actual_tracking_wrapper(self):
        async def process(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.receive_json()
            # No startup reply: the host may or may not have started a process.
            await socket.close()
            return socket

        app = web.Application()
        app.router.add_get('/process', process)
        remote = await self.remote(app)
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        tracked = service.TrackingMachine(remote)
        with self.assertLogs(machines.log, level='WARNING'):
            with self.assertRaises(machines.StartupCleanupUnconfirmedError):
                await tracked.start_process(['synthetic-fixture'], str(self.root))
        self.assertIsNotNone(tracked.startup_cleanup_error)
        with self.assertRaises(service.SubscriptionError):
            await tracked.start_process(['synthetic-fixture'], str(self.root))
        with self.assertRaises(service.SubscriptionError):
            await tracked.revoke()
        self.assertIsNotNone(tracked.startup_cleanup_error)

    async def test_runner_shutdown_acknowledges_verified_cleanup_before_closing(self):
        remote, host = await self.real_remote()
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        tracked = service.TrackingMachine(remote)
        fixture = 'import time; print("ready",flush=True); time.sleep(30)'
        processes = []
        for _ in range(3):
            process = await tracked.start_process([sys.executable, '-c', fixture], str(self.root))
            self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'ready')
            processes.append(process)
        natives = list(host.processes)

        async def held_finish_output(timeout):
            # Keep each forwarder from reporting exit before the sockets close.
            await asyncio.Event().wait()

        with ExitStack() as patches:
            for native in natives:
                patches.enter_context(patch.object(native, 'finish_output', held_finish_output))
            # A Runner restart, for example to load a renewed certificate.
            await host.close(self.servers[-1].app)
        for process in processes:
            await asyncio.wait_for(process._receiver, 5)
            self.assertTrue(process._confirmed_stopped.is_set())
            self.assertTrue(process.cleanup_confirmed)
        self.assertFalse(any(is_running(native.pid) for native in natives))
        # The next start finds no cleanup debt; only the closing Runner refuses it.
        with self.assertRaises(service.SubscriptionError):
            await tracked.start_process([sys.executable, '-c', 'print(1)'], str(self.root))
        self.assertIsNone(tracked.cleanup_error)
        self.assertIsNone(tracked.startup_cleanup_error)
        self.assertFalse(tracked.processes)

    async def test_lost_connection_cleanup_is_confirmed_by_the_runner_before_the_next_start(self):
        remote, host = await self.real_remote()
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        tracked = service.TrackingMachine(remote)
        fixture = 'import time; print("ready",flush=True); time.sleep(30)'
        process = await tracked.start_process([sys.executable, '-c', fixture], str(self.root))
        self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'ready')
        native = next(iter(host.processes))
        self.assertTrue(process.process_id)
        # The connection drops: Buddy never sends kill and never sees an acknowledgement.
        await process._websocket.close()
        await asyncio.wait_for(process._receiver, 5)
        self.assertFalse(process.cleanup_confirmed)
        self.assertEqual(process.returncode, -1)

        # Disposing the old process asks the Runner, so the next start needs no restart.
        second = await tracked.start_process([sys.executable, '-c', 'print("next",flush=True)'], str(self.root))
        self.assertTrue(process.cleanup_confirmed)
        self.assertNotIn(process, tracked.processes)
        self.assertIsNone(tracked.cleanup_error)
        self.assertFalse(is_running(native.pid))
        self.assertEqual(await asyncio.wait_for(second.read_line(), 10), 'next')
        await second.wait(10)
        await tracked.revoke()
        await self.assert_registry_empty(host)

    async def test_runner_output_limit_reports_failed_exit_after_verified_cleanup_and_next_command_works(self):
        remote, host = await self.real_remote()
        service = load_service(MemoryConfig(), self.root / 'unused-offline-state')
        service.StartupCleanupUnconfirmedError = machines.StartupCleanupUnconfirmedError
        tracked = service.TrackingMachine(remote)
        children = []

        def bounded_process(args, cwd, extra_env, temp_files, temp_dir):
            child = machines.ChildProcess(
                args, cwd, machines.subscription_env(extra_env), output_limit_bytes=8
            )
            children.append(child)
            return child

        with patch.object(runner, 'start_local_process', side_effect=bounded_process):
            code, _, stderr = await tracked.run(
                [sys.executable, '-c', 'print("synthetic overflowing output",flush=True)'],
                str(self.root), 10,
            )
        self.assertNotEqual(code, 0)
        self.assertIn('OutputLimitError', stderr)
        self.assertIn('buffer limit', stderr)
        self.assertTrue(children[0].cleanup_confirmed)
        self.assertIsNone(tracked.cleanup_error)
        self.assertIsNone(tracked.startup_cleanup_error)
        self.assertFalse(tracked.processes)
        await self.assert_registry_empty(host)
        code, stdout, stderr = await tracked.run(
            [sys.executable, '-c', 'print("next fixture",flush=True)'], str(self.root), 10,
        )
        self.assertEqual((code, stdout.strip(), stderr), (0, 'next fixture', ''))
        await tracked.revoke()

    async def test_runner_output_failure_does_not_ack_when_native_cleanup_fails(self):
        class UnverifiedProcess:
            pid = 12345
            returncode = 0
            fail_cleanup = True

            async def read_text(self):
                raise OutputLimitError('synthetic output limit')

            async def close(self):
                if self.fail_cleanup:
                    raise OSError('synthetic native verification failure')

            def stderr_text(self):
                return ''

        remote, _ = await self.real_remote()
        native = UnverifiedProcess()
        try:
            with patch.object(runner, 'start_local_process', return_value=native):
                process = await remote.start_process(['synthetic-fixture'], str(self.root))
                with self.assertRaisesRegex(ProcessClosedError, 'confirm'):
                    await process.close(2)
            self.assertFalse(process._confirmed_stopped.is_set())
            self.assertFalse(process.cleanup_confirmed)
        finally:
            native.fail_cleanup = False

    async def test_stop_preempts_blocked_runner_stdin_write(self):
        remote, host = await self.real_remote()
        process = await remote.start_process(
            [sys.executable, '-c', 'import time; print("ready",flush=True); time.sleep(30)'],
            str(self.root),
        )
        native = next(iter(host.processes))
        self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'ready')
        entered = threading.Event()
        original_write = native._write_blocking

        def observed_write(text):
            entered.set()
            original_write(text)

        try:
            with patch.object(native, '_write_blocking', side_effect=observed_write):
                await asyncio.wait_for(process.write('x' * (2 * MAX_CAPTURE_BYTES)), 5)
                async with asyncio.timeout(5):
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                await process.close(2)
            self.assertFalse(is_running(native.pid))
            self.assertTrue(native.cleanup_confirmed)
            await self.assert_registry_empty(host)
        finally:
            # This out-of-band owner guarantees fixture cleanup on the unfixed
            # server, whose handler is blocked before it can read the kill.
            await native.close()
            await self.assert_registry_empty(host)

    async def test_disconnect_preempts_blocked_runner_stdin_write(self):
        remote, host = await self.real_remote()
        process = await remote.start_process(
            [sys.executable, '-c', 'import time; print("ready",flush=True); time.sleep(30)'], str(self.root)
        )
        native = next(iter(host.processes))
        self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'ready')
        entered = threading.Event()
        original_write = native._write_blocking

        def observed_write(text):
            entered.set()
            original_write(text)

        try:
            with patch.object(native, '_write_blocking', side_effect=observed_write):
                await asyncio.wait_for(process.write('x' * (2 * MAX_CAPTURE_BYTES)), 5)
                async with asyncio.timeout(5):
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                await process._websocket.close()
            await self.assert_registry_empty(host)
            self.assertTrue(native.cleanup_confirmed)
            self.assertFalse(host.stdin_workers)
        finally:
            await native.close()
            await self.assert_registry_empty(host)

    async def test_runner_stdin_preserves_order_before_close(self):
        remote, host = await self.real_remote()
        process = await remote.start_process(
            [sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()),flush=True)'], str(self.root)
        )
        for text in ('first\n', 'caf\u00e9\n', 'third\n'):
            await process.write(text)
        process.close_stdin()
        self.assertEqual(await asyncio.wait_for(process.read_line(), 10), repr('first\ncaf\u00e9\nthird\n'))
        self.assertEqual(await process.wait(10), 0)
        await process.close()
        await self.assert_registry_empty(host)
        self.assertFalse(host.stdin_workers)

    async def test_stdin_larger_than_one_runner_message_arrives_unchanged(self):
        remote, host = await self.real_remote()
        fixture = (
            'import hashlib,sys; data=sys.stdin.buffer.read(); '
            'print(len(data), hashlib.sha256(data).hexdigest(), flush=True)'
        )
        # Escaped JSON for this text is several times the Runner's patched limit.
        text = ('café \U0001f600 line\n' * 20000) + 'end'
        encoded = text.encode('utf-8')
        with patch.object(runner, 'MAX_WEBSOCKET_MESSAGE_BYTES', 64 * 1024), patch.object(
            machines, 'MAX_STDIN_MESSAGE_CHARS', 4096
        ):
            process = await remote.start_process([sys.executable, '-c', fixture], str(self.root))
            await asyncio.wait_for(process.write(text), 10)
            process.close_stdin()
            reported = await asyncio.wait_for(process.read_line(), 10)
        self.assertEqual(reported, f'{len(encoded)} {hashlib.sha256(encoded).hexdigest()}')
        self.assertEqual(await process.wait(10), 0)
        await process.close()
        self.assertTrue(process.cleanup_confirmed)
        await self.assert_registry_empty(host)

    async def test_concurrent_stdin_writes_and_close_keep_each_write_whole(self):
        received = []
        finished = asyncio.Event()

        async def exchange(socket):
            async for message in socket:
                command = json.loads(message.data)
                if command['type'] == 'close_stdin':
                    break
                received.append(command['data'])
            finished.set()
            await socket.send_json({'type': 'exit', 'code': 0, 'stderr': ''})
            await socket.close()

        remote = await self.fake_remote(exchange)
        process = await remote.start_process(['synthetic-fixture'], str(self.root))
        first, second = 'a' * 50, 'b' * 50
        with patch.object(machines, 'MAX_STDIN_MESSAGE_CHARS', 8):
            writes = [asyncio.create_task(process.write(first)), asyncio.create_task(process.write(second))]
            process.close_stdin()
            await asyncio.gather(*writes)
            await asyncio.wait_for(finished.wait(), 2)
        self.assertTrue(all(len(piece) <= 8 for piece in received))
        self.assertIn(''.join(received), (first + second, second + first))
        self.assertEqual(await process.wait(2), 0)
        await process.close()

    async def test_runner_pending_stdin_limits_report_failed_ack_after_cleanup(self):
        original_report = runner.Runner._report_process_failure

        async def report_after_output_finishes(host, child, socket, error):
            await child.close()
            await asyncio.sleep(0.05)
            await original_report(host, child, socket, error)

        for byte_limit, item_limit, extra_frames in ((8192, 128, ['x']), (32 * 1024 * 1024, 2, ['x', 'y'])):
            with self.subTest(byte_limit=byte_limit, item_limit=item_limit):
                remote, host = await self.real_remote()
                process = await remote.start_process(
                    [sys.executable, '-c', 'import time; print("ready",flush=True); time.sleep(30)'],
                    str(self.root),
                )
                native = next(iter(host.processes))
                self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'ready')
                if sys.platform != 'win32':
                    # The first frame must stay in flight so the extra frames
                    # trip the pending limit. A POSIX pipe holds 64 KiB by
                    # default (the Windows default is 4 KiB), so shrink it
                    # below the frame size or the write drains instantly.
                    import fcntl

                    fcntl.fcntl(native.process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
                entered = threading.Event()
                original_write = native._write_blocking

                def observed_write(text):
                    entered.set()
                    original_write(text)

                try:
                    with patch.object(native, '_write_blocking', side_effect=observed_write), patch.object(
                        runner, 'MAX_PENDING_STDIN_BYTES', byte_limit
                    ), patch.object(runner, 'MAX_PENDING_STDIN_ITEMS', item_limit), patch.object(
                        runner.Runner, '_report_process_failure', report_after_output_finishes
                    ):
                        await process.write('x' * 8192)
                        async with asyncio.timeout(5):
                            while not entered.is_set():
                                await asyncio.sleep(0.01)
                        for text in extra_frames:
                            await process.write(text)
                        self.assertNotEqual(await process.wait(10), 0)
                        self.assertIn('Runner stdin buffer limit', process.stderr_text())
                        await process.close()
                    await self.assert_registry_empty(host)
                    self.assertTrue(native.cleanup_confirmed)
                    self.assertFalse(host.stdin_workers)
                finally:
                    await native.close()
                    await self.assert_registry_empty(host)


if __name__ == '__main__':
    unittest.main()
