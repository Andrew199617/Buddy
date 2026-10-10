"""Offline safety tests for subscription machine selection and metadata.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_subscription_machines.py -v

The loader executes production definitions with in-memory dependencies. It does
not import Buddy's database, runtime configuration, providers, or HTTP clients.
"""

import ast
import asyncio
import copy
import ipaddress
import logging
import re
import sys
import time
import types
import unittest
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTIONS = ROOT / 'backend' / 'open_webui' / 'utils' / 'subscriptions'


class MemoryConfig:
    def __init__(self, initial=None):
        self.values = copy.deepcopy(initial or {})
        self.commits = []

    async def get(self, key, default=None):
        return copy.deepcopy(self.values.get(key, default))

    async def upsert(self, updates):
        # Reads return copies so mutation before this one call cannot leak into
        # saved state; a commit must contain every affected setting atomically.
        self.commits.append(copy.deepcopy(updates))
        self.values.update(copy.deepcopy(updates))


class FakeProvider:
    def __init__(self, *args):
        self.calls = []
        self.observe_cleanup = None

    async def cancel_login(self):
        if self.observe_cleanup:
            self.observe_cleanup()
        self.calls.append('cancel_login')


class FakeCodexProvider(FakeProvider):
    def stop(self):
        if self.observe_cleanup:
            self.observe_cleanup()
        self.calls.append('stop')


class FakeLocalMachine:
    id = 'local'
    name = 'This server'

    def __init__(self, *args):
        self.path_checks = []

    async def path_exists(self, path, kind):
        self.path_checks.append((path, kind))
        return True


class FakeRemoteMachine:
    def __init__(self, machine_id, name, url, key):
        self.id = machine_id
        self.name = name
        self.url = url.rstrip('/')
        self.key = key
        self.closed = 0
        self.path_checks = []

    def matches(self, name, url, key):
        return self.name == name and self.url == url.rstrip('/') and self.key == key

    async def path_exists(self, path, kind):
        self.path_checks.append((path, kind))
        return True

    async def info(self):
        raise AssertionError('A machine probe must be explicitly mocked in these tests')

    async def close(self):
        self.closed += 1


class FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


def load_service(config, data_dir):
    """Keep production function bodies; replace only import-bound dependencies."""
    module = types.ModuleType('subscription_machine_service_under_test')
    module.__file__ = str(SUBSCRIPTIONS / 'service.py')
    module.__dict__.update({
        'asyncio': asyncio,
        'ipaddress': ipaddress,
        'logging': logging,
        're': re,
        'time': time,
        'asdict': asdict,
        'dataclass': dataclass,
        'fields': fields,
        'Path': Path,
        'Any': Any,
        'Awaitable': Awaitable,
        'Callable': Callable,
        'urlsplit': urlsplit,
        'uuid': uuid,
        'HTTPException': FakeHTTPException,
        'status': types.SimpleNamespace(HTTP_404_NOT_FOUND=404, HTTP_403_FORBIDDEN=403),
        'DATA_DIR': data_dir,
        'UVICORN_WORKERS': 1,
        'Config': config,
        'Models': types.SimpleNamespace(),
        'ClaudeCodeProvider': FakeProvider,
        'CodexProvider': FakeCodexProvider,
        'LOCAL_MACHINE_ID': 'local',
        'LocalMachine': FakeLocalMachine,
        'RemoteMachine': FakeRemoteMachine,
        'SUBSCRIPTION_OWNED_BY': 'subscription'
    })
    # Use the actual settings dataclass and error type without importing the
    # conversation, process, provider, or database modules they normally accompany.
    common_tree = ast.parse((SUBSCRIPTIONS / 'common.py').read_text(encoding='utf-8'))
    common_nodes = []
    for node in common_tree.body:
        if isinstance(node, ast.ClassDef) and node.name in ('ProviderSettings', 'TurnRequest'):
            common_nodes.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == 'requested_effort':
            common_nodes.append(node)
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & {'ACCESS_CHAT', 'ACCESS_READ', 'ACCESS_FULL', 'ACCESS_LEVELS'}:
                common_nodes.append(node)
    events_tree = ast.parse((SUBSCRIPTIONS / 'events.py').read_text(encoding='utf-8'))
    error_nodes = [node for node in events_tree.body if isinstance(node, ast.ClassDef) and node.name == 'SubscriptionError']
    machines_tree = ast.parse((SUBSCRIPTIONS / 'machines.py').read_text(encoding='utf-8'))
    error_nodes.extend(
        node for node in machines_tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'StartupCleanupUnconfirmedError'
    )
    service_tree = ast.parse((SUBSCRIPTIONS / 'service.py').read_text(encoding='utf-8'))
    service_nodes = [node for node in service_tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    future_annotations = ast.ImportFrom(
        module='__future__', names=[ast.alias(name='annotations')], level=0
    )
    tree = ast.Module(body=[future_annotations, *common_nodes, *error_nodes, *service_nodes], type_ignores=[])
    ast.fix_missing_locations(tree)
    with patch.dict(sys.modules, {module.__name__: module}):
        exec(compile(tree, module.__file__, 'exec'), module.__dict__)
    return module


def machine_config(machine_id='runner-a', host_id='host-a'):
    return {
        'id': machine_id,
        'name': f'Test {machine_id}',
        'url': f'https://{machine_id}.example.invalid:8083',
        'key': f'fixture-key-{machine_id}',
        'revision': f'fixture-revision-{machine_id}',
        'browser_url': f'https://browser-{machine_id}.example.invalid:8083',
        'host': {'id': host_id, 'name': 'Fixture PC', 'platform': 'win32', 'version': '1'},
        'capabilities': {'directories': True, 'files': True, 'terminals': True}
    }


def enabled_settings(machine_id):
    return {
        'enable': True,
        'access': 'full',
        'workspace': 'C:\\fixture-project',
        'cli_path': 'C:\\fixture-tools\\provider.exe',
        'machine_id': machine_id
    }


class SubscriptionMachineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [machine_config(), machine_config('runner-b', 'host-b')],
            'subscriptions.claude': enabled_settings('runner-a'),
            'subscriptions.codex': enabled_settings('runner-b')
        })
        # No dependency creates DATA_DIR: this is just a value used to assemble
        # the production STATE_DIR while every filesystem-capable object is fake.
        self.service = load_service(self.config, ROOT / 'unused-offline-machine-test-data')
        # These are the only provider objects exercised; their cleanup has no
        # CLI, process, sign-in, credential-store, or network implementation.
        self.providers = {'claude': FakeProvider(), 'codex': FakeCodexProvider()}
        self.service.PROVIDERS = self.providers
        self.cached = {}
        for config in self.config.values['subscriptions.machines']:
            cached = FakeRemoteMachine(config['id'], config['name'], config['url'], config['key'])
            self.cached[config['id']] = cached
        self.service._remote_machines = dict(self.cached)

    def assert_reset(self, provider_id, machine_id):
        settings = self.config.values[f'subscriptions.{provider_id}']
        self.assertEqual(settings['machine_id'], machine_id)
        self.assertIs(settings['enable'], False)
        self.assertEqual(settings['access'], 'chat')
        self.assertEqual(settings['workspace'], '')
        self.assertEqual(settings['cli_path'], '')

    async def test_deletion_commits_all_resets_before_cleanup_and_never_falls_back(self):
        self.config.values['subscriptions.codex'] = enabled_settings('runner-a')
        previous_b = copy.deepcopy(self.config.values['subscriptions.machines'][1])

        def observe_committed_reset():
            self.assertEqual(len(self.config.commits), 1)
            self.assert_reset('claude', 'runner-a')
            self.assert_reset('codex', 'runner-a')
            self.assertEqual(self.config.values['subscriptions.machines'], [previous_b])

        for provider in self.providers.values():
            provider.observe_cleanup = observe_committed_reset
        self.service.status_cache._entries['claude'] = object()
        self.service.models_cache._entries['codex'] = object()

        await self.service.delete_machine('runner-a')

        self.assertEqual(set(self.config.commits[0]), {
            'subscriptions.machines', 'subscriptions.claude', 'subscriptions.codex'
        })
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])
        self.assertIn('cancel_login', self.providers['codex'].calls)
        self.assertIn('stop', self.providers['codex'].calls)
        self.assertEqual(self.cached['runner-a'].closed, 1)
        self.assertEqual(self.cached['runner-b'].closed, 0)
        self.assertNotIn('runner-a', self.service._remote_machines)
        self.assertNotIn('claude', self.service.status_cache._entries)
        self.assertNotIn('codex', self.service.models_cache._entries)
        for provider_id in self.providers:
            settings = await self.service.get_settings(provider_id)
            self.assertEqual(settings.machine_id, 'runner-a')
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.get_machine(settings.machine_id)
        self.assertEqual(self.service.LOCAL_MACHINE.path_checks, [])

    async def test_deletion_leaves_providers_on_other_machines_unchanged(self):
        previous = copy.deepcopy(self.config.values['subscriptions.codex'])
        await self.service.delete_machine('runner-a')
        self.assert_reset('claude', 'runner-a')
        self.assertEqual(self.config.values['subscriptions.codex'], previous)
        self.assertEqual(self.providers['codex'].calls, [])
        self.assertNotIn('subscriptions.codex', self.config.commits[0])

    async def test_this_server_cannot_be_deleted(self):
        before = copy.deepcopy(self.config.values)
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine('local')
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertTrue(all(machine.closed == 0 for machine in self.cached.values()))

    async def test_selecting_a_new_machine_discards_authority_in_an_old_full_settings_request(self):
        updates = enabled_settings('runner-b')
        settings = await self.service.save_settings(
            'claude', updates, expected_machine_id='runner-a',
            expected_machine_revision='fixture-revision-runner-a'
        )
        self.assert_reset('claude', 'runner-b')
        self.assertEqual(asdict(settings), self.config.values['subscriptions.claude'])
        self.assertEqual(self.cached['runner-b'].path_checks, [])
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])

        # Granting access on the new host requires a subsequent explicit save.
        approved = await self.service.save_settings('claude', {
            'enable': True, 'access': 'read', 'workspace': 'D:\\new-fixture-project'
        }, expected_machine_id='runner-b', expected_machine_revision='fixture-revision-runner-b')
        self.assertTrue(approved.enable)
        self.assertEqual(approved.access, 'read')
        self.assertEqual(approved.workspace, 'D:\\new-fixture-project')
        self.assertEqual(self.cached['runner-b'].path_checks, [('D:\\new-fixture-project', 'dir')])

    async def test_selecting_a_new_machine_stops_the_codex_session(self):
        await self.service.save_settings(
            'codex', enabled_settings('runner-a'), expected_machine_id='runner-b',
            expected_machine_revision='fixture-revision-runner-b'
        )
        self.assert_reset('codex', 'runner-a')
        self.assertIn('cancel_login', self.providers['codex'].calls)
        self.assertIn('stop', self.providers['codex'].calls)
        self.assertEqual(self.cached['runner-a'].path_checks, [])

    async def test_unknown_machine_selection_does_not_save_or_use_the_local_host(self):
        before = copy.deepcopy(self.config.values)
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings(
                'claude', enabled_settings('missing-runner'), expected_machine_id='runner-a',
                expected_machine_revision='fixture-revision-runner-a'
            )
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertEqual(self.service.LOCAL_MACHINE.path_checks, [])

    async def test_saving_settings_on_the_same_machine_preserves_normal_access_and_path_checks(self):
        settings = await self.service.save_settings('claude', {
            'machine_id': 'runner-a',
            'enable': True,
            'access': 'read',
            'workspace': '  C:\\updated-fixture-project  ',
            'cli_path': '  C:\\fixture-tools\\updated.exe  '
        }, expected_machine_id='runner-a', expected_machine_revision='fixture-revision-runner-a')
        self.assertTrue(settings.enable)
        self.assertEqual(settings.access, 'read')
        self.assertEqual(settings.workspace, 'C:\\updated-fixture-project')
        self.assertEqual(settings.cli_path, 'C:\\fixture-tools\\updated.exe')
        self.assertEqual(self.cached['runner-a'].path_checks, [
            ('C:\\updated-fixture-project', 'dir'), ('C:\\fixture-tools\\updated.exe', 'file')
        ])
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])

    async def assert_identity_edit_resets(self, *, url=None, key=None, host_id='host-a', provider_id='claude'):
        other_provider = 'codex' if provider_id == 'claude' else 'claude'
        self.config.values[f'subscriptions.{provider_id}'] = enabled_settings('runner-a')
        self.config.values[f'subscriptions.{other_provider}'] = enabled_settings('runner-b')
        before_other = copy.deepcopy(self.config.values[f'subscriptions.{other_provider}'])
        original = self.config.values['subscriptions.machines'][0]
        info = {'host': {**original['host'], 'id': host_id}, 'capabilities': {'files': True}}
        verify = AsyncMock(return_value=info)
        with patch.object(self.service, 'verify_machine', verify):
            await self.service.save_machine(
                'runner-a', original['name'], url or original['url'], key,
                browser_url=original['browser_url']
            )
        self.assertEqual(len(self.config.commits), 1)
        self.assertEqual(set(self.config.commits[0]), {
            'subscriptions.machines', f'subscriptions.{provider_id}'
        })
        self.assert_reset(provider_id, 'runner-a')
        self.assertEqual(self.config.values[f'subscriptions.{other_provider}'], before_other)
        self.assertIn('cancel_login', self.providers[provider_id].calls)
        self.assertEqual(self.providers[other_provider].calls, [])
        if provider_id == 'codex':
            self.assertIn('stop', self.providers[provider_id].calls)
        self.assertEqual(self.cached['runner-a'].closed, 1)
        self.assertNotIn('runner-a', self.service._remote_machines)
        self.assertEqual(self.cached['runner-b'].closed, 0)

    async def test_editing_backend_address_resets_only_its_affected_provider(self):
        await self.assert_identity_edit_resets(url='https://replacement.example.invalid:8083')

    async def test_editing_backend_key_resets_its_codex_session_and_access(self):
        await self.assert_identity_edit_resets(key='replacement-fixture-key', provider_id='codex')

    async def test_changed_observed_host_identity_resets_access_even_at_the_same_address(self):
        await self.assert_identity_edit_resets(host_id='different-fixture-host')

    async def test_label_and_browser_address_changes_do_not_reset_backend_authority(self):
        original = copy.deepcopy(self.config.values['subscriptions.machines'][0])
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(
                'runner-a', 'Renamed fixture PC', original['url'], '',
                browser_url='https://new-browser.example.invalid:9443/'
            )
        self.assertEqual(public['browser_url'], 'https://new-browser.example.invalid:9443')
        self.assertEqual(self.config.values['subscriptions.claude'], before_settings)
        self.assertEqual(self.config.values['subscriptions.machines'][0]['key'], original['key'])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertEqual(set(self.config.commits[0]), {'subscriptions.machines'})

    async def test_first_observed_metadata_does_not_reset_an_unchanged_backend(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('host')
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': {'id': 'host-a'}})):
            await self.service.save_machine(
                'runner-a', original['name'], original['url'], None,
                browser_url=original['browser_url']
            )
        self.assertEqual(self.config.values['subscriptions.claude'], before_settings)
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_failed_backend_verification_does_not_change_saved_authority(self):
        before = copy.deepcopy(self.config.values)
        failed_probe = AsyncMock(side_effect=self.service.SubscriptionError('Fixture runner refused the key'))
        with patch.object(self.service, 'verify_machine', failed_probe):
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.save_machine(
                    'runner-a', 'Replacement fixture', 'https://replacement.example.invalid', 'bad-fixture-key'
                )
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_verification_closes_the_temporary_machine_after_a_successful_probe(self):
        info = {'host': {'id': 'fixture-verified-host'}, 'capabilities': {'files': True}}
        probe = FakeRemoteMachine('verify', 'Fixture probe', 'https://probe.example.invalid', 'fixture-probe-key')
        probe.info = AsyncMock(return_value=info)
        with patch.object(self.service, 'RemoteMachine', return_value=probe) as factory:
            result = await self.service.verify_machine('https://probe.example.invalid', 'fixture-probe-key')
        factory.assert_called_once_with('verify', 'The runner', 'https://probe.example.invalid', 'fixture-probe-key')
        probe.info.assert_awaited_once()
        self.assertEqual(result, info)
        self.assertEqual(probe.closed, 1)
        self.assertEqual(self.config.commits, [])

    async def test_verification_closes_the_temporary_machine_when_the_probe_fails(self):
        probe = FakeRemoteMachine('verify', 'Fixture probe', 'https://probe.example.invalid', 'fixture-probe-key')
        probe.info = AsyncMock(side_effect=self.service.SubscriptionError('Fixture probe failed'))
        with patch.object(self.service, 'RemoteMachine', return_value=probe):
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.verify_machine('https://probe.example.invalid', 'fixture-probe-key')
        self.assertEqual(probe.closed, 1)
        self.assertEqual(self.config.commits, [])

    async def test_machine_list_exposes_only_safe_optional_metadata_and_no_keys_or_paths(self):
        config = self.config.values['subscriptions.machines'][0]
        config.update({'workspace': 'private-workspace', 'chat_dir': 'private-chat-directory'})
        config['host'].update({'key': 'private-host-key', 'default_workspace': 'private-default-directory'})
        config['capabilities'].update({'grants': ['private-root'], 'key': 'private-capability-key', 'pty': 'yes'})
        public = await self.service.describe_machines()
        remote = next(machine for machine in public if machine['id'] == 'runner-a')
        self.assertEqual(set(remote), {'id', 'name', 'url', 'revision', 'browser_url', 'host', 'capabilities'})
        self.assertEqual(remote['host'], {
            'id': 'host-a', 'name': 'Fixture PC', 'platform': 'win32', 'version': '1'
        })
        self.assertEqual(remote['capabilities'], {'directories': True, 'files': True, 'terminals': True})
        for machine in public:
            self.assertNotIn('key', machine)
            self.assertNotIn('workspace', machine)
            self.assertNotIn('chat_dir', machine)
        self.assertEqual(public[0], {'id': 'local', 'name': 'This server', 'url': None})
        self.assertEqual(config['host']['key'], 'private-host-key', 'redaction must not mutate saved configuration')

    async def test_save_machine_persists_and_returns_only_whitelisted_observed_metadata(self):
        info = {
            'host': {
                'id': 'fixture-new-host', 'name': 'Fixture remote', 'platform': 'linux', 'version': '2',
                'key': 'private-health-key', 'default_workspace': '/private/root'
            },
            'capabilities': {
                'directories': True, 'files': False, 'workspaces': True, 'terminals': True,
                'pty': False, 'providerExecution': True, 'roots': ['/private/root'], 'shell': 'bash'
            },
            'key': 'private-probe-key',
            'chat_dir': '/private/chat'
        }
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            public = await self.service.save_machine(
                None, 'New fixture PC', 'https://new-runner.example.invalid:8083/', 'new-fixture-key',
                browser_url='http://127.0.0.1:8083/'
            )
        expected_host = {'id': 'fixture-new-host', 'name': 'Fixture remote', 'platform': 'linux', 'version': '2'}
        expected_capabilities = {
            'directories': True, 'files': False, 'workspaces': True, 'terminals': True,
            'pty': False, 'providerExecution': True
        }
        self.assertEqual(public['host'], expected_host)
        self.assertEqual(public['capabilities'], expected_capabilities)
        self.assertEqual(public['browser_url'], 'http://127.0.0.1:8083')
        self.assertNotIn('key', public)
        saved = next(config for config in self.config.values['subscriptions.machines'] if config['id'] == public['id'])
        self.assertEqual(saved['key'], 'new-fixture-key')
        self.assertEqual(saved['host'], expected_host)
        self.assertEqual(saved['capabilities'], expected_capabilities)
        self.assertNotIn('chat_dir', saved)

    async def test_invalid_browser_address_is_rejected_before_verification_or_saving(self):
        verify = AsyncMock()
        with patch.object(self.service, 'verify_machine', verify):
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.save_machine(
                    None, 'Fixture PC', 'https://runner.example.invalid', 'fixture-key',
                    browser_url='http://192.0.2.10:8083'
                )
        verify.assert_not_awaited()
        self.assertEqual(self.config.commits, [])

    def test_browser_address_accepts_https_and_loopback_http_origins(self):
        self.assertIsNone(self.service.validate_browser_url(None))
        self.assertIsNone(self.service.validate_browser_url('   '))
        accepted = {
            'https://runner.example.invalid:8083/': 'https://runner.example.invalid:8083',
            ' https://192.0.2.10:9443 ': 'https://192.0.2.10:9443',
            'https://[2001:db8::1]:9443': 'https://[2001:db8::1]:9443',
            'http://localhost:8083/': 'http://localhost:8083',
            'http://127.0.0.1:8083': 'http://127.0.0.1:8083',
            'http://127.99.2.3:9443': 'http://127.99.2.3:9443',
            'http://[::1]:8083': 'http://[::1]:8083'
        }
        for value, expected in accepted.items():
            with self.subTest(value=value):
                self.assertEqual(self.service.validate_browser_url(value), expected)

    def test_browser_address_rejects_credentials_suffixes_remote_http_and_invalid_ports(self):
        rejected = [
            'localhost:8083', 'ftp://localhost:8083', 'ws://localhost:8083',
            'http://192.0.2.10:8083', 'http://runner.example.invalid:8083',
            'http://0.0.0.0:8083', 'http://[::]:8083',
            'http://localhost.example.invalid:8083', 'http://127.attacker.example.invalid:8083',
            'https://user:password@runner.example.invalid', 'https://@runner.example.invalid',
            'https://runner.example.invalid/project', 'https://runner.example.invalid/../',
            'https://runner.example.invalid?code=secret', 'https://runner.example.invalid/#fragment',
            'https://runner.example.invalid/?', 'https://runner.example.invalid/#',
            'https://runner.example.invalid:0', 'https://runner.example.invalid:-1',
            'https://runner.example.invalid:65536', 'https://runner.example.invalid:not-a-port',
            'https://runner.example.invalid\\project', 'https://runner.example.invalid\\@other.example.invalid',
            'https://[::1', 'https://'
        ]
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(self.service.SubscriptionError):
                    self.service.validate_browser_url(value)


if __name__ == '__main__':
    unittest.main()
