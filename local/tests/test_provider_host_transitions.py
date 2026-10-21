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
    OWNER,
    OWNER_USER,
    ROOT,
    FakeCodexProvider,
    FakeProvider,
    FakeRemoteMachine,
    MemoryConfig,
    enabled_settings,
    load_service,
    machine_config,
    owner_providers,
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
        self.status_settings = []

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
        self.status_settings.append(settings)
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


async def fixture_stream_events(events, model_id, emit_when_stopped=None):
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
        'get_verified_user': lambda: None,
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
        self.service._providers = owner_providers(self.service, self.providers)
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

    def on_plan(self, provider_id):
        """The provider on the computer its plan settings name now."""
        machine_id = self.config.values[f'subscriptions.{provider_id}']['machine_id']
        return self.service.ProviderRef(OWNER, provider_id).on(machine_id)

    def tracked_handle(self, provider_id):
        """The provider's handle, or None. These tests run each provider on one computer at a time."""
        handles = self.service._tracked_handles(self.service.ProviderRef(OWNER, provider_id))
        self.assertLessEqual(len(handles), 1)
        if handles:
            return handles[0]
        return None

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
                        await self.assert_single_worker_rejection(self.service.save_machine(OWNER, 
                            None, 'Fixture PC', 'https://fixture.example.invalid', 'fixture-key'
                        ))
                        await self.assert_single_worker_rejection(self.service.delete_machine(OWNER, 'runner-a'))
                        await self.assert_single_worker_rejection(self.service.save_settings(OWNER, 
                            'claude', {'enable': False}, 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.start_login(OWNER, 
                            'claude', 'browser', 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.submit_login_code(OWNER, 
                            'claude', 'fixture-code', 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.cancel_login(OWNER, 
                            'claude', 'runner-a', REVISION_A
                        ))
                        await self.assert_single_worker_rejection(self.service.logout(OWNER, 
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
                    description = await self.service.describe_provider(OWNER, 'claude', fresh=True)
                    login = await self.service.login_state(OWNER, 'claude', 'runner-a', REVISION_A)
                    machines = await self.service.describe_machines(OWNER, )
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
        status = await self.service._load_status(self.on_plan('claude'))
        self.assertFalse(status['signed_in'])
        self.assertEqual(status['message'], self.service.SINGLE_WORKER_MESSAGE)
        self.assertEqual(await self.service._load_models(self.on_plan('claude')), [])
        self.assertEqual(await self.service._provider_models(self.service.ProviderRef(OWNER, 'claude')), [])
        self.assertEqual(await self.service.get_user_subscription_models(OWNER_USER), [])
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
            settings=await self.service.get_settings(OWNER, 'claude'),
            machine=self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
        )
        result = [event async for event in self.service._tracked_turn(self.service.ProviderRef(OWNER, 'claude'), self.providers['claude'], turn)]
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

    async def fixture_chat(self, stream=False, metadata=None):
        async def passthrough_preset(request, payload, metadata, user, models):
            return payload['model'], payload, False

        self.service._apply_preset = passthrough_preset
        self.service.parse_messages = lambda messages: types.SimpleNamespace(messages=messages)
        self.service.collect_events = fixture_collect_events
        self.service.stream_events = fixture_stream_events
        self.service.StreamingResponse = OfflineStreamingResponse
        model_id = 'claude-code.fixture-model'
        form = {'model': model_id, 'messages': [{'role': 'user', 'content': 'Fixture message'}], 'stream': stream}
        if metadata is not None:
            form['metadata'] = metadata
        return await self.service.generate_subscription_chat_completion(
            types.SimpleNamespace(state=types.SimpleNamespace()),
            form,
            OWNER_USER,
            {model_id: {'subscription': {'provider': 'claude', 'model': 'fixture-model'}}}
        )

    async def test_channel_replies_run_chat_only_whatever_access_the_provider_allows(self):
        self.seed_old_signed_in_cache()
        observed = []

        async def record_turn(turn):
            observed.append((turn.access, turn.settings.access, turn.cwd))
            yield {'text': 'fixture response'}

        self.providers['claude'].turn_hook = record_turn
        self.service.Chats.add('fixture-chat', OWNER)
        await self.fixture_chat(metadata={'chat_id': 'channel:fixture-channel'})
        await self.fixture_chat(metadata={'chat_id': 'fixture-chat'})
        self.assertEqual(observed, [
            ('chat', 'chat', 'fixture-empty-chat-workspace'),
            ('full', 'full', 'C:\\fixture-project'),
        ])
        self.assertEqual(self.config.values['subscriptions.claude']['access'], 'full')

    def seed_old_signed_in_cache(self):
        self.service.status_cache._entries[self.on_plan('claude')] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        model = types.SimpleNamespace(
            key='fixture-model', value='fixture-model', name='Fixture model', description='', efforts=[],
            ultra=False,
        )
        self.service.models_cache._entries[self.on_plan('claude')] = self.service._CacheEntry([model], time.monotonic() + 60)

    async def test_chat_starts_only_when_the_status_shows_a_plan(self):
        expected_errors = {
            'Still checking the Claude sign-in': None,
            'Claude is not signed in': {'installed': True, 'signed_in': False},
            'Fixture status check failed': {
                'installed': True, 'signed_in': False, 'message': 'Fixture status check failed'
            },
            'Fixture API account': {
                'installed': True, 'signed_in': True, 'api_billing': True, 'message': 'Fixture API account'
            },
        }
        for expected_error, provider_status in expected_errors.items():
            with self.subTest(status=provider_status):
                with patch.object(self.service.status_cache, 'get', AsyncMock(return_value=provider_status)):
                    result = await self.fixture_chat()
                self.assertEqual(len(result), 1)
                self.assertIn(expected_error, result[0]['error'])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_chat_waits_for_a_current_status_instead_of_an_expired_plan_status(self):
        # An hour ago the CLI used a plan. Since then it was switched to an API-billed login.
        self.service.status_cache._entries[self.on_plan('claude')] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() - 3600
        )

        async def api_billing_now():
            return {'installed': True, 'signed_in': True, 'api_billing': True, 'message': 'Fixture API key login'}

        self.providers['claude'].status_hook = api_billing_now
        result = await self.fixture_chat()
        self.assertEqual(len(result), 1)
        self.assertIn('Fixture API key login', result[0]['error'])
        self.assertEqual(self.providers['claude'].calls, [('status', 'runner-a', 'runner-a')])

        # An expired status that still shows a plan lets the turn run after the check.
        self.service.status_cache._entries[self.on_plan('claude')].expires_at = time.monotonic() - 1
        self.providers['claude'].status_hook = None
        self.providers['claude'].calls.clear()
        self.assertEqual(await self.fixture_chat(), [{'text': 'fixture response'}])
        self.assertEqual(self.providers['claude'].calls, [
            ('status', 'runner-a', 'runner-a'), ('run_turn', 'runner-a'),
        ])

    async def test_enabled_legacy_remote_model_get_stays_empty_even_with_old_signed_in_cache(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        self.config.values['subscriptions.codex']['enable'] = False
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        self.assertEqual(await self.service._provider_models(self.service.ProviderRef(OWNER, 'claude')), [])
        self.assertEqual(await self.service.get_user_subscription_models(OWNER_USER), [])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_legacy_remote_status_and_model_refresh_do_not_call_providers_or_create_handles(self):
        self.config.values['subscriptions.machines'][0]['revision'] = ''
        before = copy.deepcopy(self.config.values)
        status = await self.service._load_status(self.on_plan('claude'))
        self.assertFalse(status['signed_in'])
        self.assertEqual(status['message'], self.service.REVERIFY_MACHINE_MESSAGE)
        self.assertEqual(await self.service._load_models(self.on_plan('claude')), [])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])
        self.assertIsNone(self.tracked_handle('claude'))
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_legacy_remote_provider_description_is_read_only_and_skips_machine_and_caches(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        self.seed_old_signed_in_cache()
        before = copy.deepcopy(self.config.values)
        with patch.object(self.service.status_cache, 'get', AsyncMock()) as status_get:
            with patch.object(self.service.models_cache, 'get', AsyncMock()) as models_get:
                with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
                    description = await self.service.describe_provider(OWNER, 'claude', fresh=True)
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
            settings=await self.service.get_settings(OWNER, 'claude'),
            machine=self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
        )
        result = [event async for event in self.service._tracked_turn(self.service.ProviderRef(OWNER, 'claude'), self.providers['claude'], turn)]
        self.assertEqual(result, [{'error': self.service.REVERIFY_MACHINE_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_verifying_and_saving_legacy_remote_enables_model_discovery_and_chat(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('revision')
        self.assertEqual(await self.service._provider_models(self.service.ProviderRef(OWNER, 'claude')), [])
        self.assertEqual(await self.fixture_chat(), [{'error': self.service.REVERIFY_MACHINE_MESSAGE}])
        self.assertEqual(self.providers['claude'].calls, [])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], original['url'], None, original['browser_url']
            )
        self.assertTrue(public['revision'])

        async def verified_status():
            wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
            await wrapper.start_process(['fixture-status'], 'fixture-cwd')
            return {'installed': True, 'signed_in': True, 'api_billing': False}

        async def verified_models():
            wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
            await wrapper.start_process(['fixture-models'], 'fixture-cwd')
            return [types.SimpleNamespace(
                key='fixture-model', value='fixture-model', name='Fixture model', description='', efforts=[],
                ultra=False,
            )]

        async def verified_turn(turn):
            await turn.machine.start_process(['fixture-chat'], 'fixture-cwd')
            yield {'text': 'fixture response'}

        self.providers['claude'].status_hook = verified_status
        self.providers['claude'].models_hook = verified_models
        self.providers['claude'].turn_hook = verified_turn
        discovered = await self.service._provider_models(self.service.ProviderRef(OWNER, 'claude'))
        self.assertEqual([model['id'] for model in discovered], ['claude-code.fixture-model'])
        self.assertEqual(await self.fixture_chat(), [{'text': 'fixture response'}])
        self.assertEqual(len(self.machines['runner-a'].processes), 3)
        await self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a']).revoke()

    async def test_local_provider_discovery_keeps_legacy_compatibility_without_registry_revision(self):
        self.config.values['subscriptions.claude'] = enabled_settings('local')
        status = await self.service._load_status(self.on_plan('claude'))
        self.assertTrue(status['signed_in'])
        self.assertEqual(await self.service._load_models(self.on_plan('claude')), [])
        self.assertIn(('status', 'local', 'local'), self.providers['claude'].calls)
        self.assertIn(('list_models', 'local', 'local'), self.providers['claude'].calls)
        self.assertEqual(self.config.commits, [])

    async def test_unexpected_refresh_errors_report_signed_out_instead_of_failing_the_model_list(self):
        async def connection_closed():
            # A lost runner connection surfaces as ProcessClosedError, a RuntimeError.
            raise RuntimeError('Fixture runner connection closed')

        self.config.values['subscriptions.codex']['enable'] = False
        self.providers['claude'].status_hook = connection_closed
        self.providers['claude'].models_hook = connection_closed
        status = await self.service._load_status(self.on_plan('claude'))
        self.assertFalse(status['signed_in'])
        self.assertIn('Fixture runner connection closed', status['message'])
        self.assertEqual(await self.service._load_models(self.on_plan('claude')), [])
        self.assertEqual(await self.service.get_user_subscription_models(OWNER_USER), [])
        self.assertFalse(self.service.status_cache._entries[self.on_plan('claude')].value['signed_in'])

    async def test_one_failing_provider_leaves_the_other_providers_models_listed(self):
        model = types.SimpleNamespace(
            key='fixture-model', value='fixture-model', name='Fixture model', description='', efforts=[],
            ultra=False,
        )
        self.providers['codex'].models_hook = AsyncMock(return_value=[model])
        list_provider_models = self.service._provider_models

        async def claude_fails(provider_id):
            if provider_id == 'claude':
                raise RuntimeError('Fixture settings read failed')
            return await list_provider_models(provider_id)

        with patch.object(self.service, '_provider_models', claude_fails):
            models = await self.service.get_user_subscription_models(OWNER_USER)
        self.assertEqual([entry['id'] for entry in models], ['codex.fixture-model'])

    async def test_model_list_does_not_wait_for_a_slow_sign_in(self):
        self.config.values['subscriptions.codex']['enable'] = False
        entered = asyncio.Event()
        release = self.gate()

        async def slow_sign_in():
            entered.set()
            await release.wait()

        self.providers['claude'].start_hook = slow_sign_in
        login = self.launch(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual(await asyncio.wait_for(self.service.get_user_subscription_models(OWNER_USER), 1), [])
        self.assertFalse(login.done())
        self.assertNotIn(('status', 'runner-a', 'runner-a'), self.providers['claude'].calls)
        release.set()
        await asyncio.wait_for(login, 1)

    async def test_model_list_serves_cached_models_while_the_provider_lock_is_held(self):
        self.config.values['subscriptions.codex']['enable'] = False
        self.seed_old_signed_in_cache()
        for cache in (self.service.status_cache, self.service.models_cache):
            cache._entries[self.on_plan('claude')].expires_at = time.monotonic() - 1
        lock = self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude'))
        await lock.acquire()
        try:
            models = await asyncio.wait_for(self.service.get_user_subscription_models(OWNER_USER), 1)
        finally:
            lock.release()
        self.assertEqual([entry['id'] for entry in models], ['claude-code.fixture-model'])
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_deleted_host_can_be_explicitly_replaced_then_enabled_with_the_new_remote_guard(self):
        await self.service.delete_machine(OWNER, 'runner-a')
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.providers['claude'].calls.clear()
        replacement = await self.service.save_settings(OWNER, 
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
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-b'))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-a', REVISION_A))
        self.assertEqual(self.config.values, before)
        self.assertEqual(len(self.config.commits), commits)
        enabled = await self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-b', REVISION_B)
        self.assertTrue(enabled.enable)
        self.assertEqual(enabled.access, 'chat')
        self.assertEqual(enabled.workspace, '')
        self.assertEqual(enabled.cli_path, '')
        self.assertEqual(self.machines['runner-a'].closed, 1)
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))

    async def test_new_registration_cannot_reuse_a_deleted_selected_host_id(self):
        self.config.values['subscriptions.machines'][0]['name'] = 'Runner A'
        await self.service.delete_machine(OWNER, 'runner-a')
        info = {'host': {'id': 'replacement-host', 'name': 'Different PC'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            saved = await self.service.save_machine(OWNER, 
                None, 'Runner A', 'https://replacement.example.invalid:8083', 'fixture-replacement-key'
            )
        self.assertEqual(saved['id'], 'runner-a-2')
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.providers['claude'].calls.clear()
        with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
            status = await self.service._load_status(self.on_plan('claude'))
            self.assertFalse(status['signed_in'])
            self.assertEqual(await self.service._load_models(self.on_plan('claude')), [])
            self.assertEqual(await self.service._provider_models(self.service.ProviderRef(OWNER, 'claude')), [])
            description = await self.service.describe_provider(OWNER, 'claude', fresh=True)
        machine_get.assert_not_awaited()
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(description['machine']['id'], 'runner-a')
        await self.service.save_settings(OWNER, 'claude', enabled_settings(saved['id']), 'runner-a')
        current = self.config.values['subscriptions.claude']
        self.assertEqual(current['machine_id'], saved['id'])
        self.assertFalse(current['enable'])
        self.assertEqual((current['access'], current['workspace'], current['cli_path']), ('chat', '', ''))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-a'))
        await self.service.save_settings(OWNER, 'claude', {'enable': True}, saved['id'], saved['revision'])
        self.providers['claude'].calls.clear()
        status = await self.service._load_status(self.on_plan('claude'))
        self.assertTrue(status['signed_in'])
        self.assertIn(('status', saved['id'], saved['id']), self.providers['claude'].calls)

    async def test_new_registration_still_avoids_current_registry_id_collisions(self):
        before = copy.deepcopy(self.config.values['subscriptions.machines'])
        info = {'host': {'id': 'additional-host', 'name': 'Additional PC'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            saved = await self.service.save_machine(OWNER, 
                None, 'Runner B', 'https://additional.example.invalid:8083', 'fixture-additional-key'
            )
        self.assertEqual(saved['id'], 'runner-b-2')
        self.assertEqual(self.config.values['subscriptions.machines'][:2], before)

    async def test_new_registration_reserves_retained_cleanup_owner_host_ids(self):
        old_machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), old_machine)
        wrapper.valid = False
        wrapper.startup_cleanup_error = self.service.StartupCleanupUnconfirmedError('Fixture retained old startup')
        self.config.values['subscriptions.claude'] = enabled_settings('runner-b')
        self.config.values['subscriptions.machines'] = [machine_config('runner-b', 'host-b')]
        info = {'host': {'id': 'additional-host', 'name': 'Additional PC'}}
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value=info)):
            saved = await self.service.save_machine(OWNER, 
                None, 'Runner A', 'https://additional.example.invalid:8083', 'fixture-additional-key'
            )
        self.assertEqual(saved['id'], 'runner-a-2')
        self.assertIs(self.tracked_handle('claude'), wrapper)
        self.assertEqual(wrapper.id, 'runner-a')
        self.assertIsNotNone(wrapper.startup_cleanup_error)

    async def test_deleted_host_can_be_explicitly_replaced_with_local_without_restoring_old_authority(self):
        await self.service.delete_machine(OWNER, 'runner-a')
        replacement = await self.service.save_settings(OWNER, 'claude', {
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
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-a'))
        enabled = await self.service.save_settings(OWNER, 'claude', {'enable': True}, 'local')
        self.assertTrue(enabled.enable)
        self.assertEqual(enabled.access, 'chat')
        self.assertEqual(enabled.workspace, '')
        self.assertEqual(enabled.cli_path, '')
        self.assertTrue(all(not machine.processes for machine in self.machines.values()))

    async def test_deleted_host_recovery_rejects_missing_wrong_or_unchanged_selection_and_auth_actions(self):
        await self.service.delete_machine(OWNER, 'runner-a')
        self.providers['claude'].calls.clear()
        before = copy.deepcopy(self.config.values)
        commits = len(self.config.commits)
        with patch.object(self.service, 'get_machine', AsyncMock()) as machine_get:
            await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}))
            await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}, 'runner-b'))
            await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-a'}, 'runner-a'))
            await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-a'))
            await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a'))
            await self.assert_conflict(self.service.submit_login_code(OWNER, 'claude', 'fixture-code', 'runner-a'))
            await self.assert_conflict(self.service.cancel_login(OWNER, 'claude', 'runner-a'))
            await self.assert_conflict(self.service.logout(OWNER, 'claude', 'runner-a'))
        machine_get.assert_not_awaited()
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings(OWNER, 'claude', {'machine_id': 'missing-target'}, 'runner-a')
        self.assertEqual(self.config.values, before)
        self.assertEqual(len(self.config.commits), commits)
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_existing_legacy_host_without_revision_cannot_use_deleted_host_recovery(self):
        self.config.values['subscriptions.machines'][0].pop('revision')
        before = copy.deepcopy(self.config.values)
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}, 'runner-a'))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'machine_id': 'local'}, 'runner-a'))
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_deleted_host_recovery_cannot_bypass_retained_unknown_startup_cleanup_debt(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
        failure = self.service.StartupCleanupUnconfirmedError('Fixture unknown process remains on deleted host')

        async def unknown_startup():
            raise failure

        machine.start_hook = unknown_startup
        with self.assertRaises(self.service.StartupCleanupUnconfirmedError):
            await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine(OWNER, 'runner-a')
        self.assertFalse(any(config['id'] == 'runner-a' for config in self.config.values['subscriptions.machines']))
        before = copy.deepcopy(self.config.values)
        commits = len(self.config.commits)
        for target in ('runner-b', 'local'):
            with self.subTest(target=target):
                with self.assertRaises(self.service.SubscriptionError) as caught:
                    await self.service.save_settings(OWNER, 'claude', enabled_settings(target), 'runner-a')
                self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, str(caught.exception))
                self.assertIn(machine.id, str(caught.exception))
                self.assertEqual(self.config.values, before)
                self.assertEqual(len(self.config.commits), commits)
                self.assertIs(self.tracked_handle('claude'), wrapper)
                self.assertIs(wrapper.startup_cleanup_error, failure)
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])

    async def delete_host_with_unconfirmed_process(self, stop_failures):
        """Remove runner-b while the Runner cannot confirm that Codex's process stopped."""
        machine = self.machines['runner-b']
        machine.process_factory = StrictTrackedProcess
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'codex'), machine)
        process = await wrapper.start_process(['fixture-codex'], 'fixture-cwd')
        process.stop_failures = [
            self.service.SubscriptionError('Fixture Runner unreachable') for _ in range(stop_failures)
        ]
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine(OWNER, 'runner-b')
        self.assertIsNotNone(wrapper.cleanup_error)
        self.assertIn(process, wrapper.processes)
        self.assertEqual(self.config.values['subscriptions.codex']['machine_id'], 'runner-b')
        return wrapper, process

    async def test_moving_off_a_deleted_host_retries_its_unconfirmed_process_cleanup(self):
        wrapper, process = await self.delete_host_with_unconfirmed_process(stop_failures=1)
        # The Runner is reachable again; choosing another machine retries cleanup.
        moved = await self.service.save_settings(OWNER, 'codex', {'machine_id': 'local'}, 'runner-b')
        self.assertEqual(moved.machine_id, 'local')
        self.assertFalse(moved.enable)
        self.assertEqual(process.stop_count, 2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(wrapper.processes, set())
        self.assertIsNone(self.tracked_handle('codex'))
        enabled = await self.service.save_settings(OWNER, 'codex', {'enable': True}, 'local')
        self.assertTrue(enabled.enable)

    async def test_failed_retry_after_leaving_a_deleted_host_is_retried_by_turning_the_provider_on(self):
        wrapper, process = await self.delete_host_with_unconfirmed_process(stop_failures=2)
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings(OWNER, 'codex', {'machine_id': 'local'}, 'runner-b')
        settings = self.config.values['subscriptions.codex']
        self.assertEqual(settings['machine_id'], 'local')
        self.assertFalse(settings['enable'])
        self.assertIs(self.tracked_handle('codex'), wrapper)
        self.assertIsNotNone(wrapper.cleanup_error)
        # The selected machine exists now, so turning the provider on retries cleanup.
        enabled = await self.service.save_settings(OWNER, 'codex', {'enable': True}, 'local')
        self.assertTrue(enabled.enable)
        self.assertEqual(process.stop_count, 3)
        self.assertTrue(process.cleanup_confirmed)
        self.assertIsNone(self.tracked_handle('codex'))

    async def test_remote_mutations_without_an_expected_host_fail_before_provider_calls(self):
        before = copy.deepcopy(self.config.values)
        await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser'))
        await self.assert_conflict(self.service.submit_login_code(OWNER, 'claude', 'fixture-code'))
        await self.assert_conflict(self.service.cancel_login(OWNER, 'claude'))
        await self.assert_conflict(self.service.logout(OWNER, 'claude'))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': False}))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.values, before)
        self.assertEqual(self.config.commits, [])

    async def test_stale_host_mutations_and_reads_fail_before_provider_calls(self):
        await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.submit_login_code(OWNER, 'claude', 'fixture-code', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.cancel_login(OWNER, 'claude', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.logout(OWNER, 'claude', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.login_state(OWNER, 'claude', 'runner-b', REVISION_A))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': False}, 'runner-b', REVISION_A))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])

    async def test_remote_mutations_with_matching_id_but_no_revision_are_rejected(self):
        await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a'))
        await self.assert_conflict(self.service.submit_login_code(OWNER, 'claude', 'fixture-code', 'runner-a'))
        await self.assert_conflict(self.service.cancel_login(OWNER, 'claude', 'runner-a'))
        await self.assert_conflict(self.service.logout(OWNER, 'claude', 'runner-a'))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': False}, 'runner-a'))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])

    async def test_remote_mutations_with_a_stale_revision_are_rejected(self):
        await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.submit_login_code(OWNER, 'claude', 'fixture-code', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.cancel_login(OWNER, 'claude', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.logout(OWNER, 'claude', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.login_state(OWNER, 'claude', 'runner-a', 'obsolete-revision'))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': False}, 'runner-a', 'obsolete-revision'))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])

    async def test_replacing_backend_under_the_same_machine_id_rejects_the_old_revision(self):
        original = self.config.values['subscriptions.machines'][0]
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': {'id': 'replacement-host'}})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], original['url'], None, original['browser_url']
            )
        self.assertEqual(public['id'], 'runner-a')
        self.assertIsInstance(public['revision'], str)
        self.assertNotEqual(public['revision'], REVISION_A)
        before = copy.deepcopy(self.config.values)
        previous_commits = len(self.config.commits)
        self.providers['claude'].calls.clear()
        await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', REVISION_A))
        await self.assert_conflict(self.service.logout(OWNER, 'claude', 'runner-a', REVISION_A))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-a', REVISION_A))
        self.assertEqual(self.config.values, before)
        self.assertEqual(len(self.config.commits), previous_commits)
        self.assertEqual(self.providers['claude'].calls, [])
        await self.service.save_settings(OWNER, 'claude', {'enable': True}, 'runner-a', public['revision'])
        self.assertTrue(self.config.values['subscriptions.claude']['enable'])

    async def test_legacy_machine_without_revision_requires_verification_before_mutation(self):
        original = self.config.values['subscriptions.machines'][0]
        original.pop('revision')
        await self.assert_conflict(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', REVISION_A))
        await self.assert_conflict(self.service.save_settings(OWNER, 'claude', {'enable': False}, 'runner-a', REVISION_A))
        self.assertEqual(self.providers['claude'].calls, [])
        self.assertEqual(self.config.commits, [])
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            public = await self.service.save_machine(OWNER, 
                'runner-a', original['name'], original['url'], None, original['browser_url']
            )
        self.assertIsInstance(public['revision'], str)
        self.assertTrue(public['revision'])
        await self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', public['revision'])
        self.assertIn(('start_login', 'runner-a', 'runner-a', 'browser'), self.providers['claude'].calls)

    async def test_remote_reads_allow_an_omitted_host_but_matching_mutations_are_explicit(self):
        self.assertEqual(await self.service.login_state(OWNER, 'claude'), {'state': 'waiting'})
        await self.service.start_login(OWNER, 'claude', 'device', 'runner-a', REVISION_A)
        await self.service.submit_login_code(OWNER, 'claude', 'fixture-code', 'runner-a', REVISION_A)
        self.assertIn(('start_login', 'runner-a', 'runner-a', 'device'), self.providers['claude'].calls)
        self.assertIn(('submit_login_code', 'fixture-code'), self.providers['claude'].calls)

    async def test_local_legacy_actions_can_omit_the_expected_host(self):
        self.config.values['subscriptions.claude'] = enabled_settings('local')
        await self.service.start_login(OWNER, 'claude', 'browser')
        await self.service.submit_login_code(OWNER, 'claude', 'fixture-code')
        await self.service.cancel_login(OWNER, 'claude')
        await self.service.logout(OWNER, 'claude')
        await self.service.save_settings(OWNER, 'claude', {'enable': False})
        self.assertIn(('start_login', 'local', 'local', 'browser'), self.providers['claude'].calls)
        self.assertIn(('logout', 'local', 'local'), self.providers['claude'].calls)
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])

    async def test_settings_accept_the_expected_host_field_without_persisting_it(self):
        await self.service.save_settings(OWNER, 'claude', {
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
        login = self.launch(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        transition = self.launch(self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A))
        logout = self.launch(self.service.logout(OWNER, 'claude', 'runner-a', REVISION_A))
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
            access = self.launch(self.service.save_settings(OWNER, 'claude', {'access': 'read'}, 'runner-a', REVISION_A))
            await asyncio.wait_for(entered.wait(), 1)
            disable = self.launch(self.service.save_settings(OWNER, 'claude', {'enable': False}, 'runner-a', REVISION_A))
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
        mutation = self.launch(self.service.save_settings(OWNER, 'claude', {'access': 'read'}, 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        mutation.cancel()
        queued = self.launch(self.service.save_settings(OWNER, 'claude', {'enable': False}, 'runner-a', REVISION_A))
        await asyncio.sleep(0)
        mutation.cancel()
        await asyncio.sleep(0)
        self.assertFalse(mutation.done())
        self.assertFalse(queued.done())
        self.assertTrue(self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')).locked())
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
            async with self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')):
                observed.append(copy.deepcopy(self.config.values['subscriptions.claude']))

        self.providers['claude'].cancel_hook = failed_cleanup
        mutation = self.launch(self.service.save_settings(OWNER, 'claude', {'access': 'read'}, 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        if cancel_caller:
            mutation.cancel()
        observer = self.launch(observe_after_lock())
        await asyncio.sleep(0)
        if repeat_cancel:
            mutation.cancel()
            await asyncio.sleep(0)
            self.assertFalse(mutation.done())
            self.assertTrue(self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')).locked())
        self.assertEqual(observed, [])
        release.set()
        result = await asyncio.wait_for(asyncio.gather(mutation, observer, return_exceptions=True), 1)
        self.assertIsInstance(result[0], BaseException)
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]['enable'])
        # Only the switch turns off; turning it back on retries cleanup.
        self.assertEqual(observed[0]['access'], 'read')
        self.assertEqual(observed[0]['workspace'], 'C:\\fixture-project')
        self.assertEqual(observed[0]['cli_path'], 'C:\\fixture-tools\\provider.exe')
        self.assertEqual(observed[0]['machine_id'], 'runner-a')
        self.assertEqual(len(self.config.commits), 2)

    async def test_failed_cleanup_disables_access_before_the_provider_lock_is_released(self):
        await self.assert_cleanup_failure_disables_before_unlock()

    async def test_failed_cleanup_also_disables_access_when_the_mutation_caller_is_repeatedly_cancelled(self):
        await self.assert_cleanup_failure_disables_before_unlock(cancel_caller=True, repeat_cancel=True)

    async def test_deletion_cleans_every_affected_provider_and_closes_host_when_one_cleanup_fails(self):
        self.config.values['subscriptions.codex'] = enabled_settings('runner-a')
        machine = self.machines['runner-a']
        claude_wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
        codex_wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'codex'), machine)
        claude_process = await claude_wrapper.start_process(['fixture-claude'], 'fixture-cwd')
        codex_process = await codex_wrapper.start_process(['fixture-codex'], 'fixture-cwd')

        async def failed_cleanup():
            raise self.service.SubscriptionError('Fixture first-provider cleanup failed')

        self.providers['claude'].cancel_hook = failed_cleanup
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.delete_machine(OWNER, 'runner-a')
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
            await self.service.get_machine(OWNER, 'runner-a')

    async def test_logout_holds_its_provider_lock_until_the_action_finishes(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def block_logout():
            entered.set()
            await release.wait()

        self.providers['claude'].logout_hook = block_logout
        logout = self.launch(self.service.logout(OWNER, 'claude', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        config = self.launch(self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A))
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
        blocked = self.launch(self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', REVISION_A))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.service.start_login(OWNER, 'codex', 'device', 'runner-b', REVISION_B), 1)
        self.assertFalse(blocked.done())
        self.assertIn(('start_login', 'runner-b', 'runner-b', 'device'), self.providers['codex'].calls)
        release.set()
        await asyncio.wait_for(blocked, 1)

    async def test_same_machine_wrapper_is_reused_until_quiescence_and_old_handle_is_revoked(self):
        machine = self.machines['runner-a']
        first = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
        self.assertIs(first, self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine))
        process = await first.start_process(['fixture-cli'], 'fixture-cwd')
        async with self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')):
            await self.service._quiesce_provider(self.service.ProviderRef(OWNER, 'claude'))
        self.assertGreaterEqual(process.kill_count, 1)
        self.assertGreaterEqual(process.wait_count, 1)
        with self.assertRaises(self.service.SubscriptionError):
            await first.start_process(['fixture-cli'], 'fixture-cwd')
        replacement = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
        self.assertIsNot(replacement, first)
        self.assertEqual(len(machine.processes), 1)

    async def test_signing_in_on_a_chosen_computer_keeps_the_plans_own_computer(self):
        started = await self.service.start_login(
            OWNER, 'claude', 'browser', expected_machine_revision=REVISION_B, machine_id='runner-b'
        )
        self.assertEqual(started, {'state': 'waiting'})
        self.assertIn(('start_login', 'runner-b', 'runner-b', 'browser'), self.providers['claude'].calls)
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')
        # Progress for another computer shows nothing; for runner-b it shows the sign-in.
        other = await self.service.login_state(OWNER, 'claude', machine_id='runner-a')
        self.assertEqual(other, {'state': 'idle'})
        here = await self.service.login_state(OWNER, 'claude', machine_id='runner-b')
        self.assertEqual(here, {'state': 'waiting'})
        with self.assertRaises(self.service.HTTPException) as caught:
            await self.service.submit_login_code(
                OWNER, 'claude', 'fixture-code', expected_machine_revision=REVISION_A, machine_id='runner-a'
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(str(caught.exception), 'Finish signing in on Test runner-b first.')
        finished = await self.service.submit_login_code(
            OWNER, 'claude', 'fixture-code', expected_machine_revision=REVISION_B, machine_id='runner-b'
        )
        self.assertEqual(finished, {'state': 'success'})
        await self.service.logout(OWNER, 'claude', expected_machine_revision=REVISION_B, machine_id='runner-b')
        self.assertIn(('logout', 'runner-b', 'runner-b'), self.providers['claude'].calls)

    async def test_signing_in_or_out_on_another_computer_leaves_the_plans_own_computer_running(self):
        claude = self.service.ProviderRef(OWNER, 'claude')
        on_plan_computer = self.service._tracking_machine(claude, self.machines['runner-a'])
        kept = await on_plan_computer.start_process(['fixture-turn'], 'fixture-cwd')
        generation = self.service._provider_generations.get(claude, 0)
        await self.service.start_login(OWNER, 'claude', 'browser', expected_machine_revision=REVISION_B, machine_id='runner-b')
        await self.service.cancel_login(OWNER, 'claude', expected_machine_revision=REVISION_B, machine_id='runner-b')
        await self.service.logout(OWNER, 'claude', expected_machine_revision=REVISION_B, machine_id='runner-b')
        self.assertEqual(kept.kill_count, 0)
        self.assertTrue(on_plan_computer.valid)
        self.assertEqual(self.service._provider_generations.get(claude, 0), generation)
        self.assertIn(('logout', 'runner-b', 'runner-b'), self.providers['claude'].calls)
        # On the plan's own computer, signing out still stops the whole plan.
        await self.service.logout(OWNER, 'claude', 'runner-a', REVISION_A)
        self.assertGreaterEqual(kept.kill_count, 1)

    async def test_a_sign_in_left_on_a_removed_computer_no_longer_blocks_codes(self):
        claude = self.service.ProviderRef(OWNER, 'claude')
        self.providers['claude'].login_state = lambda: {'state': 'waiting'}
        self.service._login_computers[claude] = 'removed-pc'
        finished = await self.service.submit_login_code(
            OWNER, 'claude', 'fixture-code', expected_machine_revision=REVISION_A, machine_id='runner-a'
        )
        self.assertEqual(finished, {'state': 'success'})
        self.assertNotIn(claude, self.service._login_computers)

    async def test_signing_in_on_the_plans_computer_uses_its_saved_cli_path(self):
        await self.service.save_computer_settings(OWNER, 'runner-a', REVISION_A, '', {'claude': 'C:\\tools\\claude.exe'})
        observed = []
        original = self.providers['claude'].start_login

        async def record_settings(settings, machine, method):
            observed.append(settings.cli_path)
            return await original(settings, machine, method)

        self.providers['claude'].start_login = record_settings
        await self.service.start_login(OWNER, 'claude', 'browser', 'runner-a', REVISION_A)
        self.assertEqual(observed, ['C:\\tools\\claude.exe'])

    async def test_signing_in_on_a_computer_needs_one_of_yours_at_its_current_revision(self):
        for machine_id, revision in (('runner-b', REVISION_A), ('missing-pc', REVISION_B), ('runner-b', None)):
            with self.subTest(machine_id=machine_id, revision=revision):
                await self.assert_conflict(self.service.start_login(
                    OWNER, 'claude', 'browser', expected_machine_revision=revision, machine_id=machine_id
                ))
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.start_login(OWNER, 'claude', 'browser', machine_id='')
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_the_description_says_where_the_plan_is_signed_in(self):
        ref = self.service.ProviderRef(OWNER, 'claude')
        self.service.status_cache._entries[ref.on('runner-b')] = self.service._CacheEntry(
            {'installed': True, 'signed_in': False, 'message': 'Fixture signed out'}, time.monotonic() + 60
        )
        described = await self.service.describe_provider(OWNER, 'claude')
        self.assertEqual(described['computers'], [
            {'machine_id': 'runner-a', 'name': 'Test runner-a', 'online': True, 'state': 'ready'},
            {'machine_id': 'local', 'name': self.service.LOCAL_MACHINE.name, 'online': True, 'state': 'not_checked'},
            {
                'machine_id': 'runner-b', 'name': 'Test runner-b', 'online': True, 'state': 'signed_out',
                'message': 'Fixture signed out'
            },
        ])
        self.assertIsNone(described['login_machine_id'])
        statuses = [call for call in self.providers['claude'].calls if call[0] == 'status']
        self.assertEqual(statuses, [('status', 'runner-a', 'runner-a')])

        # A refresh after a change asks only the plan's own computer.
        self.providers['claude'].calls.clear()
        await self.service.describe_provider(OWNER, 'claude', fresh=True)
        statuses = [call for call in self.providers['claude'].calls if call[0] == 'status']
        self.assertEqual(statuses, [('status', 'runner-a', 'runner-a')])

        # Check again asks every computer that can be reached.
        self.providers['claude'].calls.clear()
        described = await self.service.describe_provider(OWNER, 'claude', fresh=True, check_computers=True)
        statuses = [call for call in self.providers['claude'].calls if call[0] == 'status']
        self.assertCountEqual(statuses, [
            ('status', 'runner-a', 'runner-a'), ('status', 'local', 'local'), ('status', 'runner-b', 'runner-b')
        ])
        self.assertEqual([computer['state'] for computer in described['computers']], ['ready', 'ready', 'ready'])

    async def test_each_computer_keeps_its_own_start_folder_and_cli_paths(self):
        before = await self.service.describe_computer_settings(OWNER, 'runner-a')
        # Until it has its own, the plan's computer uses what was saved with the plan, kept per plan.
        self.assertFalse(before['saved'])
        self.assertEqual((before['start_folder'], before['cli_paths']), ('', {'claude': '', 'codex': ''}))
        self.assertEqual(before['from_plans'], {
            'claude': {'start_folder': 'C:\\fixture-project', 'cli_path': 'C:\\fixture-tools\\provider.exe'}
        })
        # Codex's plan runs on runner-b, so what was saved with it applies there.
        other = await self.service.describe_computer_settings(OWNER, 'runner-b')
        self.assertEqual(list(other['from_plans']), ['codex'])

        saved = await self.service.save_computer_settings(
            OWNER, 'runner-b', REVISION_B, ' C:\\code ', {'claude': 'C:\\tools\\claude.exe', 'codex': ''}
        )
        self.assertTrue(saved['saved'])
        self.assertEqual(saved['start_folder'], 'C:\\code')
        self.assertEqual(saved['cli_paths'], {'claude': 'C:\\tools\\claude.exe', 'codex': ''})
        self.assertEqual(saved['from_plans'], {})
        self.assertEqual(self.machines['runner-b'].path_checks, [
            ('C:\\code', 'dir'), ('C:\\tools\\claude.exe', 'file')
        ])
        await self.service.status_cache.get(self.service.ProviderRef(OWNER, 'claude').on('runner-b'))
        on_b = self.providers['claude'].status_settings[-1]
        self.assertEqual((on_b.workspace, on_b.cli_path), ('C:\\code', 'C:\\tools\\claude.exe'))
        # The plan's own settings are unchanged.
        self.assertEqual(self.config.values['subscriptions.claude']['cli_path'], 'C:\\fixture-tools\\provider.exe')

        # A computer that is set up again starts with nothing saved.
        self.config.values['subscriptions.machines'][1]['revision'] = 'fixture-revision-runner-b-again'
        again = await self.service.describe_computer_settings(OWNER, 'runner-b')
        self.assertFalse(again['saved'])
        self.assertEqual(again['cli_paths']['claude'], '')

    async def test_the_plan_form_cant_change_paths_a_computer_now_keeps_itself(self):
        await self.service.save_computer_settings(OWNER, 'runner-a', REVISION_A, '', {})
        with self.assertRaisesRegex(self.service.SubscriptionError, 'its own start folder'):
            await self.service.save_settings(OWNER, 'claude', {'cli_path': ''}, 'runner-a', REVISION_A)
        # Other settings still save.
        saved = await self.service.save_settings(OWNER, 'claude', {'access': 'read'}, 'runner-a', REVISION_A)
        self.assertEqual(saved.access, 'read')

    async def test_computer_settings_are_not_saved_for_a_computer_removed_while_its_paths_were_checked(self):
        machine = self.machines['runner-b']

        async def removed_during_check(path, kind):
            self.config.values['subscriptions.machines'].pop(1)
            return True

        machine.path_exists = removed_during_check
        with self.assertRaisesRegex(self.service.SubscriptionError, 'That computer was removed'):
            await self.service.save_computer_settings(OWNER, 'runner-b', REVISION_B, 'C:\\code', {})
        self.assertNotIn('subscriptions.users.owner-1.computers', self.config.values)

    async def test_saving_computer_settings_needs_the_revision_the_page_showed(self):
        await self.assert_conflict(
            self.service.save_computer_settings(OWNER, 'runner-b', REVISION_A, '', {})
        )
        with self.assertRaisesRegex(self.service.SubscriptionError, 'That computer was removed'):
            await self.service.save_computer_settings(OWNER, 'missing-pc', REVISION_B, '', {})
        with self.assertRaisesRegex(self.service.SubscriptionError, 'Network paths'):
            await self.service.save_computer_settings(OWNER, 'runner-b', REVISION_B, '\\\\server\\share', {})
        self.assertNotIn('subscriptions.users.owner-1.computers', self.config.values)

    async def test_a_turn_uses_its_computers_saved_cli_path(self):
        self.seed_old_signed_in_cache()
        await self.service.save_computer_settings(OWNER, 'runner-a', REVISION_A, '', {'claude': 'C:\\tools\\claude.exe'})
        self.seed_old_signed_in_cache()
        observed = []

        async def record_turn(turn):
            observed.append(turn.settings)
            yield {'text': 'fixture response'}

        self.providers['claude'].turn_hook = record_turn
        self.assertEqual(await self.fixture_chat(), [{'text': 'fixture response'}])
        self.assertEqual(observed[0].cli_path, 'C:\\tools\\claude.exe')
        self.assertEqual(observed[0].workspace, '')

    async def test_removing_a_computer_stops_every_plans_work_there_but_nowhere_else(self):
        claude = self.service.ProviderRef(OWNER, 'claude')
        on_plan_computer = self.service._tracking_machine(claude, self.machines['runner-a'])
        kept = await on_plan_computer.start_process(['fixture-turn'], 'fixture-cwd')
        on_removed = self.service._tracking_machine(claude, self.machines['runner-b'])
        stopped = await on_removed.start_process(['fixture-sign-in'], 'fixture-cwd')
        self.service._login_computers[claude] = 'runner-b'
        await self.service.delete_machine(OWNER, 'runner-b')
        self.assertGreaterEqual(stopped.kill_count, 1)
        self.assertNotIn(claude.on('runner-b'), self.service._tracked_machines)
        self.assertNotIn(claude, self.service._login_computers)
        self.assertIn('cancel_login', self.providers['claude'].calls)
        # Claude's plan runs on runner-a, so it stays on, and its work there keeps running.
        self.assertEqual(kept.kill_count, 0)
        self.assertIs(self.service._tracked_machines[claude.on('runner-a')], on_plan_computer)
        self.assertTrue(self.config.values['subscriptions.claude']['enable'])
        # Codex's plan ran on runner-b, so it is turned off as before.
        self.assertFalse(self.config.values['subscriptions.codex']['enable'])

    async def test_replacing_a_computers_address_stops_other_plans_work_there_first(self):
        claude = self.service.ProviderRef(OWNER, 'claude')
        on_replaced = self.service._tracking_machine(claude, self.machines['runner-b'])
        process = await on_replaced.start_process(['fixture-sign-in'], 'fixture-cwd')
        original = self.config.values['subscriptions.machines'][1]
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            await self.service.save_machine(
                OWNER, 'runner-b', original['name'], 'https://moved.example.invalid:8083', 'fixture-new-key'
            )
        self.assertGreaterEqual(process.kill_count, 1)
        self.assertNotIn(claude.on('runner-b'), self.service._tracked_machines)
        self.assertTrue(self.config.values['subscriptions.claude']['enable'])

    async def test_turning_off_this_server_stops_work_there_for_plans_that_run_elsewhere(self):
        claude = self.service.ProviderRef(OWNER, 'claude')
        on_plan_computer = self.service._tracking_machine(claude, self.machines['runner-a'])
        kept = await on_plan_computer.start_process(['fixture-turn'], 'fixture-cwd')
        on_server = self.service._tracking_machine(claude, self.service.LOCAL_MACHINE)
        self.service._login_computers[claude] = 'local'
        await self.service.save_policy({'server_machine': False})
        self.assertFalse(on_server.valid)
        self.assertNotIn(claude.on('local'), self.service._tracked_machines)
        self.assertNotIn(claude, self.service._login_computers)
        self.assertEqual(kept.kill_count, 0)
        self.assertTrue(self.config.values['subscriptions.claude']['enable'])
        # A code can no longer finish a sign-in there.
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.submit_login_code(OWNER, 'claude', 'fixture-code', machine_id='local')

    async def test_unconfirmed_cleanup_on_an_offline_app_computer_waits_for_that_computer_alone(self):
        ref = self.service.ProviderRef(OWNER, 'claude')
        laptop = self.service.TunnelMachine('laptop', 'Laptop', self.service.HUB)
        waiting = self.service.TrackingMachine(laptop)
        waiting.cleanup_error = self.service.SubscriptionError('Fixture stop unconfirmed')
        self.service._tracked_machines[ref.on('laptop')] = waiting
        offline = types.SimpleNamespace(is_online=lambda machine_id: False)
        with patch.object(self.service, 'HUB', offline):
            # The plan's other computers carry on; work on the laptop waits.
            self.service._tracking_machine(ref, self.machines['runner-a'])
            with self.assertRaises(self.service.SubscriptionError):
                self.service._require_computer_cleanup(ref, 'laptop')
            # A stop of the whole plan keeps it waiting instead of turning the plan off.
            waiting.revoke = AsyncMock(side_effect=self.service.SubscriptionError('Fixture still offline'))
            async with self.service._provider_lock(ref):
                await self.service._quiesce_provider(ref)
            self.assertIs(self.service._tracked_machines[ref.on('laptop')], waiting)
            self.assertTrue(self.config.values['subscriptions.claude']['enable'])
        online = types.SimpleNamespace(is_online=lambda machine_id: True)
        with patch.object(self.service, 'HUB', online):
            # Once it is back but still unconfirmed, it blocks the provider as any other computer would.
            with self.assertRaises(self.service.SubscriptionError):
                self.service._tracking_machine(ref, self.machines['runner-a'])

    async def test_status_is_cached_for_each_computer_and_forgotten_for_all(self):
        ref = self.service.ProviderRef(OWNER, 'claude')
        on_a = ref.on('runner-a')
        on_b = ref.on('runner-b')
        await self.service.status_cache.get(on_a)
        await self.service.status_cache.get(on_b)
        self.assertEqual(self.providers['claude'].calls, [
            ('status', 'runner-a', 'runner-a'), ('status', 'runner-b', 'runner-b')
        ])
        on_plan_computer, on_other = self.providers['claude'].status_settings
        self.assertEqual(on_plan_computer.cli_path, 'C:\\fixture-tools\\provider.exe')
        # The start folder and CLI path were chosen for the plan's own computer.
        self.assertEqual((on_other.workspace, on_other.cli_path), ('', ''))
        self.assertIsNotNone(self.service.status_cache.cached(on_a))
        self.assertIsNotNone(self.service.status_cache.cached(on_b))
        self.service.invalidate(ref)
        self.assertIsNone(self.service.status_cache.cached(on_a))
        self.assertIsNone(self.service.status_cache.cached(on_b))

    def cache_models(self, provider_id, machine_id, *keys, signed_in=True):
        on_computer = self.service.ProviderRef(OWNER, provider_id).on(machine_id)
        self.service.status_cache._entries[on_computer] = self.service._CacheEntry(
            {'installed': True, 'signed_in': signed_in, 'api_billing': False}, time.monotonic() + 60
        )
        models = [
            types.SimpleNamespace(
                key=key, value=key, name=f'Fixture {key} on {machine_id}', description='', efforts=[], ultra=False
            )
            for key in keys
        ]
        self.service.models_cache._entries[on_computer] = self.service._CacheEntry(models, time.monotonic() + 60)

    async def test_plan_models_come_from_every_computer_where_the_plan_is_signed_in(self):
        self.config.values['subscriptions.codex']['enable'] = False
        self.cache_models('claude', 'runner-a', 'opus')
        self.cache_models('claude', 'runner-b', 'opus', 'sonnet')
        self.cache_models('claude', 'local', 'haiku', signed_in=False)
        models = await self.service.get_user_subscription_models(OWNER_USER)
        self.assertEqual([model['id'] for model in models], ['claude-code.opus', 'claude-code.sonnet'])
        # A model on several computers is listed as the plan's own computer offers it.
        self.assertEqual(models[0]['name'], 'Fixture opus on runner-a')
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_a_model_list_asks_only_the_plans_own_computer(self):
        self.config.values['subscriptions.codex']['enable'] = False
        await self.service.get_user_subscription_models(OWNER_USER)
        self.assertEqual(self.providers['claude'].calls, [
            ('status', 'runner-a', 'runner-a'), ('list_models', 'runner-a', 'runner-a')
        ])

    async def test_an_offline_app_computer_keeps_its_last_models_and_is_not_asked(self):
        self.config.values['subscriptions.codex']['enable'] = False
        self.config.values['subscriptions.claude'] = enabled_settings('runner-b')
        self.config.values['subscriptions.machines'][1]['kind'] = 'runner'
        self.cache_models('claude', 'runner-b', 'sonnet')
        for cache in (self.service.status_cache, self.service.models_cache):
            cache._entries[self.service.ProviderRef(OWNER, 'claude').on('runner-b')].expires_at = 0
        offline = types.SimpleNamespace(is_online=lambda machine_id: False)
        with patch.object(self.service, 'HUB', offline):
            models = await self.service.get_user_subscription_models(OWNER_USER)
        self.assertEqual([model['id'] for model in models], ['claude-code.sonnet'])
        self.assertEqual(self.providers['claude'].calls, [])

    async def test_a_computer_that_connects_again_forgets_only_its_own_reports(self):
        self.cache_models('claude', 'runner-a', 'opus')
        self.cache_models('claude', 'runner-b', 'sonnet')
        await self.service.record_runner_connected('runner-b', {})
        ref = self.service.ProviderRef(OWNER, 'claude')
        self.assertIsNotNone(self.service.status_cache.cached(ref.on('runner-a')))
        self.assertIsNone(self.service.status_cache.cached(ref.on('runner-b')))
        self.assertIsNone(self.service.models_cache.cached(ref.on('runner-b')))

    async def test_each_computer_keeps_its_own_handle_and_quiescing_stops_both(self):
        ref = self.service.ProviderRef(OWNER, 'claude')
        on_a = self.service._tracking_machine(ref, self.machines['runner-a'])
        process_a = await on_a.start_process(['fixture-cli'], 'fixture-cwd')
        on_b = self.service._tracking_machine(ref, self.machines['runner-b'])
        process_b = await on_b.start_process(['fixture-cli'], 'fixture-cwd')
        # A turn on another computer leaves the first computer's handle and process alone.
        self.assertIsNot(on_a, on_b)
        self.assertIs(self.service._tracking_machine(ref, self.machines['runner-a']), on_a)
        self.assertTrue(on_a.valid)
        self.assertEqual(process_a.kill_count, 0)
        self.assertCountEqual(self.service._tracked_handles(ref), [on_a, on_b])
        self.assertEqual(self.service._tracked_refs(), [ref])
        async with self.service._provider_lock(ref):
            await self.service._quiesce_provider(ref)
        self.assertGreaterEqual(process_a.kill_count, 1)
        self.assertGreaterEqual(process_b.kill_count, 1)
        self.assertFalse(on_a.valid)
        self.assertFalse(on_b.valid)
        self.assertEqual(self.service._tracked_handles(ref), [])

    async def test_unconfirmed_cleanup_on_one_computer_blocks_the_provider_on_another(self):
        ref = self.service.ProviderRef(OWNER, 'claude')
        machine = self.machines['runner-a']
        machine.process_factory = StrictTrackedProcess
        on_a = self.service._tracking_machine(ref, machine)
        process = await on_a.start_process(['fixture-cli'], 'fixture-cwd')
        process.stop_failures = [self.service.SubscriptionError('Fixture Runner unreachable')]
        with self.assertRaises(self.service.SubscriptionError):
            async with self.service._provider_lock(ref):
                await self.service._quiesce_provider(ref)
        self.assertIsNotNone(on_a.cleanup_error)
        with self.assertRaises(self.service.SubscriptionError):
            self.service._tracking_machine(ref, self.machines['runner-b'])
        # The other provider on that computer is not held up.
        other = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'codex'), self.machines['runner-b'])
        self.assertTrue(other.valid)

    async def test_machine_rename_keeps_the_underlying_machine_wrapper_and_process(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
        process = await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        original = self.config.values['subscriptions.machines'][0]
        with patch.object(self.service, 'verify_machine', AsyncMock(return_value={'host': original['host']})):
            await self.service.save_machine(OWNER, 
                'runner-a', 'Renamed fixture PC', original['url'], None,
                browser_url='https://renamed-browser.example.invalid:9443'
            )
        looked_up = await self.service.get_machine(OWNER, 'runner-a')
        self.assertIs(looked_up, machine)
        self.assertIs(self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), looked_up), wrapper)
        self.assertEqual(machine.name, 'Renamed fixture PC')
        self.assertEqual(machine.closed, 0)
        self.assertEqual(process.kill_count, 0)
        self.assertEqual(self.providers['claude'].calls, [])
        await wrapper.revoke()

    async def test_noop_settings_save_preserves_the_running_handle_and_generation(self):
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
        process = await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        generation = self.service._provider_generations.get(self.service.ProviderRef(OWNER, 'claude'), 0)
        await self.service.save_settings(OWNER, 'claude', {}, 'runner-a', REVISION_A)
        self.assertIs(self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a']), wrapper)
        self.assertEqual(self.service._provider_generations.get(self.service.ProviderRef(OWNER, 'claude'), 0), generation)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
            wrapper = self.tracked_handle('claude')
            await wrapper.start_process(['fixture-status'], 'fixture-cwd')

        machine.start_hook = unknown_startup
        self.providers['claude'].status_hook = start_from_status
        status = await self.service._load_status(self.on_plan('claude'))
        self.assertFalse(status['signed_in'])
        wrapper = self.tracked_handle('claude')
        self.assertIs(wrapper.startup_cleanup_error, failure)
        self.assertEqual(wrapper.processes, set())
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A)
        self.assertIs(self.tracked_handle('claude'), wrapper)
        self.assertFalse(wrapper.valid)
        settings = self.config.values['subscriptions.claude']
        self.assertEqual(settings['machine_id'], 'runner-b')
        self.assertFalse(settings['enable'])
        self.assertEqual(settings['access'], 'chat')
        self.assertEqual(settings['workspace'], '')
        self.assertEqual(settings['cli_path'], '')
        with self.assertRaises(self.service.SubscriptionError) as blocked:
            self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-b'])
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
            status = await self.service._load_status(self.on_plan('claude'))
            self.assertFalse(status['signed_in'])
            self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, status['message'])
            self.assertEqual(await self.service._load_models(self.on_plan('claude')), [])
            self.assertEqual(await self.service._provider_models(self.service.ProviderRef(OWNER, 'claude')), [])
            chat = await self.fixture_chat()
            self.assertEqual(len(chat), 1)
            self.assertIn(self.service.STARTUP_CLEANUP_MESSAGE, chat[0]['error'])
            with patch.object(self.service.status_cache, 'get', AsyncMock()) as status_get:
                with patch.object(self.service.models_cache, 'get', AsyncMock()) as models_get:
                    description = await self.service.describe_provider(OWNER, 'claude', fresh=True)
                    login = await self.service.login_state(OWNER, 'claude', 'runner-b', REVISION_B)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)

        async def disconnected():
            raise self.service.SubscriptionError('Fixture connection failed before startup')

        machine.start_hook = disconnected
        with self.assertRaises(self.service.SubscriptionError):
            await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        self.assertIsNone(wrapper.startup_cleanup_error)
        self.assertIsNone(wrapper.cleanup_error)
        self.assertEqual(wrapper.startups, set())
        self.assertEqual(wrapper.processes, set())
        self.assertIs(self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine), wrapper)
        machine.start_hook = None
        process = await wrapper.start_process(['fixture-retry'], 'fixture-cwd')
        self.assertIn(process, wrapper.processes)
        await wrapper.revoke()
        self.assertGreaterEqual(process.kill_count, 1)

    async def test_late_handle_failed_stop_is_retained_until_cleanup_retry_succeeds(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
            self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-b'])
        async with self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')):
            await self.service._quiesce_provider(self.service.ProviderRef(OWNER, 'claude'))
        self.assertEqual(process.stop_count, 2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(wrapper.processes, set())
        self.assertIsNone(self.tracked_handle('claude'))

    async def test_failed_existing_handle_revoke_retains_wrapper_and_disables_access_until_retry(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
        process = StrictTrackedProcess()
        process.stop_failures = [self.service.SubscriptionError('Fixture strict stop failed')]
        machine.process_factory = lambda: process
        await wrapper.start_process(['fixture-cli'], 'fixture-cwd')
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_settings(OWNER, 'claude', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A)
        self.assertIs(self.tracked_handle('claude'), wrapper)
        self.assertIn(process, wrapper.processes)
        self.assertFalse(wrapper.valid)
        self.assertIsNotNone(wrapper.cleanup_error)
        self.assertFalse(self.config.values['subscriptions.claude']['enable'])
        self.assertEqual(self.config.values['subscriptions.claude']['access'], 'chat')
        with self.assertRaises(self.service.SubscriptionError):
            self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-b'])
        async with self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')):
            await self.service._quiesce_provider(self.service.ProviderRef(OWNER, 'claude'))
        self.assertEqual(process.stop_count, 2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertEqual(wrapper.processes, set())
        self.assertIsNone(wrapper.cleanup_error)
        replacement = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-b'])
        self.assertIsNot(replacement, wrapper)
        self.assertTrue(replacement.valid)

    async def test_exited_leader_with_unconfirmed_cleanup_blocks_next_start_and_retains_handle(self):
        machine = self.machines['runner-a']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), machine)
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
        command = self.launch(wrapper.run(['fixture-cli'], 'fixture-cwd', 5))
        await asyncio.wait_for(self.machines['runner-a'].process_created.wait(), 1)
        process = self.machines['runner-a'].processes[0]
        await asyncio.wait_for(process.read_started.wait(), 1)
        async with self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')):
            await self.service._quiesce_provider(self.service.ProviderRef(OWNER, 'claude'))
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
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'codex'), self.machines['runner-a'])
        turn = types.SimpleNamespace(machine=wrapper, settings=await self.service.get_settings(OWNER, 'codex'))

        async def consume_turn():
            return [event async for event in self.service._tracked_turn(self.service.ProviderRef(OWNER, 'codex'), provider, turn)]

        active = self.launch(consume_turn())
        status = self.launch(self.service.status_cache.get(self.on_plan('codex')))
        models = self.launch(self.service.models_cache.get(self.on_plan('codex')))
        await asyncio.wait_for(asyncio.gather(turn_started.wait(), status_started.wait(), models_started.wait()), 1)
        await self.service.save_settings(OWNER, 'codex', {'machine_id': 'runner-b'}, 'runner-a', REVISION_A)
        results = await asyncio.wait_for(asyncio.gather(active, status, models, return_exceptions=True), 1)
        self.assertTrue(turn_finished.is_set())
        self.assertTrue(status_finished.is_set())
        self.assertTrue(models_finished.is_set())
        self.assertIsNone(results[1])
        self.assertIsNone(results[2])
        self.assertNotIn(self.on_plan('codex'), self.service.status_cache._entries)
        self.assertNotIn(self.on_plan('codex'), self.service.models_cache._entries)
        self.assertGreaterEqual(self.machines['runner-a'].processes[0].kill_count, 1)
        self.assertIn('cancel_login', provider.calls)
        self.assertIn('stop', provider.calls)
        self.assertGreater(self.service._provider_generations[self.service.ProviderRef(OWNER, 'codex')], 0)
        self.assertEqual(self.config.values['subscriptions.codex']['machine_id'], 'runner-b')
        self.assertFalse(self.config.values['subscriptions.codex']['enable'])

    async def start_blocked_claude_turn(self, machine):
        """A Claude turn that has started a process on the machine and waits until stopped."""
        turn_started = asyncio.Event()
        turn_finished = asyncio.Event()
        release = self.gate()

        async def blocked_turn(turn):
            await turn.machine.start_process(['fixture-cli'], 'fixture-cwd')
            turn_started.set()
            try:
                yield {'text': 'partial response'}
                await release.wait()
            finally:
                turn_finished.set()

        self.providers['claude'].turn_hook = blocked_turn
        ref = self.service.ProviderRef(OWNER, 'claude')
        wrapper = self.service._tracking_machine(ref, machine)
        turn = types.SimpleNamespace(machine=wrapper, settings=await self.service.get_settings(OWNER, 'claude'))

        async def consume_turn():
            return [event async for event in self.service._tracked_turn(ref, self.providers['claude'], turn)]

        active = self.launch(consume_turn())
        await asyncio.wait_for(turn_started.wait(), 1)
        return active, turn_finished

    async def assert_policy_stops_turn(self, machine, policy_change):
        active, turn_finished = await self.start_blocked_claude_turn(machine)
        await asyncio.wait_for(self.service.save_policy(policy_change), 1)
        await asyncio.wait_for(asyncio.gather(active, return_exceptions=True), 1)
        self.assertTrue(turn_finished.is_set())
        self.assertGreaterEqual(machine.processes[0].kill_count, 1)
        # Only stopped: turning the switch back on needs nothing else.
        self.assertTrue(self.config.values['subscriptions.claude']['enable'])

    async def test_turning_off_plan_sign_in_stops_a_running_plan_turn(self):
        await self.assert_policy_stops_turn(self.machines['runner-a'], {'plan_sign_in': False})

    async def test_turning_off_api_keys_stops_a_running_api_key_turn(self):
        self.config.values['subscriptions.claude.credential'] = 'api_key'
        await self.assert_policy_stops_turn(self.machines['runner-a'], {'api_keys': False})

    async def test_turning_off_this_server_stops_a_turn_running_on_it(self):
        self.config.values['subscriptions.claude'] = enabled_settings('local')
        local = ProcessMachine({'id': 'local', 'name': 'This server', 'url': 'https://local.example.invalid', 'key': 'unused'})
        await self.assert_policy_stops_turn(local, {'server_machine': False})

    async def test_a_policy_check_that_fails_still_stops_the_turn(self):
        async def unavailable(user_id):
            raise RuntimeError('fixture database unavailable')

        self.service.Users.get_user_by_id = unavailable
        await self.assert_policy_stops_turn(self.machines['runner-a'], {'plan_sign_in': False})

    async def test_policy_saves_do_not_bring_back_deleted_users(self):
        deleted = self.service.ProviderRef('deleted-user', 'claude')
        # Left behind by tasks that ran before the user was deleted.
        self.service._active_provider_tasks[deleted] = set()
        await asyncio.wait_for(self.service.save_policy({'desktop_download_url': ''}), 1)
        self.assertNotIn(deleted, self.service._providers)

    async def test_turning_off_another_sign_in_leaves_a_plan_turn_running(self):
        active, turn_finished = await self.start_blocked_claude_turn(self.machines['runner-a'])
        await asyncio.wait_for(self.service.save_policy({'api_keys': False, 'server_machine': False}), 1)
        await asyncio.sleep(0.05)
        self.assertFalse(active.done())
        self.assertFalse(turn_finished.is_set())
        self.assertEqual(self.machines['runner-a'].processes[0].kill_count, 0)

    async def test_stale_turn_iterator_never_calls_the_provider_after_generation_changes(self):
        provider = self.providers['claude']
        wrapper = self.service._tracking_machine(self.service.ProviderRef(OWNER, 'claude'), self.machines['runner-a'])
        turn = types.SimpleNamespace(machine=wrapper, settings=await self.service.get_settings(OWNER, 'claude'))
        old_generation = self.service._provider_generations.get(self.service.ProviderRef(OWNER, 'claude'), 0)
        iterator = self.service._tracked_turn(self.service.ProviderRef(OWNER, 'claude'), provider, turn, generation=old_generation)
        async with self.service._provider_lock(self.service.ProviderRef(OWNER, 'claude')):
            await self.service._quiesce_provider(self.service.ProviderRef(OWNER, 'claude'))
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
        self.admin = OWNER_USER

    async def test_config_form_forwards_both_host_guards_inside_the_settings_body(self):
        form = self.router.SubscriptionConfigForm(
            enable=False, expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )
        await self.router.update_subscription_config('claude', form, user=self.admin)
        self.service.save_settings.assert_awaited_once_with(OWNER, 'claude', form.model_dump())
        payload = self.service.save_settings.await_args.args[2]
        self.assertEqual(payload['expected_machine_id'], 'runner-a')
        self.assertEqual(payload['expected_machine_revision'], REVISION_A)

    async def test_start_login_form_forwards_method_and_both_host_guards(self):
        form = self.router.LoginForm(
            method='device', expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )
        result = await self.router.start_subscription_login('claude', form, user=self.admin)
        self.assertEqual(result, {'state': 'waiting'})
        self.service.start_login.assert_awaited_once_with(
            OWNER, 'claude', 'device', 'runner-a', REVISION_A, machine_id=None
        )

    async def test_login_code_form_forwards_code_and_both_host_guards(self):
        form = self.router.LoginCodeForm(
            code='fixture-code', expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )
        await self.router.submit_subscription_login_code('claude', form, user=self.admin)
        self.service.submit_login_code.assert_awaited_once_with(
            OWNER, 'claude', 'fixture-code', 'runner-a', REVISION_A, machine_id=None
        )

    async def test_login_progress_query_forwards_both_host_guards(self):
        await self.router.get_subscription_login(
            'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.login_state.assert_awaited_once_with(OWNER, 'claude', 'runner-a', REVISION_A, machine_id=None)

    async def test_delete_login_query_forwards_both_host_guards_to_cancel_helper(self):
        await self.router.cancel_subscription_login(
            'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.cancel_login.assert_awaited_once_with(OWNER, 'claude', 'runner-a', REVISION_A, machine_id=None)

    async def test_post_logout_query_forwards_both_guards_to_action_and_description(self):
        await self.router.logout_subscription(
            'claude', expected_machine_id='runner-a', expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.logout.assert_awaited_once_with(OWNER, 'claude', 'runner-a', REVISION_A, machine_id=None)
        self.service.describe_provider.assert_awaited_once_with(
            OWNER, 'claude', fresh=True, expected_machine_id='runner-a', expected_machine_revision=REVISION_A
        )

    async def test_computer_settings_routes_forward_the_revision_and_paths(self):
        self.service.describe_computer_settings = AsyncMock(return_value={'machine_id': 'runner-b'})
        self.service.save_computer_settings = AsyncMock(return_value={'machine_id': 'runner-b'})
        await self.router.get_computer_settings('runner-b', user=self.admin)
        self.service.describe_computer_settings.assert_awaited_once_with(OWNER, 'runner-b')
        form = self.router.ComputerSettingsForm(
            expected_revision=REVISION_B, start_folder='C:\\code', cli_paths={'claude': 'C:\\tools\\claude.exe'}
        )
        await self.router.save_computer_settings('runner-b', form, user=self.admin)
        self.service.save_computer_settings.assert_awaited_once_with(
            OWNER, 'runner-b', REVISION_B, 'C:\\code', {'claude': 'C:\\tools\\claude.exe'}
        )

    async def test_check_again_asks_every_computer_and_a_refused_computer_is_a_bad_request(self):
        await self.router.get_subscription('claude', refresh=True, user=self.admin)
        self.service.describe_provider.assert_awaited_once_with(
            OWNER, 'claude', fresh=True, expected_machine_id=None, expected_machine_revision=None,
            check_computers=True
        )
        self.service.login_state = AsyncMock(side_effect=self.production.SubscriptionError('Fixture empty computer'))
        with self.assertRaises(self.production.HTTPException) as refused:
            await self.router.get_subscription_login('claude', machine_id='', user=self.admin)
        self.assertEqual(refused.exception.status_code, 400)

    async def test_sign_in_routes_forward_the_chosen_computer(self):
        start = self.router.LoginForm(method='browser', expected_machine_revision=REVISION_B, machine_id='runner-b')
        await self.router.start_subscription_login('claude', start, user=self.admin)
        self.service.start_login.assert_awaited_once_with(
            OWNER, 'claude', 'browser', None, REVISION_B, machine_id='runner-b'
        )
        code = self.router.LoginCodeForm(code='fixture-code', expected_machine_revision=REVISION_B, machine_id='runner-b')
        await self.router.submit_subscription_login_code('claude', code, user=self.admin)
        self.service.submit_login_code.assert_awaited_once_with(
            OWNER, 'claude', 'fixture-code', None, REVISION_B, machine_id='runner-b'
        )
        await self.router.get_subscription_login('claude', machine_id='runner-b', user=self.admin)
        self.service.login_state.assert_awaited_once_with(OWNER, 'claude', None, None, machine_id='runner-b')
        await self.router.logout_subscription(
            'claude', expected_machine_revision=REVISION_B, machine_id='runner-b', user=self.admin
        )
        self.service.logout.assert_awaited_once_with(OWNER, 'claude', None, REVISION_B, machine_id='runner-b')
        # The plan's own computer is not what the page confirmed, so it is not checked.
        self.service.describe_provider.assert_awaited_once_with(OWNER, 'claude', fresh=True)

    async def test_provider_description_query_forwards_refresh_and_both_host_guards(self):
        await self.router.get_subscription(
            'claude', refresh=True, expected_machine_id='runner-a',
            expected_machine_revision=REVISION_A, user=self.admin
        )
        self.service.describe_provider.assert_awaited_once_with(
            OWNER, 'claude', fresh=True, expected_machine_id='runner-a', expected_machine_revision=REVISION_A,
            check_computers=True
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
