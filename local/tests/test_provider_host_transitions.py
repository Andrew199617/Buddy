"""Offline tests for provider actions during execution-host transitions.

Run from the repository root:
    python -B -m unittest discover -s local/tests -p test_provider_host_transitions.py -v
"""

import asyncio
import ast
import copy
import types
import time
import unittest
from unittest.mock import AsyncMock, patch

from test_subscription_machines import (
    ROOT,
    FakeCodexProvider,
    FakeProvider,
    FakeRemoteMachine,
    MemoryConfig,
    enabled_settings,
    load_service,
    machine_config
)

REVISION_A = 'fixture-revision-runner-a'
REVISION_B = 'fixture-revision-runner-b'


class ActionProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.start_hook = None
        self.logout_hook = None
        self.status_hook = None
        self.models_hook = None
        self.turn_hook = None
        self.cancel_hook = None

    async def cancel_login(self):
        await super().cancel_login()
        if self.cancel_hook:
            await self.cancel_hook()

    async def start_login(self, settings, machine, method):
        self.calls.append(('start_login', settings.machine_id, machine.id, method))
        if self.start_hook:
            await self.start_hook()
        return {'state': 'waiting'}

    async def submit_login_code(self, code):
        self.calls.append(('submit_login_code', code))
        return {'state': 'success'}

    def login_state(self):
        self.calls.append(('login_state',))
        return {'state': 'waiting'}

    async def logout(self, settings, machine):
        self.calls.append(('logout', settings.machine_id, machine.id))
        if self.logout_hook:
            await self.logout_hook()

    async def status(self, settings, machine):
        self.calls.append(('status', settings.machine_id, machine.id))
        if self.status_hook:
            return await self.status_hook()
        return {'installed': True, 'signed_in': True, 'api_billing': False}

    async def list_models(self, settings, machine):
        self.calls.append(('list_models', settings.machine_id, machine.id))
        if self.models_hook:
            return await self.models_hook()
        return []

    async def run_turn(self, turn):
        self.calls.append(('run_turn', turn.machine.id))
        if self.turn_hook:
            async for event in self.turn_hook(turn):
                yield event
        else:
            yield {'text': 'fixture response'}


class ActionCodexProvider(ActionProvider, FakeCodexProvider):
    pass


class TrackedProcess:
    def __init__(self):
        self.returncode = None
        self.kill_count = 0
        self.wait_count = 0
        self.read_started = asyncio.Event()
        self.finished = asyncio.Event()

    def kill(self):
        self.kill_count += 1
        self.returncode = -9
        self.finished.set()

    async def wait(self, timeout=None):
        self.wait_count += 1
        await self.finished.wait()
        return self.returncode

    async def read_text(self):
        self.read_started.set()
        await self.finished.wait()
        return None

    async def finish_output(self, timeout):
        pass

    def stderr_text(self):
        return ''


class StrictTrackedProcess(TrackedProcess):
    """Separate leader exit from confirmation that the whole process stopped."""

    def __init__(self):
        super().__init__()
        self.cleanup_confirmed = False
        self.stop_count = 0
        self.stop_entered = asyncio.Event()
        self.stop_release = asyncio.Event()
        self.stop_release.set()
        self.stop_failures = []

    async def kill_and_wait(self):
        self.stop_count += 1
        self.stop_entered.set()
        await self.stop_release.wait()
        if self.stop_failures:
            raise self.stop_failures.pop(0)
        self.kill()
        await self.wait()
        self.cleanup_confirmed = True


class ScheduledDisposalProcess(StrictTrackedProcess):
    """Model remote kill scheduling cleanup before the leader exits."""

    def __init__(self):
        super().__init__()
        self.disposal_started = False
        self._close_task = None

    def kill(self):
        if self._close_task is None:
            self.disposal_started = True
            self._close_task = asyncio.create_task(self._finish_disposal())

    async def _finish_disposal(self):
        self.stop_count += 1
        self.stop_entered.set()
        await self.stop_release.wait()
        if self.stop_failures:
            raise self.stop_failures.pop(0)
        TrackedProcess.kill(self)
        await self.wait()
        self.cleanup_confirmed = True

    async def kill_and_wait(self):
        self.kill()
        await self._close_task


class ProcessMachine(FakeRemoteMachine):
    def __init__(self, config):
        super().__init__(config['id'], config['name'], config['url'], config['key'])
        self.processes = []
        self.start_hook = None
        self.process_factory = TrackedProcess
        self.process_created = asyncio.Event()
        self.workspace_reads = 0

    async def default_workspace(self):
        self.workspace_reads += 1
        return 'fixture-default-workspace'

    async def chat_dir(self):
        return 'fixture-empty-chat-workspace'

    async def start_process(self, args, cwd, extra_env=None, temp_files=None):
        if self.start_hook:
            await self.start_hook()
        process = self.process_factory()
        self.processes.append(process)
        self.process_created.set()
        return process


async def fixture_failure_events(message):
    yield {'error': message}


async def fixture_collect_events(events, model_id):
    return [event async for event in events]


async def fixture_stream_events(events, model_id):
    async for event in events:
        yield event


class OfflineStreamingResponse:
    def __init__(self, body, media_type):
        self.body_iterator = body
        self.media_type = media_type


class OfflineForm:
    """Supply values for production form fields without importing Pydantic."""

    def __init__(self, **values):
        for name in self.__class__.__annotations__:
            setattr(self, name, values.get(name, getattr(self.__class__, name, None)))

    def model_dump(self):
        return {name: getattr(self, name) for name in self.__class__.__annotations__}


def load_router(service, subscription_error, http_exception):
    """Execute real endpoint bodies with fake dependency injection and services."""
    path = ROOT / 'backend' / 'open_webui' / 'routers' / 'subscriptions.py'
    source = ast.parse(path.read_text(encoding='utf-8'))
    nodes = []
    for node in source.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.decorator_list = []
            nodes.append(node)
    future_annotations = ast.ImportFrom(
        module='__future__', names=[ast.alias(name='annotations')], level=0
    )
    tree = ast.Module(body=[future_annotations, *nodes], type_ignores=[])
    ast.fix_missing_locations(tree)
    module = types.ModuleType('subscription_router_host_transitions_under_test')
    module.__dict__.update({
        'asyncio': asyncio,
        'BaseModel': OfflineForm,
        'Depends': lambda dependency: None,
        'get_admin_user': lambda: None,
        'HTTPException': http_exception,
        'status': types.SimpleNamespace(HTTP_400_BAD_REQUEST=400),
        'UVICORN_WORKERS': 1,
        'SubscriptionError': subscription_error,
        'service': service
    })
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


class ProviderHostTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [machine_config(), machine_config('runner-b', 'host-b')],
            'subscriptions.claude': enabled_settings('runner-a'),
            'subscriptions.codex': enabled_settings('runner-b')
        })
        self.service = load_service(self.config, ROOT / 'unused-offline-transition-data')
        self.providers = {'claude': ActionProvider(), 'codex': ActionCodexProvider()}
        self.service.PROVIDERS = self.providers
        self.service.failure_events = fixture_failure_events
        self.machines = {
            config['id']: ProcessMachine(config)
            for config in self.config.values['subscriptions.machines']
        }
        self.service._remote_machines = dict(self.machines)
        self.pending = []
        self.teardown_gates = []

    def launch(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.pending.append(task)
        return task

    def gate(self):
        event = asyncio.Event()
        self.teardown_gates.append(event)
        return event

    async def asyncTearDown(self):
        for gate in self.teardown_gates:
            gate.set()
        for task in self.pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.pending, return_exceptions=True)

    async def assert_conflict(self, operation):
        with self.assertRaises(self.service.HTTPException) as caught:
            await operation
        self.assertEqual(caught.exception.status_code, 409)

    async def assert_single_worker_rejection(self, operation):
        with self.assertRaises(self.service.SubscriptionError) as caught:
            await operation
        self.assertEqual(str(caught.exception), self.service.SINGLE_WORKER_MESSAGE)

    async def test_multi_worker_mutations_are_blocked_before_config_machine_and_provider_actions(self):
        self.service.UVICORN_WORKERS = 2
        with patch.object(self.config, 'get', AsyncMock()) as config_get:
            with patch.object(self.config, 'upsert', AsyncMock()) as config_upsert:
                with patch.object(self.service, 'verify_machine', AsyncMock()) as verify:
                    with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
                        await self.assert_single_worker_rejection(self.service.save_machine(
                            None, 'Fixture PC', 'https://fixture.example.invalid', 'fixture-key'
                        ))
                        await self.assert_single_worker_rejection(self.service.delete_machine('runner-a'))
                        await self.assert_single_worker_rejection(self.service.save_settings(
                            'claude', {'enable': False}, 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.start_login(
                            'claude', 'browser', 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.submit_login_code(
                            'claude', 'fixture-code', 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.cancel_login(
                            'claude', 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.logout(
                            'claude', 'runner-a', REVISION_A
                        ))
        config_get.assert_not_awaited()
        config_upsert.assert_not_awaited()
        verify.assert_not_awaited()
        machine_get.assert_not_awaited()
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))

    async def test_multi_worker_metadata_reads_report_limitation_without_provider_or_machine_access(self):
        self.service.UVICORN_WORKERS = 2
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        with patch.object(self.service.status_cache, 'get', AsyncMock()) as status_get:
            with patch.object(self.service.models_cache, 'get', AsyncMock()) as models_get:
                with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
                    description = await self.service.describe_provider('claude', fresh=True)
                    login = await self.service.login_state('claude', 'runner-a', REVISION_A)
                    machines = await self.service.describe_machines()
        status_get.assert_not_awaited()
        models_get.assert_not_awaited()
        machine_get.assert_not_awaited()
        self.assertFalse(description['status']['signed_in'])
        self.assertEqual(description['status']['message'], self.service.SINGLE_WORKER_MESSAGE)
        self.assertEqual(description['models'], [])
        self.assertEqual(description['default_workspace'], '')
        self.assertEqual(login, {'state': 'idle', 'message': self.service.SINGLE_WORKER_MESSAGE})
        self.assertEqual(len(machines), 3)
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_multi_worker_model_refresh_and_discovery_block_even_with_signed_in_cache(self):
        self.service.UVICORN_WORKERS = 2
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        status = await self.service._load_status('claude')
        self.assertFalse(status['signed_in'])
        self.assertEqual(status['message'], self.service.SINGLE_WORKER_MESSAGE)
        self.assertEqual(await self.service._load_models('claude'), [])
        self.assertEqual(await self.service._provider_models('claude'), [])
        self.assertEqual(await self.service.get_subscription_models(), [])
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_multi_worker_chat_and_streaming_chat_fail_without_provider_actions(self):
        self.service.UVICORN_WORKERS = 2
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        self.assertEqual(await self.fixture_chat(), [{'error': self.service.SINGLE_WORKER_MESSAGE}])
        response = await self.fixture_chat(stream=True)
        self.assertEqual(
            [event async for event in response.body_iterator],
            [{'error': self.service.SINGLE_WORKER_MESSAGE}]
        )
        self.assertTrue(all(not provider.calls for provider in self.providers.values()))
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_multi_worker_tracking_cannot_start_a_fresh_turn(self):
        self.service.UVICORN_WORKERS = 2
        turn = types.SimpleNamespace(
            settings=await self.service.get_settings('claude'),
            machine=self.service._tracking_machine('claude', self.machines['runner-a'])
        )
        result = [event async for event in self.service._tracked_turn('claude', self.providers['claude'], turn)]
        self.assertEqual(result, [{'error': self.service.SINGLE_WORKER_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_multi_worker_read_only_health_verification_remains_available(self):
        self.service.UVICORN_WORKERS = 2
        probe = ProcessMachine(machine_config())
        info = {'host': {'id': 'fixture-health-host'}}
        probe.info = AsyncMock(return_value=info)
        with patch.object(self.service, 'RemoteMachine', return_value=probe):
            self.assertEqual(await self.service.verify_machine('https://fixture.example.invalid', 'fixture-key'), info)
        probe.info.assert_awaited_once()
        self.assertEqual(probe.closed, 1)
        self.assertEqual(probe.processes, [])
        self.assertEqual(self.config.commits, [])

    async def fixture_chat(self, stream=False):
        async def passthrough_preset(request, payload, metadata, user):
            return payload['model'], payload

        self.service._apply_preset = passthrough_preset
        self.service.parse_messages = lambda messages: types.SimpleNamespace(messages=messages)
        self.service.collect_events = fixture_collect_events
        self.service.stream_events = fixture_stream_events
        self.service.StreamingResponse = OfflineStreamingResponse
        model_id = 'claude-code.fixture-model'
        return await self.service.generate_subscription_chat_completion(
            types.SimpleNamespace(state=types.SimpleNamespace()),
            {'model': model_id, 'messages': [{'role': 'user', 'content': 'Fixture message'}], 'stream': stream},
            types.SimpleNamespace(role='admin'),
            {model_id: {'subscription': {'provider': 'claude', 'model': 'fixture-model'}}}
        )

    def seed_old_signed_in_cache(self):
        self.service.status_cache._entries['claude'] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        model = types.SimpleNamespace(
            key='fixture-model', value='fixture-model', name='Fixture model', description='', efforts=[]
        )
        self.service.models_cache._entries['claude'] = self.service._CacheEntry([model], time.monotonic() + 60)

    async def test_enabled_legacy_remote_model_get_stays_empty_even_with_old_signed_in_cache(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        self.config.values['subscriptions.codex']['enable'] = False
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        self.assertEqual(await self.service._provider_models('claude'), [])
        self.assertEqual(await self.service.get_subscription_models(), [])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_legacy_remote_status_and_model_refresh_do_not_call_providers_or_create_handles(self):
        self.config.values['subscriptions.machines'][0]['revision'] = ''
        before = copy.deepcopy(self.config.values)
        status = await self.service._load_status('claude')
        self.assertFalse(status['signed_in'])
        self.assertEqual(status['message'], self.service.REVERIFY_MACHINE_MESSAGE)
        self.assertEqual(await self.service._load_models('claude'), [])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])
        self.assertNotIn('claude', self.service._tracked_machines)
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_legacy_remote_provider_description_is_read_only_and_skips_machine_and_caches(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        with patch.object(self.service.status_cache, 'get', AsyncMock()) as status_get:
            with patch.object(self.service.models_cache, 'get', AsyncMock()) as models_get:
                with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
                    description = await self.service.describe_provider('claude', fresh=True)
        status_get.assert_not_awaited()
        models_get.assert_not_awaited()
        machine_get.assert_not_awaited()
        self.assertFalse(description['status']['signed_in'])
        self.assertEqual(description['status']['message'], self.service.REVERIFY_MACHINE_MESSAGE)
        self.assertEqual(description['models'], [])
        self.assertEqual(description['default_workspace'], '')
        self.assertNotIn('revision', description['machine'])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].workspace_reads, 0)
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_legacy_remote_chat_is_blocked_before_provider_and_machine_access(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
            result = await self.fixture_chat()
        machine_get.assert_not_awaited()
        self.assertEqual(result, [{'error': self.service.REVERIFY_MACHINE_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_streaming_legacy_remote_chat_closes_with_error_without_starting_a_provider(self):
        self.config.values['subscriptions.machines'][0]['revision'] = ''
        self.seed_old_signed_in_cache()
        response = await self.fixture_chat(stream=True)
        events = [event async for event in response.body_iterator]
        self.assertEqual(events, [{'error': self.service.REVERIFY_MACHINE_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])
        self.assertEqual(self.config.commits, [])

    async def test_tracked_turn_cannot_start_on_an_unverified_remote_at_the_current_generation(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        turn = types.SimpleNamespace(
            settings=await self.service.get_settings('claude'),
            machine=self.service._tracking_machine('claude', self.machines['runner-a'])
        )
        result = [event async for event in self.service._tracked_turn('claude', self.providers['claude'], turn)]
        self.assertEqual(result, [{'error': self.service.REVERIFY_MACHINE_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_verifying_and_saving_legacy_remote_enables_model_discovery_and_chat(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('revision')
        self.assertEqual(await self.service._provider_models('claude'), [])
        self.assertEqual(await self.fixture_chat(), [{'error': self.service.REVERIFY_MACHINE_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(
                'runner-a', original['name'], original['url'], None, original['browser_url']
            )
        self.assertTrue(public['revision'])

        async def verified_status():
            wrapper = self.service._tracking_machine('claude', self.machines['runner-a'])
            await wrapper.start_process(['fixture-status'], 'fixture-cwd')
            return {'installed': True, 'signed_in': True, 'api_billing': False}

        async def verified_models():
            wrapper = self.service._tracking_machine('claude', self.machines['runner-a'])
            await wrapper.start_process(['fixture-models'], 'fixture-cwd')
            return [types.SimpleNamespace(
                key='fixture-model', value='fixture-model', name='Fixture model', description='', efforts=[]
            )]

        async def verified_turn(turn):
            await turn.machine.start_process(['fixture-chat'], 'fixture-cwd')
            yield {'text': 'fixture response'}

        self.providers['claude'].status_hook = verified_status
        self.providers['claude'].models_hook = verified_models
        self.providers['claude'].turn_hook = verified_turn
        discovered = await self.service._provider_models('claude')
        self.assertEqual([model['id'] for model in discovered], ['claude-code.fixture-model'])
        self.assertEqual(await self.fixture_chat(), [{'text': 'fixture response'}])
        self.assertEqual(len(self.machines['runner-a'].processes), 3)
        await self.service._tracking_machine('claude', self.machines['runner-a']).revoke()

    async def test_local_provider_discovery_keeps_legacy_compatibility_without_registry_revision(self):
        self.config.values['subscriptions.claude'] = enabled_settings('local')
        status = await self.service._load_status('claude')
        self.assertTrue(status['signed_in'])
        self.assertEqual(await self.service._load_models('claude'), [])
        self.assertIn(('status', 'local', 'local'), self.providers['claude'].calls)
        self.assertIn(('list_models', 'local', 'local'), self.providers['claude'].calls)
        self.assertEqual(self.config.commits, [])

    async def test_deleted_host_can_be_explicitly_replaced_then_enabled_with_the_new_remote_guard(self):
        await self.service.delete_machine('runner-a')
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.providers['claude'].calls.clear()
        replacement = await self.service.save_settings(
            'claude', enabled_settings('runner-b'), 'runner-a'
        )
        self.assertEqual(replacement.machine_id, 'runner-b')
        self.assertFalse(replacement.enable)
        self.assertEqual(replacement.access, 'chat')
        self.assertEqual(replacement.workspace, '')
        self.assertEqual(replacement.cli_path, '')
        self.assertEqual(self.machines['runner-b'].path_checks, [])
        self.assertEqual(self.providers['claude'].calls, ['cancel_login'])
        before = copy.deepcopy(self.config.values)
        commits = len(self.config.commits)
        await self.assert_conflict(self.service.save_settings('claude', {'enable': True}))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': True}, 'runner-b'))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': True}, 'runner-a', REVISION_A))
        self.assertEqual(self.config.values, before)
        self.assertEqual(len(self.config.commits), commits)
        enabled = await self.service.save_settings('claude', {'enable': True}, 'runner-b', REVISION_B)
        self.assertTrue(enabled.enable)
        self.assertEqual(enabled.access, 'chat')
        self.assertEqual(enabled.workspace, '')
        self.assertEqual(enabled.cli_path, '')
        self.assertEqual(self.machines['runner-a'].closed, 1)
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))

    async def test_new_registration_cannot_reuse_a_deleted_selected_host_id(self):
        self.config.values['subscriptions.machines'][0]['name'] = 'Runner A'
        await self.service.delete_machine('runner-a')
        info = {'host': {'id': 'replacement-host', 'name': 'Different PC'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            saved = await self.service.save_machine(
                None, 'Runner A', 'https://replacement.example.invalid:8083', 'fixture-replacement-key'
            )
        self.assertEqual(saved['id'], 'runner-a-2')
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.providers['claude'].calls.clear()
        with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
            status = await self.service._load_status('claude')
            self.assertFalse(status['signed_in'])
            self.assertEqual(await self.service._load_models('claude'), [])
            self.assertEqual(await self.service._provider_models('claude'), [])
            description = await self.service.describe_provider('claude', fresh=True)
        machine_get.assert_not_awaited()
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(description['machine']['id'], 'runner-a')
        await self.service.save_settings('claude', enabled_settings(saved['id']), 'runner-a')
        current = self.config.values['subscriptions.claude']
        self.assertEqual(current['machine_id'], saved['id'])
        self.assertFalse(current['enable'])
        self.assertEqual((current['access'], current['workspace'], current['cli_path']), ('chat', '', ''))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': True}, 'runner-a'))
        await self.service.save_settings('claude', {'enable': True}, saved['id'], saved['revision'])
        self.providers['claude'].calls.clear()
        status = await self.service._load_status('claude')
        self.assertTrue(status['signed_in'])
        self.assertIn(('status', saved['id'], saved['id']), self.providers['claude'].calls)

    async def test_new_registration_still_avoids_current_registry_id_collisions(self):
        before = copy.deepcopy(self.config.values['subscriptions.machines'])
        info = {'host': {'id': 'additional-host', 'name': 'Additional PC'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            saved = await self.service.save_machine(
                None, 'Runner B', 'https://additional.example.invalid:8083', 'fixture-additional-key'
            )
        self.assertEqual(saved['id'], 'runner-b-2')
        self.assertEqual(self.config.values['subscriptions.machines'][:2], before)

    async def test_new_registration_reserves_retained_cleanup_owner_host_ids(self):
        old_machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', old_machine)
        wrapper.valid = False
        wrapper.startup_cleanup_error = self.service.StartupCleanupUnconfirmedError('Fixture retained old startup')
        self.config.values['subscriptions.claude'] = enabled_settings('runner-b')
        self.config.values['subscriptions.machines'] = [machine_config('runner-b', 'host-b')]
        info = {'host': {'id': 'additional-host', 'name': 'Additional PC'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            saved = await self.service.save_machine(
                None, 'Runner A', 'https://additional.example.invalid:8083', 'fixture-additional-key'
            )
        self.assertEqual(saved['id'], 'runner-a-2')
        self.assertIs(self.service._tracked_machines['claude'], wrapper)
        self.assertEqual(wrapper.id, 'runner-a')
        self.assertIsNotNone(wrapper.startup_cleanup_error)

    async def test_deleted_host_can_be_explicitly_replaced_with_local_without_restoring_old_authority(self):
        await self.service.delete_machine('runner-a')
        replacement = await self.service.save_settings('claude', {
            **enabled_settings('local'), 'expected_machine_id': 'runner-a',
            'expected_machine_revision': REVISION_A
        })
        self.assertEqual(replacement.machine_id, 'local')
        self.assertFalse(replacement.enable)
        self.assertEqual(replacement.access, 'chat')
        self.assertEqual(replacement.workspace, '')
        self.assertEqual(replacement.cli_path, '')
        self.assertNotIn('expected_machine_id', self.config.values['subscriptions.claude'])
        self.assertNotIn('expected_machine_revision', self.config.values['subscriptions.claude'])
        await self.assert_conflict(self.service.save_settings('claude', {'enable': True}, 'runner-a'))
        enabled = await self.service.save_settings('claude', {'enable': True}, 'local')
        self.assertTrue(enabled.enable)
        self.assertEqual(enabled.access, 'chat')
        self.assertEqual(enabled.workspace, '')
        self.assertEqual(enabled.cli_path, '')
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))

    async def test_deleted_host_recovery_rejects_missing_wrong_or_unchanged_selection_and_auth_actions(self):
        await self.service.delete_machine('runner-a')
        self.providers['claude'].calls.clear()
        before = copy.deepcopy(self.config.values)
        commits = len(self.config.commits)
        with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
            await self.assert_conflict(self.service.save_settings('claude', {'machine_id': 'runner-b'}))
            await self.assert_conflict(self.service.save_settings('claude', {'machine_id': 'runner-b'}, 'runner-b'))
            await self.assert_conflict(self.service.save_settings('claude', {'machine_id': 'runner-a'}, 'runner-a'))
            await self.assert_conflict(self.service.save_settings('claude', {'enable': True}, 'runner-a'))
            await self.assert_conflict(self.service.start_login('claude', 'browser', 'runner-a'))
            await self.assert_conflict(self.service.submit_login_code('claude', 'fixture-code', 'runner-a'))
            await self.assert_conflict(self.service.cancel_login('claude', 'runner-a'))
            await self.assert_conflict(self.service.logout('claude', 'runner-a'))
        machine_get.assert_not_awaited()
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings('claude', {'machine_id': 'missing-target'}, 'runner-a')
        self.assertEqual(self.config.values, before)
        self.assertEqual(len(self.config.commits), commits)
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_existing_legacy_host_without_revision_cannot_use_deleted_host_recovery(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        before = copy.deepcopy(self.config.values)
        await self.assert_conflict(self.service.save_settings('claude', {'machine_id': 'runner-b'}, 'runner-a'))
        await self.assert_conflict(self.service.save_settings('claude', {'machine_id': 'local'}, 'runner-a'))
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_deleted_host_recovery_cannot_bypass_retained_unknown_startup_cleanup_debt(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)
        failure = self.service.StartupCleanupUnconfirmedError('Fixture unknown process remains on deleted host')

        async def unknown_startup():
            raise failure

        machine.start_hook = unknown_startup
        with self.assertRaises(self.service.StartupCleanupUnconfirmedError):
            await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine('runner-a')
        self.assertFalse(any(config['id'] == 'runner-a' for config in self.config.values['subscriptions.machines']))
        before = copy.deepcopy(self.config.values)
        commits = len(self.config.commits)
        for target in ('runner-b', 'local'):
            with self.subTest(target=target):
                with self.assertRaises(self.service.SubscriptionError) as caught:
                    await self.service.save_settings('claude', enabled_settings(target), 'runner-a')
                self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, str(caught.exception))
                self.assertIn(machine.id, str(caught.exception))
                self.assertEqual(self.config.values, before)
                self.assertEqual(len(self.config.commits), commits)
                self.assertIs(self.service._tracked_machines['claude'], wrapper)
                self.assertIs(wrapper.startup_cleanup_error, failure)
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])

    async def test_remote_mutations_without_an_expected_host_fail_before_provider_calls(self):
        before = copy.deepcopy(self.config.values)
        await self.assert_conflict(self.service.start_login('claude', 'browser'))
        await self.assert_conflict(self.service.submit_login_code('claude', 'fixture-code'))
        await self.assert_conflict(self.service.cancel_login('claude'))
        await self.assert_conflict(self.service.logout('claude'))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': False}))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_stale_host_mutations_and_reads_fail_before_provider_calls(self):
        await self.assert_conflict(self.service.start_login('claude', 'browser', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.submit_login_code('claude', 'fixture-code', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.cancel_login('claude', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.logout('claude', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.login_state('claude', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': False}, 'runner-b', REVISION_A))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])

    async def test_remote_mutations_with_matching_id_but_no_revision_are_rejected(self):
        await self.assert_conflict(self.service.start_login('claude', 'browser', 'runner-a'))
        await self.assert_conflict(self.service.submit_login_code('claude', 'fixture-code', 'runner-a'))
        await self.assert_conflict(self.service.cancel_login('claude', 'runner-a'))
        await self.assert_conflict(self.service.logout('claude', 'runner-a'))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': False}, 'runner-a'))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])

    async def test_remote_mutations_with_a_stale_revision_are_rejected(self):
        await self.assert_conflict(self.service.start_login('claude', 'browser', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.submit_login_code('claude', 'fixture-code', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.cancel_login('claude', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.logout('claude', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.login_state('claude', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': False}, 'runner-a', 'obsolete-revision'))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])

    async def test_replacing_backend_under_the_same_machine_id_rejects_the_old_revision(self):
        original = self.config.values['subscriptions.machines'][0]
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': {'id': 'replacement-host'}})):
            public = await self.service.save_machine(
                'runner-a', original['name'], original['url'], None, original['browser_url']
            )
        self.assertEqual(public['id'], 'runner-a')
        self.assertIsInstance(public['revision'], str)
        self.assertNotEqual(public['revision'], REVISION_A)
        before = copy.deepcopy(self.config.values)
        previous_commits = len(self.config.commits)
        self.providers['claude'].calls.clear()
        await self.assert_conflict(self.service.start_login('claude', 'browser', 'runner-a', REVISION_A))
        await self.assert_conflict(self.service.logout('claude', 'runner-a', REVISION_A))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': True}, 'runner-a', REVISION_A))
        self.assertEqual(self.config.values, before)
        self.assertEqual(len(self.config.commits), previous_commits)
        self.assertEqual(self.providers['claude'].calls, [])
        await self.service.save_settings('claude', {'enable': True}, 'runner-a', public['revision'])
        self.assertTrue(self.config.values['subscriptions.claude']['enable'])

    async def test_legacy_machine_without_revision_requires_verification_before_mutation(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('revision')
        await self.assert_conflict(self.service.start_login('claude', 'browser', 'runner-a', REVISION_A))
        await self.assert_conflict(self.service.save_settings('claude', {'enable': False}, 'runner-a', REVISION_A))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(
                'runner-a', original['name'], original['url'], None, original['browser_url']
            )
        self.assertIsInstance(public['revision'], str)
        self.assertTrue(public['revision'])
        await self.service.start_login('claude', 'browser', 'runner-a', public['revision'])
        self.assertIn(('start_login', 'runner-a', 'runner-a', 'browser'), self.providers['claude'].calls)

    async def test_remote_reads_allow_an_omitted_host_but_matching_mutations_are_explicit(self):
        self.assertEqual(await self.service.login_state('claude'), {'state': 'waiting'})
        await self.service.start_login('claude', 'device', 'runner-a', REVISION_A)
        await self.service.submit_login_code('claude', 'fixture-code', 'runner-a', REVISION_A)
        self.assertIn(('start_login', 'runner-a', 'runner-a', 'device'), self.providers['claude'].calls)
        self.assertIn(('submit_login_code', 'fixture-code'), self.providers['claude'].calls)

    async def test_local_legacy_actions_can_omit_the_expected_host(self):
        self.config.values['subscriptions.claude'] = enabled_settings('local')
        await self.service.start_login('claude', 'browser')
        await self.service.submit_login_code('claude', 'fixture-code')
        await self.service.cancel_login('claude')
        await self.service.logout('claude')
        await self.service.save_settings('claude', {'enable': False})
        self.assertIn(('start_login', 'local', 'local', 'browser'), self.providers['claude'].calls)
        self.assertIn(('logout', 'local', 'local'), self.providers['claude'].calls)
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])

    async def test_settings_accept_the_expected_host_field_without_persisting_it(self):
        await self.service.save_settings('claude', {
            'expected_machine_id': 'runner-a', 'expected_machine_revision': REVISION_A, 'access': 'read'
        })
        saved = self.config.values['subscriptions.claude']
        self.assertEqual(saved['access'], 'read')
        self.assertNotIn('expected_machine_id', saved)
        self.assertNotIn('expected_machine_revision', saved)

    async def test_login_config_and_queued_logout_serialize_and_recheck_host(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def block_login():
            entered.set()
            await release.wait()

        self.providers['claude'].start_hook = block_login
        login = self.launch(self.service.start_login('claude', 'browser', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        transition = self.launch(self.service.save_settings('claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A))
        logout = self.launch(self.service.logout('claude', 'runner-a', REVISION_A))
        await asyncio.sleep(0)
        self.assertFalse(transition.done())
        self.assertFalse(logout.done())
        self.assertEqual(self.config.commits, [])
        self.assertNotIn(('logout', 'runner-a', 'runner-a'), self.providers['claude'].calls)
        release.set()
        await asyncio.wait_for(login, 1)
        await asyncio.wait_for(transition, 1)
        await self.assert_conflict(asyncio.wait_for(logout, 1))
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-b')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.assertFalse(any(isinstance(call, tuple) and call[0] == 'logout' for call in self.providers['claude'].calls))

    async def test_concurrent_config_updates_preserve_both_changes_without_lost_writes(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        original_upsert = self.config.upsert
        first = True

        async def blocked_first_upsert(updates):
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            await original_upsert(updates)

        with patch.object(self.config, 'upsert', blocked_first_upsert):
            access = self.launch(self.service.save_settings('claude', {'access': 'read'}, 'runner-a', REVISION_A))
            await asyncio.wait_for(entered.wait(), 1)
            disable = self.launch(self.service.save_settings('claude', {'enable': False}, 'runner-a', REVISION_A))
            await asyncio.sleep(0)
            self.assertFalse(disable.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(access, disable), 1)
        self.assertEqual(self.config.values['subscriptions.claude']['access'], 'read')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.assertEqual(len(self.config.commits), 2)

    async def test_repeated_mutation_cancellation_waits_for_cleanup_before_releasing_its_lock(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_cleanup():
            entered.set()
            await release.wait()

        self.providers['claude'].cancel_hook = slow_cleanup
        mutation = self.launch(self.service.save_settings('claude', {'access': 'read'}, 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        mutation.cancel()
        queued = self.launch(self.service.save_settings('claude', {'enable': False}, 'runner-a', REVISION_A))
        await asyncio.sleep(0)
        mutation.cancel()
        await asyncio.sleep(0)
        self.assertFalse(mutation.done())
        self.assertFalse(queued.done())
        self.assertTrue(self.service._provider_lock('claude').locked())
        self.providers['claude'].cancel_hook = None
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(mutation, 1)
        await asyncio.wait_for(queued, 1)
        self.assertEqual(self.config.values['subscriptions.claude']['access'], 'read')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])

    async def assert_cleanup_failure_disables_before_unlock(self, cancel_caller=False, repeat_cancel=False):
        entered = asyncio.Event()
        release = asyncio.Event()
        observed = []

        async def failed_cleanup():
            entered.set()
            await release.wait()
            raise self.service.SubscriptionError('Fixture cleanup could not confirm termination')

        async def observe_after_lock():
            async with self.service._provider_lock('claude'):
                observed.append(copy.deepcopy(self.config.values['subscriptions.claude']))

        self.providers['claude'].cancel_hook = failed_cleanup
        mutation = self.launch(self.service.save_settings('claude', {'access': 'read'}, 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        if cancel_caller:
            mutation.cancel()
        observer = self.launch(observe_after_lock())
        await asyncio.sleep(0)
        if repeat_cancel:
            mutation.cancel()
            await asyncio.sleep(0)
            self.assertFalse(mutation.done())
            self.assertTrue(self.service._provider_lock('claude').locked())
        self.assertEqual(observed, [])
        release.set()
        result = await asyncio.wait_for(asyncio.gather(mutation, observer, return_exceptions=True), 1)
        self.assertIsInstance(result[0], BaseException)
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]['enable'])
        self.assertEqual(observed[0]['access'], 'chat')
        self.assertEqual(observed[0]['workspace'], '')
        self.assertEqual(observed[0]['cli_path'], '')
        self.assertEqual(observed[0]['machine_id'], 'runner-a')
        self.assertEqual(len(self.config.commits), 2)

    async def test_failed_cleanup_disables_access_before_the_provider_lock_is_released(self):
        await self.assert_cleanup_failure_disables_before_unlock()

    async def test_failed_cleanup_also_disables_access_when_the_mutation_caller_is_repeatedly_cancelled(self):
        await self.assert_cleanup_failure_disables_before_unlock(cancel_caller=True, repeat_cancel=True)

    async def test_deletion_cleans_every_affected_provider_and_closes_host_when_one_cleanup_fails(self):
        self.config.values['subscriptions.codex'] = enabled_settings('runner-a')
        machine = self.machines['runner-a']
        claude_wrapper = self.service._tracking_machine('claude', machine)
        codex_wrapper = self.service._tracking_machine('codex', machine)
        claude_process = await claude_wrapper.start_process(['fixture-claude'], 'fixture-cwd')
        codex_process = await codex_wrapper.start_process(['fixture-codex'], 'fixture-cwd')

        async def failed_cleanup():
            raise self.service.SubscriptionError('Fixture first-provider cleanup failed')

        self.providers['claude'].cancel_hook = failed_cleanup
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine('runner-a')
        for provider_id in ('claude', 'codex'):
            settings = self.config.values[f'subscriptions.{provider_id}']
            self.assertFalse(settings['enable'])
            self.assertEqual(settings['access'], 'chat')
            self.assertEqual(settings['workspace'], '')
            self.assertEqual(settings['cli_path'], '')
            self.assertEqual(settings['machine_id'], 'runner-a')
            self.assertIn('cancel_login', self.providers[provider_id].calls)
        self.assertIn('stop', self.providers['codex'].calls)
        self.assertGreaterEqual(claude_process.kill_count, 1)
        self.assertGreaterEqual(codex_process.kill_count, 1)
        self.assertEqual(machine.closed, 1)
        self.assertNotIn('runner-a', self.service._remote_machines)
        self.assertEqual(self.machines['runner-b'].closed, 0)
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.get_machine('runner-a')

    async def test_logout_holds_its_provider_lock_until_the_action_finishes(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def block_logout():
            entered.set()
            await release.wait()

        self.providers['claude'].logout_hook = block_logout
        logout = self.launch(self.service.logout('claude', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        config = self.launch(self.service.save_settings('claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A))
        await asyncio.sleep(0)
        self.assertFalse(config.done())
        self.assertEqual(self.config.commits, [])
        release.set()
        await asyncio.wait_for(asyncio.gather(logout, config), 1)
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-b')

    async def test_another_provider_can_act_while_one_provider_is_busy(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def block_login():
            entered.set()
            await release.wait()

        self.providers['claude'].start_hook = block_login
        blocked = self.launch(self.service.start_login('claude', 'browser', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.service.start_login('codex', 'device', 'runner-b', REVISION_B), 1)
        self.assertFalse(blocked.done())
        self.assertIn(('start_login', 'runner-b', 'runner-b', 'device'), self.providers['codex'].calls)
        release.set()
        await asyncio.wait_for(blocked, 1)

    async def test_same_machine_wrapper_is_reused_until_quiescence_and_old_handle_is_revoked(self):
        machine = self.machines['runner-a']
        first = self.service._tracking_machine('claude', machine)
        self.assertIs(first, self.service._tracking_machine('claude', machine))
        process = await first.start_process(['fixture-cli'], 'fixture-cwd')
        async with self.service._provider_lock('claude'):
            await self.service._quiesce_provider('claude')
        self.assertGreaterEqual(process.kill_count, 1)
        self.assertGreaterEqual(process.wait_count, 1)
        with self.assertRaises(self.service.SubscriptionError):
            await first.start_process(['fixture-cli'], 'fixture-cwd')
        replacement = self.service._tracking_machine('claude', machine)
        self.assertIsNot(replacement, first)
        self.assertEqual(len(machine.processes), 1)

    async def test_machine_rename_keeps_the_underlying_machine_wrapper_and_process(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)
        process = await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        original = self.config.values['subscriptions.machines'][0]
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            await self.service.save_machine(
                'runner-a', 'Renamed fixture PC', original['url'], None,
                browser_url='https://renamed-browser.example.invalid:9443'
            )
        looked_up = await self.service.get_machine('runner-a')
        self.assertIs(looked_up, machine)
        self.assertIs(self.service._tracking_machine('claude', looked_up), wrapper)
        self.assertEqual(machine.name, 'Renamed fixture PC')
        self.assertEqual(machine.closed, 0)
        self.assertEqual(process.kill_count, 0)
        self.assertEqual(self.providers['claude'].calls, [])
        await wrapper.revoke()

    async def test_noop_settings_save_preserves_the_running_handle_and_generation(self):
        wrapper = self.service._tracking_machine('claude', self.machines['runner-a'])
        process = await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        generation = self.service._provider_generations.get('claude', 0)
        await self.service.save_settings('claude', {}, 'runner-a', REVISION_A)
        self.assertIs(self.service._tracking_machine('claude', self.machines['runner-a']), wrapper)
        self.assertEqual(self.service._provider_generations.get('claude', 0), generation)
        self.assertEqual(process.kill_count, 0)
        self.assertEqual(self.providers['claude'].calls, [])
        await wrapper.revoke()

    async def test_late_process_creation_on_a_revoked_wrapper_is_stopped_immediately(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        machine = self.machines['runner-a']

        async def block_start():
            entered.set()
            await release.wait()

        machine.start_hook = block_start
        wrapper = self.service._tracking_machine('claude', machine)
        starting = self.launch(wrapper.start_process(['fixture-cli'], 'fixture-cwd'))
        await asyncio.wait_for(entered.wait(), 1)
        revoking = self.launch(wrapper.revoke())
        await asyncio.sleep(0)
        self.assertFalse(revoking.done())
        self.assertFalse(wrapper.valid)
        self.assertEqual(len(wrapper.startups), 1)
        self.assertEqual(machine.processes, [])
        release.set()
        await asyncio.wait_for(revoking, 1)
        with self.assertRaises(self.service.SubscriptionError):
            await asyncio.wait_for(starting, 1)
        self.assertEqual(len(machine.processes), 1)
        self.assertGreaterEqual(machine.processes[0].kill_count, 1)
        self.assertGreaterEqual(machine.processes[0].wait_count, 1)

    async def test_cancelled_startup_retains_identity_and_strict_cleanup_despite_repeated_cancellation(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)
        entered = asyncio.Event()
        release_start = self.gate()
        startup_cancelled = asyncio.Event()
        process = StrictTrackedProcess()
        process.stop_release = self.gate()
        machine.process_factory = lambda: process

        async def delayed_identity():
            entered.set()
            try:
                await release_start.wait()
            except asyncio.CancelledError:
                startup_cancelled.set()
                raise

        machine.start_hook = delayed_identity
        starting = self.launch(wrapper.start_process(['fixture-cli'], 'fixture-cwd'))
        await asyncio.wait_for(entered.wait(), 1)
        starting.cancel()
        await asyncio.sleep(0)
        starting.cancel()
        await asyncio.sleep(0)
        self.assertFalse(starting.done())
        self.assertFalse(startup_cancelled.is_set())
        self.assertEqual(len(wrapper.startups), 1)
        self.assertTrue(all(not task.cancelled() for task in wrapper.startups))
        release_start.set()
        await asyncio.wait_for(process.stop_entered.wait(), 1)
        self.assertFalse(starting.done())
        self.assertIn(process, wrapper.processes)
        starting.cancel()
        await asyncio.sleep(0)
        self.assertFalse(starting.done())
        process.stop_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(starting, 1)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(process.stop_count, 1)
        self.assertEqual(wrapper.processes, set())
        self.assertEqual(wrapper.startups, set())
        self.assertFalse(startup_cancelled.is_set())

    async def test_typed_unknown_startup_debt_blocks_status_host_replacement_models_chat_and_readiness(self):
        machine = self.machines['runner-a']
        failure = self.service.StartupCleanupUnconfirmedError('Fixture startup has no confirmed process identity')

        async def unknown_startup():
            raise failure

        async def start_from_status():
            wrapper = self.service._tracked_machines['claude']
            await wrapper.start_process(['fixture-status'], 'fixture-cwd')

        machine.start_hook = unknown_startup
        self.providers['claude'].status_hook = start_from_status
        status = await self.service._load_status('claude')
        self.assertFalse(status['signed_in'])
        wrapper = self.service._tracked_machines['claude']
        self.assertIs(wrapper.startup_cleanup_error, failure)
        self.assertEqual(wrapper.processes, set())
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings('claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A)
        self.assertIs(self.service._tracked_machines['claude'], wrapper)
        self.assertFalse(wrapper.valid)
        settings = self.config.values['subscriptions.claude']
        self.assertEqual(settings['machine_id'], 'runner-b')
        self.assertFalse(settings['enable'])
        self.assertEqual(settings['access'], 'chat')
        self.assertEqual(settings['workspace'], '')
        self.assertEqual(settings['cli_path'], '')
        with self.assertRaises(self.service.SubscriptionError) as blocked:
            self.service._tracking_machine('claude', self.machines['runner-b'])
        self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, str(blocked.exception))
        self.assertIn(machine.id, str(blocked.exception))
        self.assertIn(machine.name, str(blocked.exception))
        self.assertNotIn(machine.url, str(blocked.exception))
        self.assertNotIn(machine.key, str(blocked.exception))
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-next'], 'fixture-cwd')
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.revoke()
        self.assertIs(wrapper.startup_cleanup_error, failure)

        # Even stale persisted enable/cache values cannot bypass retained debt.
        self.config.values['subscriptions.claude'] = enabled_settings('runner-b')
        self.config.values['subscriptions.codex']['enable'] = False
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        self.providers['claude'].calls.clear()
        with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
            status = await self.service._load_status('claude')
            self.assertFalse(status['signed_in'])
            self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, status['message'])
            self.assertEqual(await self.service._load_models('claude'), [])
            self.assertEqual(await self.service._provider_models('claude'), [])
            chat = await self.fixture_chat()
            self.assertEqual(len(chat), 1)
            self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, chat[0]['error'])
            with patch.object(self.service.status_cache, 'get', AsyncMock()) as status_get:
                with patch.object(self.service.models_cache, 'get', AsyncMock()) as models_get:
                    description = await self.service.describe_provider('claude', fresh=True)
                    login = await self.service.login_state('claude', 'runner-b', REVISION_B)
            status_get.assert_not_awaited()
            models_get.assert_not_awaited()
        machine_get.assert_not_awaited()
        self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, description['status']['message'])
        self.assertIn(machine.id, description['status']['message'])
        self.assertIn(machine.name, description['status']['message'])
        self.assertEqual(description['machine']['id'], 'runner-b')
        self.assertEqual(description['models'], [])
        self.assertEqual(description['default_workspace'], '')
        self.assertEqual(description['login'], {'state': 'idle'})
        self.assertEqual(login['state'], 'idle')
        self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, login['message'])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))
        self.assertEqual(self.config.values, before)

    async def test_ordinary_preconnect_failure_can_retry_without_poisoning_the_wrapper(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)

        async def disconnected():
            raise self.service.SubscriptionError('Fixture connection failed before startup')

        machine.start_hook = disconnected
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        self.assertIsNone(wrapper.startup_cleanup_error)
        self.assertIsNone(wrapper.cleanup_error)
        self.assertEqual(wrapper.startups, set())
        self.assertEqual(wrapper.processes, set())
        self.assertIs(self.service._tracking_machine('claude', machine), wrapper)
        machine.start_hook = None
        process = await wrapper.start_process(['fixture-retry'], 'fixture-cwd')
        self.assertIn(process, wrapper.processes)
        await wrapper.revoke()
        self.assertGreaterEqual(process.kill_count, 1)

    async def test_late_handle_failed_stop_is_retained_until_cleanup_retry_succeeds(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)
        entered = asyncio.Event()
        release_start = self.gate()
        process = StrictTrackedProcess()
        process.stop_failures = [self.service.SubscriptionError('Fixture strict stop failed')]
        machine.process_factory = lambda: process

        async def delayed_identity():
            entered.set()
            await release_start.wait()

        machine.start_hook = delayed_identity
        starting = self.launch(wrapper.start_process(['fixture-cli'], 'fixture-cwd'))
        await asyncio.wait_for(entered.wait(), 1)
        revoking = self.launch(wrapper.revoke())
        await asyncio.sleep(0)
        release_start.set()
        outcomes = await asyncio.wait_for(asyncio.gather(starting, revoking, return_exceptions=True), 1)
        self.assertTrue(all(isinstance(outcome, self.service.SubscriptionError) for outcome in outcomes))
        self.assertIn(process, wrapper.processes)
        self.assertIsNotNone(wrapper.cleanup_error)
        self.assertFalse(process.cleanup_confirmed)
        with self.assertRaises(self.service.SubscriptionError):
            self.service._tracking_machine('claude', self.machines['runner-b'])
        async with self.service._provider_lock('claude'):
            await self.service._quiesce_provider('claude')
        self.assertEqual(process.stop_count, 2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(wrapper.processes, set())
        self.assertNotIn('claude', self.service._tracked_machines)

    async def test_failed_existing_handle_revoke_retains_wrapper_and_disables_access_until_retry(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)
        process = StrictTrackedProcess()
        process.stop_failures = [self.service.SubscriptionError('Fixture strict stop failed')]
        machine.process_factory = lambda: process
        await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings('claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A)
        self.assertIs(self.service._tracked_machines['claude'], wrapper)
        self.assertIn(process, wrapper.processes)
        self.assertFalse(wrapper.valid)
        self.assertIsNotNone(wrapper.cleanup_error)
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.assertEqual(self.config.values['subscriptions.claude']['access'], 'chat')
        with self.assertRaises(self.service.SubscriptionError):
            self.service._tracking_machine('claude', self.machines['runner-b'])
        async with self.service._provider_lock('claude'):
            await self.service._quiesce_provider('claude')
        self.assertEqual(process.stop_count, 2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(wrapper.processes, set())
        self.assertIsNone(wrapper.cleanup_error)
        replacement = self.service._tracking_machine('claude', self.machines['runner-b'])
        self.assertIsNot(replacement, wrapper)
        self.assertTrue(replacement.valid)

    async def test_exited_leader_with_unconfirmed_cleanup_blocks_next_start_and_retains_handle(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine('claude', machine)
        process = StrictTrackedProcess()
        machine.process_factory = lambda: process
        await wrapper.start_process(['fixture-first'], 'fixture-cwd')
        process.returncode = 0
        process.finished.set()
        process.stop_failures = [self.service.SubscriptionError('Fixture descendants remain alive')]
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-second'], 'fixture-cwd')
        self.assertEqual(len(machine.processes), 1)
        self.assertIn(process, wrapper.processes)
        self.assertFalse(process.cleanup_confirmed)
        self.assertIsNotNone(wrapper.cleanup_error)
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-third'], 'fixture-cwd')
        self.assertEqual(process.stop_count, 1)
        await wrapper.revoke()
        self.assertEqual(process.stop_count, 2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(wrapper.processes, set())
        self.assertIsNone(wrapper.cleanup_error)

    async def test_cleanup_confirmed_handle_is_safely_pruned_before_next_start(self):
        machine = self.machines['runner-a']
        machine.process_factory = StrictTrackedProcess
        wrapper = self.service._tracking_machine('claude', machine)
        first = await wrapper.start_process(['fixture-first'], 'fixture-cwd')
        first.returncode = 0
        first.finished.set()
        first.cleanup_confirmed = True
        second = await wrapper.start_process(['fixture-second'], 'fixture-cwd')
        self.assertEqual(first.stop_count, 0)
        self.assertNotIn(first, wrapper.processes)
        self.assertIn(second, wrapper.processes)
        self.assertEqual(len(machine.processes), 2)
        await wrapper.revoke()
        self.assertEqual(second.stop_count, 1)
        self.assertTrue(second.cleanup_confirmed)

    async def test_pending_disposal_with_running_leader_delays_next_command_until_confirmed(self):
        machine = self.machines['runner-a']
        first = ScheduledDisposalProcess()
        first.stop_release = self.gate()
        machine.process_factory = lambda: first
        wrapper = self.service._tracking_machine('claude', machine)
        await wrapper.start_process(['fixture-first'], 'fixture-cwd')
        machine.process_factory = StrictTrackedProcess
        first.kill()
        self.pending.append(first._close_task)
        await asyncio.wait_for(first.stop_entered.wait(), 1)
        self.assertIsNone(first.returncode)
        next_command = self.launch(wrapper.start_process(['fixture-next'], 'fixture-cwd'))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(next_command.done())
        self.assertEqual(len(machine.processes), 1)
        self.assertIn(first, wrapper.processes)
        first.stop_release.set()
        second = await asyncio.wait_for(next_command, 1)
        self.assertTrue(first.cleanup_confirmed)
        self.assertNotIn(first, wrapper.processes)
        self.assertIn(second, wrapper.processes)
        self.assertEqual(len(machine.processes), 2)
        await wrapper.revoke()

    async def test_failed_disposal_with_running_leader_blocks_next_command_and_keeps_debt(self):
        machine = self.machines['runner-a']
        first = ScheduledDisposalProcess()
        first.stop_failures = [self.service.SubscriptionError('Fixture pending cleanup failed')]
        machine.process_factory = lambda: first
        wrapper = self.service._tracking_machine('claude', machine)
        await wrapper.start_process(['fixture-first'], 'fixture-cwd')
        machine.process_factory = StrictTrackedProcess
        first.kill()
        self.pending.append(first._close_task)
        await asyncio.wait_for(first.stop_entered.wait(), 1)
        self.assertIsNone(first.returncode)
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-next'], 'fixture-cwd')
        self.assertEqual(len(machine.processes), 1)
        self.assertIn(first, wrapper.processes)
        self.assertIsNotNone(wrapper.cleanup_error)
        self.assertFalse(first.cleanup_confirmed)
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-blocked'], 'fixture-cwd')
        self.assertEqual(len(machine.processes), 1)

    async def test_active_process_without_disposal_allows_another_concurrent_command(self):
        machine = self.machines['runner-a']
        machine.process_factory = ScheduledDisposalProcess
        wrapper = self.service._tracking_machine('claude', machine)
        first = await wrapper.start_process(['fixture-first'], 'fixture-cwd')
        second = await asyncio.wait_for(wrapper.start_process(['fixture-concurrent'], 'fixture-cwd'), 1)
        self.assertIsNone(first.returncode)
        self.assertFalse(first.disposal_started)
        self.assertEqual(first.stop_count, 0)
        self.assertEqual(len(machine.processes), 2)
        self.assertIn(first, wrapper.processes)
        self.assertIn(second, wrapper.processes)
        await wrapper.revoke()

    async def test_tracked_command_capture_stops_its_process_when_quiesced(self):
        wrapper = self.service._tracking_machine('claude', self.machines['runner-a'])
        command = self.launch(wrapper.run(['fixture-cli'], 'fixture-cwd', 5))
        await asyncio.wait_for(self.machines['runner-a'].process_created.wait(), 1)
        process = self.machines['runner-a'].processes[0]
        await asyncio.wait_for(process.read_started.wait(), 1)
        async with self.service._provider_lock('claude'):
            await self.service._quiesce_provider('claude')
        await asyncio.wait_for(command, 1)
        self.assertGreaterEqual(process.kill_count, 1)
        self.assertGreaterEqual(process.wait_count, 1)

    async def test_host_transition_cancels_active_turn_status_models_and_stops_old_processes(self):
        self.config.values['subscriptions.codex'] = enabled_settings('runner-a')
        provider = self.providers['codex']
        turn_started = asyncio.Event()
        turn_finished = asyncio.Event()
        status_started = asyncio.Event()
        status_finished = asyncio.Event()
        models_started = asyncio.Event()
        models_finished = asyncio.Event()
        never_release = asyncio.Event()

        async def turn_events(turn):
            await turn.machine.start_process(['fixture-cli'], 'fixture-cwd')
            turn_started.set()
            try:
                yield {'text': 'old-host partial response'}
                await never_release.wait()
            finally:
                turn_finished.set()

        async def old_status():
            status_started.set()
            try:
                await never_release.wait()
            finally:
                status_finished.set()

        async def old_models():
            models_started.set()
            try:
                await never_release.wait()
            finally:
                models_finished.set()

        provider.turn_hook = turn_events
        provider.status_hook = old_status
        provider.models_hook = old_models
        wrapper = self.service._tracking_machine('codex', self.machines['runner-a'])
        turn = types.SimpleNamespace(machine=wrapper, settings=await self.service.get_settings('codex'))

        async def consume_turn():
            return [event async for event in self.service._tracked_turn('codex', provider, turn)]

        active = self.launch(consume_turn())
        status = self.launch(self.service.status_cache.get('codex'))
        models = self.launch(self.service.models_cache.get('codex'))
        await asyncio.wait_for(asyncio.gather(turn_started.wait(), status_started.wait(), models_started.wait()), 1)
        await self.service.save_settings('codex', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A)
        results = await asyncio.wait_for(asyncio.gather(active, status, models, return_exceptions=True), 1)
        self.assertTrue(turn_finished.is_set())
        self.assertTrue(status_finished.is_set())
        self.assertTrue(models_finished.is_set())
        self.assertIsNone(results[1])
        self.assertIsNone(results[2])
        self.assertNotIn('codex', self.service.status_cache._entries)
        self.assertNotIn('codex', self.service.models_cache._entries)
        self.assertGreaterEqual(self.machines['runner-a'].processes[0].kill_count, 1)
        self.assertIn('cancel_login', provider.calls)
        self.assertIn('stop', provider.calls)
        self.assertGreater(self.service._provider_generations['codex'], 0)
        self.assertEqual(self.config.values['subscriptions.codex']['machine_id'], 'runner-b')
        self.assertFalse(self.config.values['subscriptions.codex']['enable'])

    async def test_stale_turn_iterator_never_calls_the_provider_after_generation_changes(self):
        provider = self.providers['claude']
        wrapper = self.service._tracking_machine('claude', self.machines['runner-a'])
        turn = types.SimpleNamespace(machine=wrapper, settings=await self.service.get_settings('claude'))
        old_generation = self.service._provider_generations.get('claude', 0)
        iterator = self.service._tracked_turn('claude', provider, turn, generation=old_generation)
        async with self.service._provider_lock('claude'):
            await self.service._quiesce_provider('claude')
        events = [event async for event in iterator]
        self.assertTrue(any('error' in event for event in events))
        self.assertFalse(any(isinstance(call, tuple) and call[0] == 'run_turn' for call in provider.calls))
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_cache_invalidation_cancels_shared_refresh_and_returns_no_stale_value(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        blocker = asyncio.Event()
        load_count = 0

        async def load(provider_id):
            nonlocal load_count
            load_count += 1
            if load_count == 1:
                entered.set()
                try:
                    await blocker.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return {'host': 'new-fixture-host'}

        cache = self.service.CachedLoader(60, load)
        pending = self.launch(cache.get('claude'))
        await asyncio.wait_for(entered.wait(), 1)
        cache.invalidate('claude')
        self.assertIsNone(await asyncio.wait_for(pending, 1))
        self.assertTrue(cancelled.is_set())
        self.assertNotIn('claude', cache._entries)
        self.assertEqual(await cache.get('claude'), {'host': 'new-fixture-host'})
        self.assertEqual(load_count, 2)

    async def test_cache_discards_old_host_values_even_if_the_loader_suppresses_cancellation(self):
        entered = asyncio.Event()
        blocker = asyncio.Event()

        async def old_load(provider_id):
            entered.set()
            try:
                await blocker.wait()
            except asyncio.CancelledError:
                return {'host': 'old-fixture-host'}

        cache = self.service.CachedLoader(60, old_load)
        pending = self.launch(cache.get('claude'))
        await asyncio.wait_for(entered.wait(), 1)
        cache.invalidate('claude')
        self.assertIsNone(await asyncio.wait_for(pending, 1))
        self.assertNotIn('claude', cache._entries)


class ProviderHostRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        production = load_service(MemoryConfig(), ROOT / 'unused-offline-router-data')
        self.production = production
        self.service = types.SimpleNamespace(
            save_settings=AsyncMock(),
            start_login=AsyncMock(return_value={'state': 'waiting'}),
            submit_login_code=AsyncMock(return_value={'state': 'success'}),
            cancel_login=AsyncMock(return_value={'state': 'idle'}),
            logout=AsyncMock(),
            login_state=AsyncMock(return_value={'state': 'waiting'}),
            describe_provider=AsyncMock(return_value={'id': 'claude'})
        )
        self.router = load_router(self.service, production.SubscriptionError, production.HTTPException)
        self.admin = types.SimpleNamespace(role='admin')

    async def test_config_form_forwards_both_host_guards_inside_the_settings_body(self):
        form = self.router.SubscriptionConfigForm(
            enable=False, expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )
        await self.router.update_subscription_config('claude', form, user=self.admin)
        self.service.save_settings.assert_awaited_once_with('claude', form.model_dump())
        payload = self.service.save_settings.await_args.args[1]
        self.assertEqual(payload['expected_machine_id'], 'runner-a')
        self.assertEqual(payload['expected_machine_revision'], REVISION_A)

    async def test_start_login_form_forwards_method_and_both_host_guards(self):
        form = self.router.LoginForm(
            method='device', expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )
        result = await self.router.start_subscription_login('claude', form, user=self.admin)
        self.assertEqual(result, {'state': 'waiting'})
        self.service.start_login.assert_awaited_once_with('claude', 'device', 'runner-a', REVISION_A)

    async def test_login_code_form_forwards_code_and_both_host_guards(self):
        form = self.router.LoginCodeForm(
            code='fixture-code', expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )
        await self.router.submit_subscription_login_code('claude', form, user=self.admin)
        self.service.submit_login_code.assert_awaited_once_with('claude', 'fixture-code', 'runner-a', REVISION_A)

    async def test_login_progress_query_forwards_both_host_guards(self):
        await self.router.get_subscription_login(
            'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.login_state.assert_awaited_once_with('claude', 'runner-a', REVISION_A)

    async def test_delete_login_query_forwards_both_host_guards_to_cancel_helper(self):
        await self.router.cancel_subscription_login(
            'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.cancel_login.assert_awaited_once_with('claude', 'runner-a', REVISION_A)

    async def test_post_logout_query_forwards_both_guards_to_action_and_description(self):
        await self.router.logout_subscription(
            'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.logout.assert_awaited_once_with('claude', 'runner-a', REVISION_A)
        self.service.describe_provider.assert_awaited_once_with(
            'claude', fresh=True, expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )

    async def test_provider_description_query_forwards_refresh_and_both_host_guards(self):
        await self.router.get_subscription(
            'claude', refresh=True, expected_machine_id='runner-a',
            expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.describe_provider.assert_awaited_once_with(
            'claude', fresh=True, expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )

    async def test_host_conflicts_propagate_without_becoming_bad_requests_or_running_followup(self):
        conflict = self.production.HTTPException(status_code=409, detail='Fixture host changed')
        self.service.logout.side_effect = conflict
        with self.assertRaises(self.production.HTTPException) as caught:
            await self.router.logout_subscription(
                'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
            )
        self.assertIs(caught.exception, conflict)
        self.service.describe_provider.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
