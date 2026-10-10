"""Loopback-only Runner workspace tests; all grants and commands use temp fixtures.

Run: python -m unittest discover -s local/tests -p test_runner_capabilities.py -v
"""

import asyncio
import io
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from open_webui.utils.subscriptions.workspace_capabilities import (  # noqa: E402
    CapabilityError,
    FILE_BYTES,
    OUTPUT_BYTES,
    WorkspaceCapabilities,
    _Session,
    _digest,
)
from open_webui.utils.subscriptions.pinned_paths import PinnedPath, PinnedPathError  # noqa: E402
from open_webui.utils.subscriptions.process import ProcessClosedError  # noqa: E402

ORIGIN = 'http://localhost:8082'
SECOND_ORIGIN = 'http://127.0.0.1:8082'


class RunnerCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='buddy-capabilities-test-')
        self.base = Path(self.temporary.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'nested').mkdir()
        (self.project / 'hello.txt').write_text('hello π', encoding='utf-8')
        (self.project / 'nested' / 'inside.txt').write_text('nested', encoding='utf-8')
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        (self.outside / 'private.txt').write_text('outside secret fixture', encoding='utf-8')
        self.clients = []
        self.hosts = []
        self.token = None
        self.host = None
        self.client = None

    async def asyncTearDown(self):
        for client in reversed(self.clients):
            await client.close()
        for host in self.hosts:
            await host.close()
        self.temporary.cleanup()

    async def start(self, roots=None, **options):
        if roots is None:
            roots = [{'path': str(self.project), 'write': True}]
        host = WorkspaceCapabilities(roots=roots, origins=[ORIGIN, SECOND_ORIGIN], **options)
        app = web.Application()
        host.register_routes(app)
        client = TestClient(TestServer(app, host='127.0.0.1'))
        await client.start_server()
        self.clients.append(client)
        self.hosts.append(host)
        self.host = host
        self.client = client
        return host, client

    async def request(self, method, path, *, token=True, origin=ORIGIN, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        if origin is not None:
            headers['Origin'] = origin
        credential = self.token if token is True else token
        if credential:
            headers['Authorization'] = f'Bearer {credential}'
        response = await self.client.request(method, path, headers=headers, **kwargs)
        body = await response.json()
        return response.status, body, response

    async def pair(self):
        status, body, _ = await self.request('POST', '/v1/pair', token=False, json={'code': self.host.pairing_code})
        self.assertEqual(status, 200, body)
        self.token = body['token']
        return body

    async def grant(self):
        status, body, _ = await self.request('GET', '/v1/grants')
        self.assertEqual(status, 200, body)
        return body['grants'][0]

    async def workspace(self, path=''):
        grant = await self.grant()
        status, body, _ = await self.request('POST', '/v1/workspaces', json={'grantId': grant['id'], 'path': path})
        self.assertEqual(status, 200, body)
        return body['workspace']

    async def terminal(self):
        workspace = await self.workspace()
        status, body, _ = await self.request('POST', '/v1/terminals', json={'workspaceId': workspace['id']})
        self.assertEqual(status, 200, body)
        return body['terminal']

    async def command(self, terminal, source):
        status, body, _ = await self.request(
            'POST', f'/v1/terminals/{terminal["id"]}/commands',
            json={'executable': sys.executable, 'args': ['-u', '-c', source]},
        )
        self.assertEqual(status, 200, body)
        return body['terminal']

    async def wait_terminal(self, terminal, condition=None, timeout=8):
        if condition is None:
            condition = lambda current: current['status'] in ('exited', 'closed')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, body, _ = await self.request('GET', f'/v1/terminals/{terminal["id"]}')
            self.assertEqual(status, 200, body)
            if condition(body['terminal']):
                return body['terminal']
            await asyncio.sleep(0.02)
        self.fail(f'Terminal did not reach expected state: {body}')

    async def test_discovery_is_minimal_and_identity_matches_pair_and_workspace(self):
        await self.start()
        status, info, response = await self.request('GET', '/v1/host', token=False, origin=None)
        self.assertEqual(status, 200)
        self.assertEqual(set(info['host']), {'id', 'name', 'platform', 'version'})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        status, capabilities, _ = await self.request('GET', '/v1/capabilities', token=False)
        self.assertEqual(status, 200)
        self.assertEqual(capabilities, {
            'protocol': 'buddy-workspaces', 'version': 1, 'host': info['host'],
            'capabilities': {'directories': True, 'files': True, 'workspaces': True, 'terminals': True, 'pty': False},
            'permissions': {'requiresPairing': True, 'grants': 'host-approved', 'commands': 'opt-in-os-account'},
        })
        pairing = await self.pair()
        self.assertEqual(pairing['host'], info['host'])
        self.assertGreater(pairing['expiresAt'], time.time() * 1000)
        workspace = await self.workspace()
        self.assertEqual(workspace['hostId'], info['host']['id'])

    async def test_restart_changes_identity_and_rejects_old_credentials(self):
        await self.start()
        pairing = await self.pair()
        original_id = self.host.host['id']
        old_token = self.token
        await self.start()
        self.assertNotEqual(self.host.host['id'], original_id)
        status, _, _ = await self.request('GET', '/v1/grants', token=old_token)
        self.assertEqual(status, 401)
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': pairing['token']})
        self.assertEqual(status, 403)

    async def test_zero_roots_and_paths_do_not_create_grants(self):
        await self.start(roots=[])
        await self.pair()
        status, body, _ = await self.request('GET', '/v1/grants')
        self.assertEqual((status, body), (200, {'grants': []}))
        for grant_id in (str(self.project), 'local', 'provider-workspace'):
            status, _, _ = await self.request('POST', '/v1/workspaces', json={'grantId': grant_id, 'path': ''})
            self.assertEqual(status, 403)

    async def test_missing_random_and_privileged_runner_keys_are_rejected(self):
        await self.start()
        for credential in (False, 'runner-key', secrets.token_urlsafe(32)):
            status, _, _ = await self.request('GET', '/v1/grants', token=credential)
            self.assertEqual(status, 401)

    async def test_real_runner_separates_provider_key_pair_token_and_operator_controls(self):
        from open_webui.utils.subscriptions import runner

        provider_key = secrets.token_urlsafe(32)
        app = runner.build_app(
            provider_key, self.base / 'runner-state',
            roots=[{'path': str(self.project)}], origins=[ORIGIN],
        )
        self.host = app['workspace_capabilities']
        self.client = TestClient(TestServer(app, host='127.0.0.1'))
        await self.client.start_server()
        self.clients.append(self.client)
        self.hosts.append(self.host)
        status, _, _ = await self.request('GET', '/v1/grants', token=provider_key)
        self.assertEqual(status, 401)
        await self.pair()
        original_browser_token = self.token
        status, _, _ = await self.request('POST', '/workspace-pairing', origin=None)
        self.assertEqual(status, 401)
        status, _, _ = await self.request('POST', '/workspace-pairing', token=provider_key)
        self.assertEqual(status, 403)
        status, refreshed, _ = await self.request('POST', '/workspace-pairing', origin=None, token=provider_key)
        self.assertEqual(status, 200)
        self.assertEqual(refreshed['host'], self.host.host)
        status, body, _ = await self.request('POST', '/v1/pair', token=False, json={'code': refreshed['code']})
        self.assertEqual(status, 200)
        self.assertNotEqual(body['token'], original_browser_token)
        status, _, _ = await self.request('GET', '/v1/grants', token=original_browser_token)
        self.assertEqual(status, 200)
        status, _, _ = await self.request('GET', '/health', origin=None)
        self.assertEqual(status, 401)
        status, _, _ = await self.request('GET', '/workspace-grants', origin=None)
        self.assertEqual(status, 401)
        for origin in (ORIGIN, 'https://evil.example', 'null'):
            status, _, _ = await self.request('GET', '/health', origin=origin, token=provider_key)
            self.assertEqual(status, 403)
        status, body, _ = await self.request('GET', '/health', origin=None, token=provider_key)
        self.assertEqual(status, 200)
        self.assertEqual(body['host'], self.host.host)
        workspace = await self.workspace()
        status, body, _ = await self.request('GET', '/workspace-grants', origin=None, token=provider_key)
        self.assertEqual(status, 200)
        self.assertEqual(body['grants'][0]['id'], workspace['grantId'])
        status, body, _ = await self.request('DELETE', f'/workspace-grants/{workspace["grantId"]}', origin=None, token=provider_key)
        self.assertEqual((status, body), (200, {'revoked': True}))
        status, body, _ = await self.request('GET', '/v1/grants')
        self.assertEqual((status, body), (200, {'grants': []}))

    async def test_pairing_wrong_expired_replayed_and_attempt_bound(self):
        await self.start()
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': 'wrong'})
        self.assertEqual(status, 403)
        await self.pair()
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': self.host.pairing_code})
        self.assertEqual(status, 403)
        await self.start(pairing_ttl_ms=20)
        await asyncio.sleep(0.04)
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': self.host.pairing_code})
        self.assertEqual(status, 403)
        await self.start()
        for _ in range(8):
            status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': 'wrong'})
            self.assertEqual(status, 403)
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': self.host.pairing_code})
        self.assertEqual(status, 403)

    async def test_simultaneous_pairing_consumes_code_once(self):
        await self.start()
        responses = await asyncio.gather(*(
            self.request('POST', '/v1/pair', token=False, json={'code': self.host.pairing_code})
            for _ in range(4)
        ))
        self.assertEqual(sorted(status for status, _, _ in responses), [200, 403, 403, 403])

    async def test_host_pairing_refresh_invalidates_old_code_preserves_sessions_and_grants(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        original_code = self.host.pairing_code
        refreshed = self.host.issue_pairing_code()
        self.assertEqual(refreshed['host'], self.host.host)
        self.assertNotEqual(refreshed['code'], original_code)
        self.assertGreater(refreshed['expiresAt'], time.time() * 1000)
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': original_code})
        self.assertEqual(status, 403)
        await self.pair()
        original_token = self.token
        terminal = await self.terminal()
        await self.command(terminal, 'import time; print("still running", flush=True); time.sleep(30)')
        original_child = self.host._terminals[terminal['id']].child
        original_grants = self.host.list_grants()
        self.host.issue_pairing_code()
        self.assertEqual(self.host.list_grants(), original_grants)
        self.assertIs(self.host._terminals[terminal['id']].child, original_child)
        self.assertIsNone(original_child.returncode)
        await self.pair()
        self.assertNotEqual(self.token, original_token)
        status, body, _ = await self.request('GET', '/v1/workspaces', token=original_token)
        self.assertEqual(status, 200)
        self.assertEqual(body['workspaces'][0]['id'], terminal['workspaceId'])
        status, body, _ = await self.request('GET', f'/v1/terminals/{terminal["id"]}', token=original_token)
        self.assertEqual(status, 200)
        self.assertEqual(body['terminal']['status'], 'running')
        status, _, _ = await self.request('GET', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 404)

    async def test_refreshed_pairing_respects_session_limit_and_disconnect_frees_capacity(self):
        from unittest.mock import patch

        await self.start()
        with patch('open_webui.utils.subscriptions.workspace_capabilities.MAX_SESSIONS', 2):
            await self.pair()
            first_token = self.token
            self.host.issue_pairing_code()
            await self.pair()
            second_token = self.token
            self.host.issue_pairing_code()
            status, _, _ = await self.request('POST', '/v1/pair', token=False, json={'code': self.host.pairing_code})
            self.assertEqual(status, 429)
            status, _, _ = await self.request('DELETE', '/v1/session', token=first_token)
            self.assertEqual(status, 200)
            await self.pair()
            status, _, _ = await self.request('GET', '/v1/grants', token=second_token)
            self.assertEqual(status, 200)

    async def test_origins_reject_missing_null_foreign_and_lookalike(self):
        await self.start()
        for origin in (None, 'null', 'https://evil.example', ORIGIN + '.evil.example', ORIGIN + '/'):
            status, _, response = await self.request('POST', '/v1/pair', token=False, origin=origin, json={'code': self.host.pairing_code})
            self.assertEqual(status, 403, origin)
            self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        status, _, _ = await self.request('GET', '/v1/capabilities', token=False, origin=None)
        self.assertEqual(status, 403)
        await self.pair()
        status, _, _ = await self.request('GET', '/v1/grants', origin=SECOND_ORIGIN)
        self.assertEqual(status, 403)

    async def test_explicit_no_origin_clients_remain_bound_to_no_origin(self):
        await self.start(allow_no_origin=True)
        status, body, _ = await self.request('POST', '/v1/pair', token=False, origin=None, json={'code': self.host.pairing_code})
        self.assertEqual(status, 200)
        self.token = body['token']
        status, _, _ = await self.request('GET', '/v1/grants', origin=None)
        self.assertEqual(status, 200)
        status, _, _ = await self.request('GET', '/v1/grants', origin=ORIGIN)
        self.assertEqual(status, 403)

    async def test_host_header_rebinding_and_wrong_ports_are_rejected(self):
        await self.start()
        for host in ('evil.example', 'localhost:1', 'localhost.evil:8083', '127.0.0.1:1', 'localhost'):
            status, _, _ = await self.request('GET', '/v1/host', token=False, headers={'Host': host})
            self.assertEqual(status, 403, host)

    async def test_cors_allows_exact_origin_and_limited_methods_headers(self):
        await self.start()
        status, body, response = await self.request('OPTIONS', '/v1/files', token=False, headers={
            'Access-Control-Request-Method': 'PUT',
            'Access-Control-Request-Headers': 'authorization,content-type',
        })
        self.assertEqual((status, body), (200, {'ok': True}))
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], ORIGIN)
        self.assertNotIn('Access-Control-Allow-Credentials', response.headers)
        status, _, _ = await self.request('OPTIONS', '/v1/files', token=False, headers={'Access-Control-Request-Method': 'PATCH'})
        self.assertEqual(status, 403)
        status, _, _ = await self.request('OPTIONS', '/v1/files', token=False, headers={'Access-Control-Request-Headers': 'cookie'})
        self.assertEqual(status, 403)

    async def test_nested_directories_files_and_workspace_deduplication(self):
        await self.start()
        await self.pair()
        grant = await self.grant()
        status, body, _ = await self.request('GET', '/v1/directories', params={'grantId': grant['id'], 'path': ''})
        self.assertEqual(status, 200)
        self.assertEqual([entry['name'] for entry in body['entries']], ['nested', 'hello.txt'])
        status, body, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': 'nested/inside.txt'})
        self.assertEqual((status, body), (200, {'path': 'nested/inside.txt', 'content': 'nested'}))
        first = await self.workspace('nested')
        second = await self.workspace('nested')
        self.assertEqual(first, second)
        self.assertEqual(first['path'], str(self.project / 'nested'))

    async def test_traversal_windows_drives_unc_and_ambiguous_paths_are_denied(self):
        await self.start()
        await self.pair()
        grant = await self.grant()
        paths = ('..', '../outside/private.txt', 'nested/../../outside', '/etc/passwd',
                 'C:/Windows', 'C:\\Windows', '\\\\server\\share', '//server/share',
                 'nested\\../hello.txt', 'nested/./inside.txt', 'nested//inside.txt',
                 'hello.txt:stream', 'hello.txt.', 'hello.txt ', 'NUL', 'COM1.txt', 'a\0b')
        for path in paths:
            with self.subTest(path=path):
                status, _, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': path})
                self.assertEqual(status, 403)

    async def test_actual_directory_symlink_or_junction_escape_is_denied(self):
        link = self.project / 'escape'
        if sys.platform == 'win32':
            # Junction creation uses the platform's native command only inside temp fixtures.
            import subprocess
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.outside)], check=True, capture_output=True)
        else:
            link.symlink_to(self.outside, target_is_directory=True)
        try:
            await self.start()
            await self.pair()
            grant = await self.grant()
            status, body, _ = await self.request('GET', '/v1/directories', params={'grantId': grant['id'], 'path': ''})
            self.assertEqual(status, 200)
            self.assertNotIn('escape', [entry['name'] for entry in body['entries']])
            status, _, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': 'escape/private.txt'})
            self.assertEqual(status, 403)
            with self.assertRaises(Exception):
                WorkspaceCapabilities(roots=[{'path': str(link)}])
        finally:
            if sys.platform == 'win32':
                os.rmdir(link)
            else:
                link.unlink()

    async def test_hard_link_outside_grant_cannot_be_read_or_written(self):
        os.link(self.outside / 'private.txt', self.project / 'hardlink.txt')
        await self.start()
        await self.pair()
        grant = await self.grant()
        status, _, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': 'hardlink.txt'})
        self.assertEqual(status, 403)
        status, _, _ = await self.request('PUT', '/v1/files', json={'grantId': grant['id'], 'path': 'hardlink.txt', 'content': 'changed'})
        self.assertEqual(status, 403)
        self.assertEqual((self.outside / 'private.txt').read_text(encoding='utf-8'), 'outside secret fixture')

    async def test_root_replacement_is_denied(self):
        await self.start()
        await self.pair()
        grant = await self.grant()
        self.project.rename(self.base / 'old-project')
        self.project.mkdir()
        (self.project / 'hello.txt').write_text('replacement', encoding='utf-8')
        status, _, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': 'hello.txt'})
        self.assertEqual(status, 403)
        status, _, _ = await self.request('GET', '/v1/grants')
        self.assertEqual(status, 403)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native handle sharing fixture')
    async def test_windows_ancestor_swap_is_blocked_during_read_and_write(self):
        from unittest.mock import patch

        await self.start()
        await self.pair()
        grant = await self.grant()
        nested = self.project / 'nested'
        original_open = PinnedPath._windows_open
        blocked = []

        def attempt_swap(guard, absolute, **options):
            handle = original_open(guard, absolute, **options)
            if absolute == nested and options['directory']:
                # This is the exact interval before the next descendant open.
                # Renaming any held ancestor would permit a junction redirect.
                for ancestor in (nested, self.project, self.base):
                    with self.assertRaises(PermissionError):
                        ancestor.rename(ancestor.with_name(ancestor.name + '-swapped'))
                    blocked.append(ancestor)
                # A new writable directory handle must also be denied, because
                # reparse metadata can be changed without renaming its entry.
                with self.assertRaises(PermissionError):
                    original_open(guard, nested, directory=True, writable=True)
            return handle

        with patch.object(PinnedPath, '_windows_open', attempt_swap):
            status, body, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': 'nested/inside.txt'})
            self.assertEqual((status, body['content']), (200, 'nested'))
            status, body, _ = await self.request('PUT', '/v1/files', json={'grantId': grant['id'], 'path': 'nested/inside.txt', 'content': 'inside edited'})
            self.assertEqual((status, body['content']), (200, 'inside edited'))
        self.assertEqual(len(blocked), 6)
        self.assertEqual((self.outside / 'private.txt').read_text(encoding='utf-8'), 'outside secret fixture')
        # Guard disposal releases all ancestor ownership, including after PUT.
        nested.rename(self.project / 'renamed-nested')

    @unittest.skipIf(sys.platform == 'win32', 'POSIX descriptor traversal fixture')
    async def test_posix_ancestor_swap_keeps_file_and_listing_on_pinned_directory(self):
        from unittest.mock import patch
        from open_webui.utils.subscriptions import pinned_paths

        nested = self.project / 'nested'
        saved = self.project / 'original-nested'
        original_open = pinned_paths._open_at
        swapped = False

        def swap_after_open(component, flags, parent=None):
            nonlocal swapped
            descriptor = original_open(component, flags, parent)
            if component == 'nested' and not swapped:
                nested.rename(saved)
                nested.symlink_to(self.outside, target_is_directory=True)
                swapped = True
            return descriptor

        try:
            with patch.object(pinned_paths, '_open_at', swap_after_open):
                with PinnedPath(self.project, 'nested/inside.txt', file=True, writable=True) as guard:
                    self.assertEqual(os.read(guard.descriptor, 64), b'nested')
                    os.lseek(guard.descriptor, 0, os.SEEK_SET)
                    os.write(guard.descriptor, b'inside')
                    self.assertFalse(os.get_inheritable(guard.descriptor))
            self.assertEqual((saved / 'inside.txt').read_text(encoding='utf-8'), 'inside')
            self.assertEqual((self.outside / 'private.txt').read_text(encoding='utf-8'), 'outside secret fixture')
            nested.unlink()
            saved.rename(nested)
            swapped = False
            with patch.object(pinned_paths, '_open_at', swap_after_open):
                with PinnedPath(self.project, 'nested') as guard, guard.scandir() as listing:
                    self.assertEqual([entry.name for entry in listing], ['inside.txt'])
        finally:
            if nested.is_symlink():
                nested.unlink()
            if saved.exists():
                saved.rename(nested)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX nonblocking FIFO fixture')
    async def test_posix_leaf_fifo_swap_is_rejected_without_blocking(self):
        import signal
        from unittest.mock import patch
        from open_webui.utils.subscriptions import pinned_paths

        leaf = self.project / 'nested' / 'inside.txt'
        original_open = pinned_paths._open_at
        swapped = False

        def swap_before_leaf(component, flags, parent=None):
            nonlocal swapped
            if component == 'inside.txt' and not swapped:
                leaf.unlink()
                os.mkfifo(leaf)
                swapped = True
            return original_open(component, flags, parent)

        def deadline_expired(signum, frame):
            raise TimeoutError('FIFO opening blocked despite the required nonblocking flag.')

        previous_handler = signal.signal(signal.SIGALRM, deadline_expired)
        try:
            signal.setitimer(signal.ITIMER_REAL, 1)
            started = time.monotonic()
            with patch.object(pinned_paths, '_open_at', swap_before_leaf), self.assertRaises(PinnedPathError):
                PinnedPath(self.project, 'nested/inside.txt', file=True)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            leaf.unlink()
            leaf.write_text('nested', encoding='utf-8')

    async def test_cancelled_reader_keeps_descriptor_owned_until_worker_finishes(self):
        from unittest.mock import patch

        await self.start()
        entered = threading.Event()
        release = threading.Event()
        original_read = self.host._read_text
        guard = PinnedPath(self.project, 'hello.txt', file=True)
        task = None

        def blocked_read(descriptor):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Reader fixture was not released.')
            return original_read(descriptor)

        async def own_guard():
            try:
                return await self.host._read_in_thread(guard)
            finally:
                guard.close()

        try:
            with patch.object(self.host, '_read_text', blocked_read):
                task = asyncio.create_task(own_guard())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                self.assertFalse(guard._closed)
                self.assertGreater(os.fstat(guard.descriptor).st_size, 0)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(guard._closed)
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            guard.close()

    async def test_read_only_grants_deny_writes_and_execution(self):
        await self.start(roots=[{'path': str(self.project)}])
        await self.pair()
        grant = await self.grant()
        self.assertEqual(grant['capabilities'], {'read': True, 'write': False, 'execute': False})
        status, _, _ = await self.request('PUT', '/v1/files', json={'grantId': grant['id'], 'path': 'hello.txt', 'content': 'changed'})
        self.assertEqual(status, 403)
        workspace = await self.workspace()
        status, _, _ = await self.request('POST', '/v1/terminals', json={'workspaceId': workspace['id']})
        self.assertEqual(status, 403)

    async def test_text_edit_limits_binary_rejection_and_no_creation(self):
        await self.start()
        await self.pair()
        grant = await self.grant()
        status, body, _ = await self.request('PUT', '/v1/files', json={'grantId': grant['id'], 'path': 'hello.txt', 'content': 'short'})
        self.assertEqual((status, body), (200, {'path': 'hello.txt', 'content': 'short'}))
        self.assertEqual((self.project / 'hello.txt').read_text(encoding='utf-8'), 'short')
        status, _, _ = await self.request('PUT', '/v1/files', json={'grantId': grant['id'], 'path': 'new.txt', 'content': 'new'})
        self.assertEqual(status, 404)
        self.assertFalse((self.project / 'new.txt').exists())
        for name, data, expected in (('binary.txt', b'a\0b', 415), ('invalid.txt', b'\xff', 415), ('large.txt', b'a' * (FILE_BYTES + 1), 413)):
            (self.project / name).write_bytes(data)
            status, _, _ = await self.request('GET', '/v1/files', params={'grantId': grant['id'], 'path': name})
            self.assertEqual(status, expected)
        status, _, _ = await self.request('PUT', '/v1/files', json={'grantId': grant['id'], 'path': 'hello.txt', 'content': 'a' * (FILE_BYTES + 1)})
        self.assertEqual(status, 413)

    async def test_request_bodies_are_objects_and_bounded(self):
        await self.start()
        status, _, _ = await self.request('POST', '/v1/pair', token=False, json=[])
        self.assertEqual(status, 400)
        status, _, _ = await self.request('POST', '/v1/pair', token=False, data='not json', headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        status, _, _ = await self.request('POST', '/v1/pair', token=False, data=io.BytesIO(b'a' * (2 * FILE_BYTES + 1)), headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 413)

    async def test_directory_workspace_and_terminal_history_counts_are_bounded(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        grant = await self.grant()
        for index in range(65):
            (self.project / f'workspace-{index}').mkdir()
        for index in range(64):
            status, _, _ = await self.request('POST', '/v1/workspaces', json={'grantId': grant['id'], 'path': f'workspace-{index}'})
            self.assertEqual(status, 200)
        status, _, _ = await self.request('POST', '/v1/workspaces', json={'grantId': grant['id'], 'path': 'workspace-64'})
        self.assertEqual(status, 429)
        # Existing owned workspaces remain usable at the limit.
        status, body, _ = await self.request('POST', '/v1/workspaces', json={'grantId': grant['id'], 'path': 'workspace-0'})
        self.assertEqual(status, 200)
        workspace_id = body['workspace']['id']
        for _ in range(64):
            status, body, _ = await self.request('POST', '/v1/terminals', json={'workspaceId': workspace_id})
            self.assertEqual(status, 200)
            status, _, _ = await self.request('DELETE', f'/v1/terminals/{body["terminal"]["id"]}')
            self.assertEqual(status, 200)
        status, _, _ = await self.request('POST', '/v1/terminals', json={'workspaceId': workspace_id})
        self.assertEqual(status, 429)
        # Lower only the configured test fixture's entry threshold, preserving
        # the same streaming enumeration and rejection path as the 5,000 limit.
        from unittest.mock import patch
        with patch('open_webui.utils.subscriptions.workspace_capabilities.MAX_DIRECTORY_ENTRIES', 2):
            status, _, _ = await self.request('GET', '/v1/directories', params={'grantId': grant['id'], 'path': ''})
            self.assertEqual(status, 413)

    async def test_expiry_and_disconnect_revoke_owned_workspaces(self):
        await self.start(token_ttl_ms=70)
        await self.pair()
        await self.workspace()
        await asyncio.sleep(0.15)
        status, _, _ = await self.request('GET', '/v1/grants')
        self.assertEqual(status, 401)
        self.assertEqual(self.host._sessions, {})
        self.assertEqual(self.host._workspaces, {})
        await self.start()
        await self.pair()
        await self.workspace()
        status, body, _ = await self.request('DELETE', '/v1/session')
        self.assertEqual((status, body), (200, {'revoked': True}))
        status, _, _ = await self.request('GET', '/v1/workspaces')
        self.assertEqual(status, 401)
        self.assertEqual(self.host._workspaces, {})

    async def test_host_grant_revocation_removes_workspaces(self):
        await self.start()
        await self.pair()
        workspace = await self.workspace()
        self.assertTrue(await self.host.revoke_grant(workspace['grantId']))
        self.assertFalse(await self.host.revoke_grant(workspace['grantId']))
        status, body, _ = await self.request('GET', '/v1/workspaces')
        self.assertEqual((status, body), (200, {'workspaces': []}))
        status, _, _ = await self.request('GET', '/v1/directories', params={'grantId': workspace['grantId'], 'path': ''})
        self.assertEqual(status, 403)

    async def test_workspace_and_terminal_ids_are_session_owned(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        workspace_id = terminal['workspaceId']
        # A second synthetic in-memory session exercises ownership without enabling
        # another pairing endpoint or creating any persistent credential.
        foreign_token = secrets.token_urlsafe(32)
        foreign = _Session(_digest(foreign_token), ORIGIN, time.time() * 1000 + 10000)
        self.host._sessions[foreign.key] = foreign
        status, body, _ = await self.request('GET', '/v1/workspaces', token=foreign_token)
        self.assertEqual((status, body), (200, {'workspaces': []}))
        status, _, _ = await self.request('POST', '/v1/terminals', token=foreign_token, json={'workspaceId': workspace_id})
        self.assertEqual(status, 404)
        status, _, _ = await self.request('GET', f'/v1/terminals/{terminal["id"]}', token=foreign_token)
        self.assertEqual(status, 404)

    async def test_command_is_argv_only_and_uses_selected_workspace(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        await self.command(terminal, 'import os,sys; print(os.getcwd()); print("stderr fixture", file=sys.stderr)')
        finished = await self.wait_terminal(terminal)
        self.assertEqual(finished['status'], 'exited')
        self.assertEqual(finished['exitCode'], 0)
        self.assertIn(str(self.project), finished['output'])
        self.assertIn('stderr fixture', finished['output'])

    async def test_command_syntax_busy_terminal_and_replaced_workspace(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        for body in ({'executable': '', 'args': []}, {'executable': 'x', 'args': 'a'}, {'executable': 'x', 'args': [None]}, {'executable': 'x\0', 'args': []}):
            status, _, _ = await self.request('POST', f'/v1/terminals/{terminal["id"]}/commands', json=body)
            self.assertEqual(status, 400)
        await self.command(terminal, 'import time; time.sleep(30)')
        status, _, _ = await self.request('POST', f'/v1/terminals/{terminal["id"]}/commands', json={'executable': sys.executable, 'args': ['-c', 'print(1)']})
        self.assertEqual(status, 409)
        status, body, _ = await self.request('DELETE', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['terminal']['status'], 'closed')
        workspace = await self.workspace('nested')
        status, body, _ = await self.request('POST', '/v1/terminals', json={'workspaceId': workspace['id']})
        self.assertEqual(status, 200)
        replacement_terminal = body['terminal']
        (self.project / 'nested').rename(self.project / 'old-nested')
        (self.project / 'nested').mkdir()
        status, _, _ = await self.request('POST', f'/v1/terminals/{replacement_terminal["id"]}/commands', json={'executable': sys.executable, 'args': ['-c', 'print(1)']})
        self.assertEqual(status, 403)

    async def test_timeout_idle_limit_and_active_session_count(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}], command_timeout_ms=100, session_idle_ms=5000)
        await self.pair()
        terminal = await self.terminal()
        await self.command(terminal, 'import time; time.sleep(30)')
        finished = await self.wait_terminal(terminal)
        self.assertEqual(finished['status'], 'closed')
        self.assertIn('time limit', finished['output'])
        for _ in range(8):
            await self.terminal()
        workspace = await self.workspace()
        status, _, _ = await self.request('POST', '/v1/terminals', json={'workspaceId': workspace['id']})
        self.assertEqual(status, 429)
        await self.start(roots=[{'path': str(self.project), 'execute': True}], session_idle_ms=30)
        await self.pair()
        terminal = await self.terminal()
        await asyncio.sleep(0.08)
        status, body, _ = await self.request('GET', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 200)
        self.assertEqual(body['terminal']['status'], 'closed')

    async def test_output_is_bounded_and_no_newline_stderr_does_not_hang(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        await self.command(terminal, 'import sys; sys.stderr.write("x" * 400000); sys.stderr.flush()')
        finished = await self.wait_terminal(terminal)
        self.assertLessEqual(len(finished['output'].encode('utf-8')), OUTPUT_BYTES)
        self.assertIn(finished['status'], ('exited', 'closed'))

    async def test_cleanup_failure_is_reported_and_never_claims_closed(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        record = self.host._terminals[terminal['id']]

        class CannotConfirmCleanup:
            async def close(self):
                raise TimeoutError('synthetic cleanup failure')

        record.child = CannotConfirmCleanup()
        record.status = 'running'
        status, _, _ = await self.request('DELETE', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 503)
        self.assertEqual(record.status, 'running')
        record.child = None
        record.cleanup_error = None

    async def test_cleanup_error_retains_handles_and_a_confirmed_retry_closes(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        record = self.host._terminals[terminal['id']]

        class RetryCleanup:
            attempts = 0

            async def close(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise ProcessClosedError('synthetic unconfirmed native disposal')

        child = RetryCleanup()
        record.child = child
        record.status = 'running'
        status, _, _ = await self.request('DELETE', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 503)
        self.assertTrue(record.stopping)
        self.assertIs(record.child, child)
        status, body, _ = await self.request('DELETE', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 200, body)
        self.assertEqual(record.status, 'closed')
        self.assertIsNone(record.child)
        self.assertIsNone(record.cleanup_error)

    async def test_stop_final_confirmation_supersedes_a_late_finish_error(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        record = self.host._terminals[terminal['id']]
        first_closed = asyncio.Event()

        class ConfirmCleanup:
            async def close(self):
                first_closed.set()

        async def delayed_finish():
            await first_closed.wait()
            record.cleanup_error = 'synthetic stale finish failure'

        record.child = ConfirmCleanup()
        record.status = 'running'
        record.task = asyncio.create_task(delayed_finish())
        status, body, _ = await self.request('DELETE', f'/v1/terminals/{terminal["id"]}')
        self.assertEqual(status, 200, body)
        self.assertEqual(record.status, 'closed')
        self.assertIsNone(record.cleanup_error)

    async def test_concurrent_delete_rejects_new_command_after_native_child_finishes(self):
        from unittest.mock import patch

        await self.start(roots=[{'path': str(self.project), 'execute': True}])
        await self.pair()
        terminal = await self.terminal()
        await self.command(terminal, 'import time; time.sleep(30)')
        record = self.host._terminals[terminal['id']]
        child = record.child
        original_close = child.close
        native_closed = asyncio.Event()
        release_delete = asyncio.Event()
        calls = 0

        async def delay_first_close(*args, **kwargs):
            nonlocal calls
            calls += 1
            first = calls == 1
            await original_close(*args, **kwargs)
            if first:
                native_closed.set()
                await release_delete.wait()

        try:
            with patch.object(child, 'close', delay_first_close):
                deleting = asyncio.create_task(self.request('DELETE', f'/v1/terminals/{terminal["id"]}'))
                await asyncio.wait_for(native_closed.wait(), 5)
                await asyncio.wait_for(asyncio.shield(record.task), 5)
                self.assertIsNone(record.child)
                self.assertTrue(record.stopping)
                status, _, _ = await self.request('POST', f'/v1/terminals/{terminal["id"]}/commands', json={'executable': sys.executable, 'args': ['-c', 'print("must not spawn")']})
                self.assertEqual(status, 410)
                release_delete.set()
                status, body, _ = await asyncio.wait_for(deleting, 5)
                self.assertEqual(status, 200, body)
                self.assertEqual(body['terminal']['status'], 'closed')
                self.assertIsNotNone(child.returncode)
                self.assertIsNone(record.child)
        finally:
            release_delete.set()

    async def test_sweeper_retries_failed_cleanup_and_continues_other_expired_sessions(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}], session_idle_ms=20)
        await self.pair()
        first_terminal = await self.terminal()
        first_key = _digest(self.token)
        first_record = self.host._terminals[first_terminal['id']]

        class FailsTwice:
            attempts = 0

            async def close(self):
                self.attempts += 1
                if self.attempts <= 2:
                    raise TimeoutError('synthetic cleanup retry fixture')

        child = FailsTwice()
        first_record.child = child
        first_record.status = 'running'
        first_record.last_used = time.time() * 1000 + 100000
        self.host.issue_pairing_code()
        await self.pair()
        second_workspace = await self.workspace()
        second_key = _digest(self.token)
        # Expire both only after setup so this verifies disposal, not network timing.
        self.host._sessions[first_key].expires_at = 0
        self.host._sessions[second_key].expires_at = 0
        with self.assertLogs('open_webui.utils.subscriptions.workspace_capabilities', level='ERROR'):
            deadline = time.monotonic() + 2
            while (self.host._sessions or self.host._pending_sessions or self.host._terminals) and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
        self.assertGreaterEqual(child.attempts, 3)
        self.assertFalse(self.host._sweeper.done())
        self.assertEqual(self.host._sessions, {})
        self.assertEqual(self.host._pending_sessions, {})
        self.assertNotIn(second_workspace['id'], self.host._workspaces)
        self.assertEqual(first_record.status, 'closed')

    async def test_failed_grant_revocation_remains_denied_and_is_retried(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}], session_idle_ms=20)
        await self.pair()
        terminal = await self.terminal()
        record = self.host._terminals[terminal['id']]
        workspace = self.host._workspaces[terminal['workspaceId']]

        class FailsOnce:
            attempts = 0

            async def close(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError('synthetic revocation cleanup failure')

        record.child = FailsOnce()
        record.status = 'running'
        with self.assertRaises(CapabilityError) as failed:
            await self.host.revoke_grant(workspace.grant_id)
        self.assertEqual(failed.exception.status, 503)
        self.assertNotIn(workspace.grant_id, self.host._grants)
        self.assertIn(workspace.grant_id, self.host._pending_grants)
        status, _, _ = await self.request('GET', '/v1/directories', params={'grantId': workspace.grant_id, 'path': ''})
        self.assertEqual(status, 403)
        deadline = time.monotonic() + 2
        while self.host._pending_grants and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertEqual(self.host._pending_grants, set())
        self.assertNotIn(workspace.id, self.host._workspaces)
        self.assertEqual(record.status, 'closed')

    async def test_slow_expired_session_does_not_block_other_session_cleanup(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}], session_idle_ms=20)
        await self.pair()
        terminal = await self.terminal()
        first_token = self.token
        first_key = _digest(first_token)
        record = self.host._terminals[terminal['id']]
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowCleanup:
            async def close(self):
                entered.set()
                await release.wait()

        record.child = SlowCleanup()
        record.status = 'running'
        record.last_used = time.time() * 1000 + 100000
        self.host.issue_pairing_code()
        await self.pair()
        second_workspace = await self.workspace()
        self.host._sessions[first_key].expires_at = 0
        self.host._sessions[_digest(self.token)].expires_at = 0
        try:
            await asyncio.wait_for(entered.wait(), 2)
            deadline = time.monotonic() + 1
            while second_workspace['id'] in self.host._workspaces and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            self.assertNotIn(second_workspace['id'], self.host._workspaces)
            self.assertIn(first_key, self.host._pending_sessions)
            status, _, _ = await self.request('GET', '/v1/grants', token=first_token)
            self.assertEqual(status, 401)
            status, _, _ = await self.request('GET', '/v1/host', token=False)
            self.assertEqual(status, 200)
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while self.host._pending_sessions and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertEqual(self.host._pending_sessions, {})

    async def test_cancelled_revocation_preserves_denial_and_cleanup_retry_ownership(self):
        await self.start(roots=[{'path': str(self.project), 'execute': True}], session_idle_ms=20)
        await self.pair()
        terminal = await self.terminal()
        session = self.host._sessions[_digest(self.token)]
        record = self.host._terminals[terminal['id']]
        entered = asyncio.Event()
        release = asyncio.Event()

        class GatedCleanup:
            async def close(self):
                entered.set()
                await release.wait()

        record.child = GatedCleanup()
        record.status = 'running'
        record.last_used = time.time() * 1000 + 100000
        revoking = asyncio.create_task(self.host._revoke_session(session))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            revoking.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await revoking
            self.assertNotIn(session.key, self.host._sessions)
            self.assertIn(session.key, self.host._pending_sessions)
            self.assertTrue(record.stopping)
            status, _, _ = await self.request('GET', '/v1/grants')
            self.assertEqual(status, 401)
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while self.host._pending_sessions and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertEqual(self.host._pending_sessions, {})
        self.assertEqual(record.status, 'closed')

    async def test_active_commands_stop_on_session_revoke_grant_revoke_and_shutdown(self):
        for action in ('session', 'grant', 'shutdown', 'expiry'):
            with self.subTest(action=action):
                ttl = 450 if action == 'expiry' else 30000
                await self.start(roots=[{'path': str(self.project), 'execute': True}], token_ttl_ms=ttl)
                await self.pair()
                terminal = await self.terminal()
                await self.command(terminal, 'import time; print("ready", flush=True); time.sleep(30)')
                record = self.host._terminals[terminal['id']]
                child = record.child
                if action == 'session':
                    status, _, _ = await self.request('DELETE', '/v1/session')
                    self.assertEqual(status, 200)
                elif action == 'grant':
                    workspace = self.host._workspaces[terminal['workspaceId']]
                    self.assertTrue(await self.host.revoke_grant(workspace.grant_id))
                elif action == 'shutdown':
                    await self.host.close()
                else:
                    await asyncio.sleep(0.7)
                    self.assertEqual(self.host._sessions, {})
                self.assertIsNotNone(child.returncode)
                self.assertEqual(record.status, 'closed')


class RunnerCapabilityConfigurationTests(unittest.TestCase):
    def test_unsafe_origins_invalid_durations_relative_and_volume_roots_rejected(self):
        for origin in ('null', '*', ORIGIN + '/', 'http://user:pass@localhost:8082'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                WorkspaceCapabilities(origins=[origin])
        for value in (0, -1, float('inf'), float('nan'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WorkspaceCapabilities(token_ttl_ms=value)
        with self.assertRaises(ValueError):
            WorkspaceCapabilities(roots=[{'path': 'relative'}])
        with self.assertRaises(ValueError):
            WorkspaceCapabilities(roots=[{'path': Path.cwd().anchor}])


if __name__ == '__main__':
    unittest.main()
