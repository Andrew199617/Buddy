"""Offline safety tests for subscription machine selection and metadata.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_subscription_machines.py -v

The loader executes production definitions with in-memory dependencies and the
actual transport validator. It does not import Buddy's database, runtime
configuration, or providers, and makes no HTTP requests.
"""

import ast
import asyncio
import copy
import hashlib
import hmac
import ipaddress
import secrets
import logging
import re
import sys
import time
import types
import unittest
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTIONS = ROOT / 'backend' / 'open_webui' / 'utils' / 'subscriptions'
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions.runner_transport import normalize_runner_url  # noqa: E402
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402
from open_webui.utils.subscriptions.machines import TunnelMachine  # noqa: E402
from open_webui.utils.subscriptions.tunnel_hub import RunnerCallError, RunnerHub  # noqa: E402
from open_webui.utils.subscriptions import folder_listing  # noqa: E402
from test_project_folders import FakeChats, FakeFolders, load_project_folders  # noqa: E402

# The instance owner: the user whose setup predates per-user machines. Most
# tests act as this user, whose settings keep the original config keys.
OWNER = 'owner-1'
OWNER_USER = types.SimpleNamespace(id=OWNER, role='admin')


def owner_providers(service, providers_by_id):
    """Install stand-in providers as the owner's own."""
    return {service.ProviderRef(OWNER, provider_id): provider for provider_id, provider in providers_by_id.items()}


class FakeUsers:
    def __init__(self, users=None):
        self.users = {OWNER: OWNER_USER, **(users or {})}

    async def get_first_user(self):
        return OWNER_USER

    async def get_super_admin_user(self):
        return OWNER_USER

    async def get_user_by_id(self, user_id):
        return self.users.get(user_id)


class FakeGroups:
    def __init__(self, memberships=None):
        self.memberships = memberships or {}

    async def get_groups_by_member_id(self, user_id):
        return [types.SimpleNamespace(id=group_id) for group_id in self.memberships.get(user_id, [])]


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

    def use_credential(self, credential_mode, api_key):
        self.credential_mode = credential_mode
        self.api_key = api_key


class MemoryCredentials:
    """Stored sign-ins in memory, with the same methods as SubscriptionCredentials."""

    def __init__(self):
        self.secrets = {}

    async def save(self, user_id, provider, kind, secret, expires_at=None):
        self.secrets[(user_id, provider, kind)] = (secret, expires_at)
        return self._describe(user_id, provider, kind)

    async def secret(self, user_id, provider, kind):
        stored = self.secrets.get((user_id, provider, kind))
        return stored[0] if stored else None

    def _describe(self, user_id, provider, kind):
        secret, expires_at = self.secrets[(user_id, provider, kind)]
        return types.SimpleNamespace(
            user_id=user_id, provider=provider, kind=kind, fingerprint='…' + secret[-4:], expires_at=expires_at,
        )

    async def describe(self, user_id):
        return [self._describe(*key) for key in self.secrets if key[0] == user_id]

    async def delete(self, user_id, provider, kind):
        return self.secrets.pop((user_id, provider, kind), None) is not None

    async def delete_user(self, user_id):
        for key in [key for key in self.secrets if key[0] == user_id]:
            del self.secrets[key]


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
    # No chat folders unless a test adds some; the owner of a folder may read it.
    folder_readers = {}
    module.__dict__.update({
        'sys': sys,
        'project_folders': load_project_folders(config, FakeFolders(), FakeChats(), folder_readers),
        'asyncio': asyncio,
        'hashlib': hashlib,
        'hmac': hmac,
        'ipaddress': ipaddress,
        'secrets': secrets,
        'logging': logging,
        're': re,
        'time': time,
        'asdict': asdict,
        'dataclass': dataclass,
        'field': field,
        'fields': fields,
        'replace': replace,
        'Path': Path,
        'Any': Any,
        'Awaitable': Awaitable,
        'Callable': Callable,
        'urlsplit': urlsplit,
        'uuid': uuid,
        'normalize_runner_url': normalize_runner_url,
        'SubscriptionError': SubscriptionError,
        'HTTPException': FakeHTTPException,
        'status': types.SimpleNamespace(HTTP_404_NOT_FOUND=404, HTTP_403_FORBIDDEN=403),
        'DATA_DIR': data_dir,
        'UVICORN_WORKERS': 1,
        'Config': config,
        'Models': types.SimpleNamespace(),
        'Users': FakeUsers(),
        'Groups': FakeGroups(),
        'SubscriptionCredentials': MemoryCredentials(),
        'KIND_API_KEY': 'api_key',
        'KIND_PLAN_TOKEN': 'plan_token',
        'CHANNEL_CHAT_ID_PREFIX': 'channel:',
        'ClaudeCodeProvider': FakeProvider,
        'CodexProvider': FakeCodexProvider,
        'LOCAL_MACHINE_ID': 'local',
        'LocalMachine': FakeLocalMachine,
        'RemoteMachine': FakeRemoteMachine,
        'TunnelMachine': TunnelMachine,
        'HUB': RunnerHub(),
        'RunnerCallError': RunnerCallError,
        'folder_listing': folder_listing,
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
            if names & {'ACCESS_CHAT', 'ACCESS_READ', 'ACCESS_FULL', 'ACCESS_LEVELS', 'CREDENTIAL_MACHINE',
                         'CREDENTIAL_PLAN_TOKEN', 'CREDENTIAL_API_KEY', 'CREDENTIAL_MODES'}:
                common_nodes.append(node)
    error_nodes = []
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
    module._server_owner_id = OWNER
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


def load_machine_router(service):
    """Execute the actual form and save endpoint without app/auth/database imports."""
    from pydantic import BaseModel

    router_path = ROOT / 'backend' / 'open_webui' / 'routers' / 'subscriptions.py'
    tree = ast.parse(router_path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in ('MachineForm', 'MachineVerifyForm', '_bad_request', 'save_machine', 'verify_machine')]
    for node in nodes:
        if isinstance(node, ast.AsyncFunctionDef):
            node.decorator_list = []
    module = types.ModuleType('subscription_machine_router_under_test')
    module.__dict__.update({
        'BaseModel': BaseModel,
        'Optional': Optional,
        'Depends': lambda dependency: None,
        'get_admin_user': lambda: None,
        'get_admin_session_user': lambda: None,
        'service': service,
        'SubscriptionError': Exception,
        'HTTPException': FakeHTTPException,
        'status': types.SimpleNamespace(HTTP_400_BAD_REQUEST=400),
    })
    with patch.dict(sys.modules, {module.__name__: module}):
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(router_path), 'exec'), module.__dict__)
    return module


class ExportableMemoryConfig(MemoryConfig):
    async def get_all(self):
        return copy.deepcopy(self.values)

    async def get_namespace(self, namespace):
        return {key: copy.deepcopy(value) for key, value in self.values.items() if key.startswith(f'{namespace}.')}


def load_config_router(service, config):
    """Execute the actual config import, export, and namespace endpoints offline."""
    from pydantic import BaseModel

    router_path = ROOT / 'backend' / 'open_webui' / 'routers' / 'configs.py'
    tree = ast.parse(router_path.read_text(encoding='utf-8'))
    names = ('ImportConfigForm', 'import_config', 'export_config', 'get_config_namespace')
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in nodes:
        if isinstance(node, ast.AsyncFunctionDef):
            node.decorator_list = []
    future_annotations = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = types.ModuleType('config_router_under_test')
    module.__dict__.update({
        'BaseModel': BaseModel,
        'Depends': lambda dependency: None,
        'get_admin_user': lambda: None,
        'Config': config,
        'EVENTS': types.SimpleNamespace(CONFIG_IMPORTED='config.imported'),
        'publish_event': AsyncMock(),
        'keep_runner_keys': service.keep_runner_keys,
        'redact_runner_keys': service.redact_runner_keys,
    })
    tree = ast.fix_missing_locations(ast.Module(body=[future_annotations, *nodes], type_ignores=[]))
    with patch.dict(sys.modules, {module.__name__: module}):
        exec(compile(tree, str(router_path), 'exec'), module.__dict__)
    return module


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
        self.service._providers = owner_providers(self.service, self.providers)
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
        self.service.status_cache._entries[self.service.ProviderRef(OWNER, 'claude')] = object()
        self.service.models_cache._entries[self.service.ProviderRef(OWNER, 'codex')] = object()

        await self.service.delete_machine(OWNER, 'runner-a')

        self.assertEqual(set(self.config.commits[0]), {
            'subscriptions.machines', 'subscriptions.claude', 'subscriptions.codex'
        })
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])
        self.assertIn('cancel_login', self.providers['codex'].calls)
        self.assertIn('stop', self.providers['codex'].calls)
        self.assertEqual(self.cached['runner-a'].closed, 1)
        self.assertEqual(self.cached['runner-b'].closed, 0)
        self.assertNotIn('runner-a', self.service._remote_machines)
        self.assertNotIn(self.service.ProviderRef(OWNER, 'claude'), self.service.status_cache._entries)
        self.assertNotIn(self.service.ProviderRef(OWNER, 'codex'), self.service.models_cache._entries)
        for provider_id in self.providers:
            settings = await self.service.get_settings(OWNER, provider_id)
            self.assertEqual(settings.machine_id, 'runner-a')
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.get_machine(OWNER, settings.machine_id)
        self.assertEqual(self.service.LOCAL_MACHINE.path_checks, [])

    async def test_deletion_leaves_providers_on_other_machines_unchanged(self):
        previous = copy.deepcopy(self.config.values['subscriptions.codex'])
        await self.service.delete_machine(OWNER, 'runner-a')
        self.assert_reset('claude', 'runner-a')
        self.assertEqual(self.config.values['subscriptions.codex'], previous)
        self.assertEqual(self.providers['codex'].calls, [])
        self.assertNotIn('subscriptions.codex', self.config.commits[0])

    async def test_this_server_cannot_be_deleted(self):
        before = copy.deepcopy(self.config.values)
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine(OWNER, 'local')
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertTrue(all(machine.closed == 0 for machine in self.cached.values()))

    async def test_selecting_a_new_machine_discards_authority_in_an_old_full_settings_request(self):
        updates = enabled_settings('runner-b')
        settings = await self.service.save_settings(OWNER, 
            'claude', updates, expected_machine_id='runner-a',
            expected_machine_revision='fixture-revision-runner-a'
        )
        self.assert_reset('claude', 'runner-b')
        self.assertEqual(asdict(settings), self.config.values['subscriptions.claude'])
        self.assertEqual(self.cached['runner-b'].path_checks, [])
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])

        # Granting access on the new host requires a subsequent explicit save.
        approved = await self.service.save_settings(OWNER, 'claude', {
            'enable': True, 'access': 'read', 'workspace': 'D:\\new-fixture-project'
        }, expected_machine_id='runner-b', expected_machine_revision='fixture-revision-runner-b')
        self.assertTrue(approved.enable)
        self.assertEqual(approved.access, 'read')
        self.assertEqual(approved.workspace, 'D:\\new-fixture-project')
        self.assertEqual(self.cached['runner-b'].path_checks, [('D:\\new-fixture-project', 'dir')])

    async def test_selecting_a_new_machine_stops_the_codex_session(self):
        await self.service.save_settings(OWNER, 
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
            await self.service.save_settings(OWNER, 
                'claude', enabled_settings('missing-runner'), expected_machine_id='runner-a',
                expected_machine_revision='fixture-revision-runner-a'
            )
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertEqual(self.service.LOCAL_MACHINE.path_checks, [])

    async def test_saving_settings_on_the_same_machine_preserves_normal_access_and_path_checks(self):
        settings = await self.service.save_settings(OWNER, 'claude', {
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
            await self.service.save_machine(OWNER, 
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
        await self.assert_identity_edit_resets(
            url='https://replacement.example.invalid:8083', key='fixture-key-runner-a'
        )

    async def test_new_backend_address_requires_a_key_instead_of_sending_the_saved_one(self):
        before = copy.deepcopy(self.config.values)
        verify = AsyncMock()
        with patch.object(self.service, 'verify_machine', verify):
            for key in (None, '', '   '):
                with self.subTest(key=key), self.assertRaises(self.service.SubscriptionError) as caught:
                    await self.service.save_machine(OWNER, 
                        'runner-a', 'Test runner-a', 'https://replacement.example.invalid:8083', key
                    )
                self.assertIn('new address', str(caught.exception))
        verify.assert_not_awaited()
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_editing_backend_key_resets_its_codex_session_and_access(self):
        await self.assert_identity_edit_resets(key='replacement-fixture-key', provider_id='codex')

    async def test_changed_observed_host_identity_resets_access_even_at_the_same_address(self):
        await self.assert_identity_edit_resets(host_id='different-fixture-host')

    async def test_label_and_browser_address_changes_do_not_reset_backend_authority(self):
        original = copy.deepcopy(self.config.values['subscriptions.machines'][0])
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', 'Renamed fixture PC', original['url'], '',
                browser_url='https://new-browser.example.invalid:9443/'
            )
        self.assertEqual(public['browser_url'], 'https://new-browser.example.invalid:9443')
        self.assertEqual(self.config.values['subscriptions.claude'], before_settings)
        self.assertEqual(self.config.values['subscriptions.machines'][0]['key'], original['key'])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertEqual(set(self.config.commits[0]), {'subscriptions.machines'})

    async def test_rename_without_browser_address_preserves_saved_address_and_revision(self):
        original = copy.deepcopy(self.config.values['subscriptions.machines'][0])
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 'runner-a', 'Renamed fixture PC', original['url'], '')
        self.assertEqual(public['browser_url'], original['browser_url'])
        self.assertEqual(public['revision'], original['revision'])
        self.assertEqual(self.config.values['subscriptions.claude'], before_settings)
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertEqual(set(self.config.commits[0]), {'subscriptions.machines'})

    async def test_explicit_null_browser_address_clears_without_changing_revision_or_authority(self):
        original = copy.deepcopy(self.config.values['subscriptions.machines'][0])
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], original['url'], None,
                browser_url=None, browser_url_supplied=True
            )
        self.assertIsNone(self.config.values['subscriptions.machines'][0]['browser_url'])
        self.assertNotIn('browser_url', public)
        self.assertEqual(public['revision'], original['revision'])
        self.assertEqual(self.config.values['subscriptions.claude'], before_settings)
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_blank_browser_address_still_explicitly_clears_the_saved_address(self):
        original = copy.deepcopy(self.config.values['subscriptions.machines'][0])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], original['url'], None, browser_url='   '
            )
        self.assertIsNone(self.config.values['subscriptions.machines'][0]['browser_url'])
        self.assertNotIn('browser_url', public)
        self.assertEqual(public['revision'], original['revision'])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_browser_address_omission_keeps_legacy_field_absent(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('browser_url')
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', 'Renamed fixture PC', original['url'], None,
                browser_url=None, browser_url_supplied=False
            )
        self.assertNotIn('browser_url', self.config.values['subscriptions.machines'][0])
        self.assertNotIn('browser_url', public)
        self.assertEqual(public['revision'], original['revision'])

    async def test_new_machine_without_browser_address_registers_without_one(self):
        info = {'host': {'id': 'fixture-new-host'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            public = await self.service.save_machine(OWNER, 
                None, 'New fixture PC', 'https://new-runner.example.invalid:8083', 'new-fixture-key'
            )
        self.assertNotIn('browser_url', public)
        self.assertTrue(public['revision'])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_first_observed_metadata_does_not_reset_an_unchanged_backend(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('host')
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': {'id': 'host-a'}})):
            await self.service.save_machine(OWNER, 
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
                await self.service.save_machine(OWNER, 
                    'runner-a', 'Replacement fixture', 'https://replacement.example.invalid', 'bad-fixture-key'
                )
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_runner_address_is_canonical_before_verification_and_persistence(self):
        verify = AsyncMock(return_value={'host': {'id': 'fixture-new-host'}})
        with patch.object(self.service, 'verify_machine', verify):
            public = await self.service.save_machine(OWNER, 
                None, 'New fixture PC', ' HTTPS://RUNNER.EXAMPLE.INVALID:443/ ', 'fixture-key'
            )
        verify.assert_awaited_once_with('https://runner.example.invalid', 'fixture-key')
        self.assertEqual(public['url'], 'https://runner.example.invalid')
        saved = next(item for item in self.config.values['subscriptions.machines'] if item['id'] == public['id'])
        self.assertEqual(saved['url'], public['url'])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_unsafe_runner_addresses_fail_before_probe_or_config_write(self):
        before = copy.deepcopy(self.config.values)
        verify = AsyncMock()
        with patch.object(self.service, 'verify_machine', verify):
            for url in ('http://host.docker.internal:8083', 'http://100.122.80.32:8083',
                        'http://192.0.2.10:8083', '\nhttps://runner.example.invalid'):
                with self.subTest(url=url), self.assertRaises(self.service.SubscriptionError):
                    await self.service.save_machine(OWNER, 'runner-a', 'Fixture PC', url, 'fixture-key')
        verify.assert_not_awaited()
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))

    async def test_equivalent_runner_origin_preserves_revision_and_provider_authority(self):
        original = self.config.values['subscriptions.machines'][0]
        original['url'] = 'https://RUNNER-A.EXAMPLE.INVALID:443/'
        before_settings = copy.deepcopy(self.config.values['subscriptions.claude'])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], 'https://runner-a.example.invalid', None
            )
        self.assertEqual(public['url'], 'https://runner-a.example.invalid')
        self.assertEqual(public['revision'], original['revision'])
        self.assertEqual(self.config.values['subscriptions.claude'], before_settings)
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertEqual(set(self.config.commits[0]), {'subscriptions.machines'})

    async def test_upgrade_of_unsafe_legacy_origin_resets_provider_authority(self):
        original = self.config.values['subscriptions.machines'][0]
        original['url'] = 'http://runner-a.example.invalid:8083'
        old_revision = original['revision']
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            # The HTTPS origin is a new address, so the key must be entered again.
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], 'https://runner-a.example.invalid:8083', original['key']
            )
        self.assertNotEqual(public['revision'], old_revision)
        self.assert_reset('claude', 'runner-a')
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])

    async def test_direct_verification_normalizes_before_constructing_the_probe(self):
        probe = FakeRemoteMachine('verify', 'Fixture probe', 'https://probe.example.invalid', 'fixture-key')
        probe.info = AsyncMock(return_value={'host': {'id': 'fixture-probe'}})
        with patch.object(self.service, 'RemoteMachine', return_value=probe) as factory:
            await self.service.verify_machine(' HTTPS://PROBE.EXAMPLE.INVALID:443/ ', 'fixture-key')
        factory.assert_called_once_with('verify', 'The runner', 'https://probe.example.invalid', 'fixture-key')
        self.assertEqual(probe.closed, 1)
        with patch.object(self.service, 'RemoteMachine') as factory:
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.verify_machine('http://host.docker.internal:8083', 'fixture-key')
        factory.assert_not_called()
        self.assertEqual(self.config.commits, [])

    async def test_verification_rejects_a_browser_address_that_save_would_reject(self):
        with patch.object(self.service, 'RemoteMachine') as factory:
            with self.assertRaises(self.service.SubscriptionError):
                await self.service.verify_machine(
                    'https://probe.example.invalid', 'fixture-key', 'http://192.0.2.10:8083'
                )
        factory.assert_not_called()
        probe = FakeRemoteMachine('verify', 'Fixture probe', 'https://probe.example.invalid', 'fixture-key')
        probe.info = AsyncMock(return_value={'host': {'id': 'fixture-probe'}})
        with patch.object(self.service, 'RemoteMachine', return_value=probe):
            await self.service.verify_machine(
                'https://probe.example.invalid', 'fixture-key', 'http://127.0.0.1:8083'
            )
        probe.info.assert_awaited_once()
        self.assertEqual(self.config.commits, [])

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
        public = await self.service.describe_machines(OWNER, )
        remote = next(machine for machine in public if machine['id'] == 'runner-a')
        self.assertEqual(
            set(remote), {'id', 'name', 'url', 'has_key', 'revision', 'browser_url', 'host', 'capabilities'}
        )
        self.assertIs(remote['has_key'], True)
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

    async def test_malformed_observed_metadata_is_left_out_of_the_machine_list(self):
        config = self.config.values['subscriptions.machines'][0]
        config['host'] = 'not-a-host-record'
        config['capabilities'] = ['files']
        public = await self.service.describe_machines(OWNER, )
        remote = next(machine for machine in public if machine['id'] == 'runner-a')
        self.assertNotIn('host', remote)
        self.assertNotIn('capabilities', remote)

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
            public = await self.service.save_machine(OWNER, 
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
                await self.service.save_machine(OWNER, 
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


class RunnerKeyConfigExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ExportableMemoryConfig({
            'subscriptions.machines': [machine_config(), machine_config('runner-b', 'host-b')],
            'subscriptions.claude': enabled_settings('runner-a'),
            'ui.default_models': 'fixture-model',
        })
        self.service = load_service(self.config, ROOT / 'unused-offline-export-data')
        self.router = load_config_router(self.service, self.config)

    def assert_no_runner_keys(self, values):
        machines = values['subscriptions.machines']
        self.assertEqual([machine['id'] for machine in machines], ['runner-a', 'runner-b'])
        for machine in machines:
            self.assertNotIn('key', machine)

    async def test_export_and_namespace_leave_runner_keys_out(self):
        exported = await self.router.export_config(user=object())
        self.assert_no_runner_keys(exported)
        self.assertEqual(exported['ui.default_models'], 'fixture-model')
        self.assertEqual(exported['subscriptions.machines'][0]['url'], machine_config()['url'])
        namespace = await self.router.get_config_namespace('subscriptions', user=object())
        self.assert_no_runner_keys(namespace)
        self.assertEqual(namespace['subscriptions.claude'], enabled_settings('runner-a'))
        stored = self.config.values['subscriptions.machines']
        self.assertEqual(stored[0]['key'], 'fixture-key-runner-a', 'redaction must not change saved machines')

    async def test_importing_an_export_keeps_saved_keys_and_returns_none(self):
        exported = await self.router.export_config(user=object())
        form = self.router.ImportConfigForm(config=exported)
        response = await self.router.import_config(object(), form, user=object())
        self.assert_no_runner_keys(response)
        stored = self.config.values['subscriptions.machines']
        self.assertEqual(stored[0]['key'], 'fixture-key-runner-a')
        self.assertEqual(stored[1]['key'], 'fixture-key-runner-b')

    async def test_import_keeps_a_key_only_for_the_same_machine_at_the_same_address(self):
        moved = {**machine_config(), 'url': 'https://moved.example.invalid:8083'}
        moved.pop('key')
        equivalent = {**machine_config('runner-b', 'host-b'), 'url': 'HTTPS://RUNNER-B.EXAMPLE.INVALID:8083/'}
        equivalent.pop('key')
        unknown = {**machine_config('runner-c', 'host-c')}
        unknown.pop('key')
        replaced = {**machine_config('runner-d', 'host-d'), 'key': 'fixture-imported-key'}
        imported = await self.service.keep_runner_keys({
            'subscriptions.machines': [moved, equivalent, unknown, replaced],
            'ui.default_models': 'fixture-model',
        })
        restored = imported['subscriptions.machines']
        self.assertNotIn('key', restored[0])
        self.assertEqual(restored[1]['key'], 'fixture-key-runner-b')
        self.assertNotIn('key', restored[2])
        self.assertEqual(restored[3]['key'], 'fixture-imported-key')
        self.assertEqual(imported['ui.default_models'], 'fixture-model')

    async def test_a_machine_without_a_key_asks_for_it_instead_of_connecting(self):
        self.config.values['subscriptions.machines'][0].pop('key')
        with patch.object(self.service, 'RemoteMachine') as factory:
            with self.assertRaises(self.service.SubscriptionError) as caught:
                await self.service.get_machine(OWNER, 'runner-a')
        factory.assert_not_called()
        self.assertIn('runner key', str(caught.exception))

    async def test_the_machine_list_says_which_machines_need_their_key_again(self):
        self.config.values['subscriptions.machines'][0].pop('key')
        public = {machine['id']: machine for machine in await self.service.describe_machines(OWNER, )}
        self.assertIs(public['runner-a']['has_key'], False)
        self.assertIs(public['runner-b']['has_key'], True)
        self.assertNotIn('key', public['runner-a'])
        self.assertNotIn('has_key', public['local'])

    async def test_a_provider_on_a_machine_without_a_key_can_still_be_turned_off(self):
        self.config.values['subscriptions.machines'][0].pop('key')
        self.service._providers = owner_providers(self.service, {'claude': FakeProvider(), 'codex': FakeCodexProvider()})
        with patch.object(self.service, 'RemoteMachine') as factory:
            settings = await self.service.save_settings(OWNER, 
                'claude', {'enable': False}, 'runner-a', 'fixture-revision-runner-a'
            )
            self.assertIs(settings.enable, False)
            self.assertEqual(self.config.values['subscriptions.claude'], {
                **enabled_settings('runner-a'), 'enable': False
            })
            with self.assertRaises(self.service.SubscriptionError) as caught:
                await self.service.save_settings(OWNER, 
                    'claude', {'enable': True}, 'runner-a', 'fixture-revision-runner-a'
                )
        factory.assert_not_called()
        self.assertIn('runner key', str(caught.exception))
        self.assertIs(self.config.values['subscriptions.claude']['enable'], False)


class SubscriptionMachineRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = types.SimpleNamespace(
            save_machine=AsyncMock(return_value={'id': 'runner-a'}),
            verify_machine=AsyncMock(return_value={'host': {'id': 'fixture-host'}}),
            describe_machines=AsyncMock(return_value=[]),
        )
        self.router = load_machine_router(self.service)

    async def assert_browser_field_forwarding(self, fields, supplied):
        form = self.router.MachineForm.model_validate({
            'id': 'runner-a', 'name': 'Renamed fixture PC', 'url': 'https://runner.example.invalid:8083',
            **fields,
        })
        await self.router.save_machine(form, user=OWNER_USER)
        self.service.save_machine.assert_awaited_once_with(
            OWNER, 'runner-a', 'Renamed fixture PC', 'https://runner.example.invalid:8083', None,
            fields.get('browser_url'), browser_url_supplied=supplied,
        )

    async def test_old_form_omission_is_forwarded_as_unsupplied(self):
        await self.assert_browser_field_forwarding({}, False)

    async def test_explicit_null_is_forwarded_as_supplied(self):
        await self.assert_browser_field_forwarding({'browser_url': None}, True)

    async def test_explicit_browser_address_is_forwarded_as_supplied(self):
        await self.assert_browser_field_forwarding({'browser_url': 'https://browser.example.invalid'}, True)

    async def test_verify_forwards_the_browser_address_for_validation(self):
        form = self.router.MachineVerifyForm.model_validate({
            'url': 'https://runner.example.invalid:8083', 'key': 'fixture-key',
            'browser_url': 'http://127.0.0.1:8083',
        })
        await self.router.verify_machine(form, user=OWNER_USER)
        self.service.verify_machine.assert_awaited_once_with(
            'https://runner.example.invalid:8083', 'fixture-key', 'http://127.0.0.1:8083'
        )

    async def test_verify_without_a_browser_address_still_checks_the_runner(self):
        form = self.router.MachineVerifyForm.model_validate({
            'url': 'https://runner.example.invalid:8083', 'key': 'fixture-key',
        })
        await self.router.verify_machine(form, user=OWNER_USER)
        self.service.verify_machine.assert_awaited_once_with(
            'https://runner.example.invalid:8083', 'fixture-key', None
        )


if __name__ == '__main__':
    unittest.main()
