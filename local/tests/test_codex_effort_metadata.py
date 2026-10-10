"""Offline Codex effort tests: fake app-server, config, accounts, and machines.

Run from the repository root:
    python -B -m unittest discover -s local/tests -p test_codex_effort_metadata.py -v
"""

import asyncio
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions.codex import CodexProvider  # noqa: E402
from open_webui.utils.subscriptions.common import ProviderSettings, TurnRequest  # noqa: E402
from open_webui.utils.subscriptions.conversation import parse_messages  # noqa: E402
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402
from open_webui.utils.subscriptions.streaming import collect_events, failure_events  # noqa: E402
from test_subscription_machines import MemoryConfig, load_service  # noqa: E402


class OfflineMachine:
    id = 'local'
    name = 'Fixture host'

    async def start_process(self, *args, **kwargs):
        raise AssertionError('These tests must not execute a CLI or native process')

    async def find_tool(self, *args, **kwargs):
        raise AssertionError('These tests must not discover host tools or credentials')


class OfflineAppServer:
    """Only model metadata and chat RPCs; no account or filesystem operations."""

    generation = 1
    running = True

    def __init__(self, efforts=('low', 'medium', 'high', 'xhigh')):
        self.efforts = list(efforts)
        self.requests = []
        self.queues = {}
        self.model_error = None
        self.restart_during_model_read = False
        self.restart_during_setup = None
        self.thread_count = 0

    async def request(self, method, params=None, timeout=None):
        self.requests.append((method, params))
        if self.restart_during_setup == method:
            self.restart_during_setup = None
            self.generation += 1
            self.efforts = ['low', 'medium']
        if method == 'model/list':
            if self.model_error:
                raise self.model_error
            if self.restart_during_model_read:
                self.generation += 1
            return {'data': [{
                'id': 'fixture-model',
                'supportedReasoningEfforts': [{'reasoningEffort': effort} for effort in self.efforts],
            }]}
        if method == 'config/read':
            return {'config': {}}
        if method == 'thread/start':
            self.thread_count += 1
            return {'thread': {'id': f'fixture-thread-{self.thread_count}'}}
        if method == 'turn/start':
            self.queues[params['threadId']].put_nowait({
                'method': 'turn/completed',
                'params': {'turn': {'id': 'fixture-turn', 'status': 'completed'}},
            })
            return {'turn': {'id': 'fixture-turn'}}
        if method == 'thread/unsubscribe':
            return {}
        raise AssertionError(f'Unexpected app-server operation: {method}')

    def subscribe(self, thread_id):
        queue = asyncio.Queue()
        self.queues[thread_id] = queue
        return queue

    def unsubscribe(self, thread_id):
        self.queues.pop(thread_id)

    def stop(self):
        self.running = False


class CodexEffortMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.machine = OfflineMachine()
        self.server = OfflineAppServer()
        self.provider = CodexProvider()
        self.provider._server_for = AsyncMock(return_value=self.server)

    def turn(self, effort=None, is_task=False, efforts=None):
        return TurnRequest(
            model='fixture-model',
            conversation=parse_messages([{'role': 'user', 'content': 'Fixture prompt'}]),
            settings=ProviderSettings(enable=True),
            cwd='fixture-chat-directory',
            effort=effort,
            is_task=is_task,
            machine=self.machine,
            efforts=[] if efforts is None else list(efforts),
        )

    async def run_turn(self, effort=None, is_task=False, efforts=None):
        return [event async for event in self.provider.run_turn(self.turn(effort, is_task, efforts))]

    def turn_params(self):
        return [params for method, params in self.server.requests if method == 'turn/start']

    def model_reads(self):
        return [params for method, params in self.server.requests if method == 'model/list']

    async def chat_from_rehydrated_registry(self, effort, workers=1, model_efforts=None):
        config = MemoryConfig({'subscriptions.codex': {'enable': True, 'machine_id': 'local'}})
        service = load_service(config, ROOT / 'unused-offline-effort-test-data')
        service.PROVIDERS['codex'] = self.provider
        service.UVICORN_WORKERS = workers
        service.parse_messages = parse_messages
        service.collect_events = collect_events
        service.failure_events = failure_events
        service.get_machine = AsyncMock(return_value=self.machine)
        service.working_directory = AsyncMock(return_value='fixture-chat-directory')

        async def no_database_preset(request, payload, metadata, user):
            return payload['model'], payload

        service._apply_preset = no_database_preset
        service.status_cache._entries['codex'] = service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60,
        )
        model_id = 'codex.fixture-model'
        # A backend restart may load the shared registry without provider discovery.
        registry = json.loads(json.dumps({model_id: {'subscription': {
            'provider': 'codex', 'model': 'fixture-model',
            'efforts': ['low', 'medium', 'high', 'xhigh'] if model_efforts is None else model_efforts,
        }}}))
        return await service.generate_subscription_chat_completion(
            types.SimpleNamespace(state=types.SimpleNamespace()),
            {'model': model_id, 'messages': [{'role': 'user', 'content': 'Fixture prompt'}],
             'reasoning_effort': effort},
            types.SimpleNamespace(role='admin'),
            registry,
        )

    async def test_single_worker_rehydrated_registry_preserves_explicit_efforts(self):
        for effort in ('medium', 'high', 'xhigh'):
            with self.subTest(effort=effort):
                self.setUp()
                await self.chat_from_rehydrated_registry(effort)
                self.assertEqual(self.turn_params()[-1].get('effort'), effort)

    async def test_multiworker_still_blocks_before_any_app_server_request(self):
        with self.assertRaisesRegex(HTTPException, 'require one Buddy backend worker'):
            await self.chat_from_rehydrated_registry('high', workers=2)
        self.assertEqual(self.server.requests, [])

    async def test_cold_unsupported_effort_is_filtered_after_metadata_load(self):
        self.server.efforts = ['low', 'high']
        await self.run_turn('xhigh')
        self.assertEqual(len(self.model_reads()), 1)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_cold_carried_levels_cannot_authorize_an_unsupported_effort(self):
        self.server.generation = 0
        self.server.efforts = ['low']
        await self.run_turn('high', efforts=['high'])
        self.assertEqual(len(self.model_reads()), 1)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_rehydrated_old_host_levels_are_replaced_with_current_server_levels(self):
        self.server.generation = 0
        self.server.efforts = ['low']
        await self.chat_from_rehydrated_registry('high', model_efforts=['high'])
        self.assertEqual(len(self.model_reads()), 1)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_service_forwards_a_copy_of_valid_carried_model_levels(self):
        observed = []
        original = self.provider.run_turn

        async def record(turn):
            observed.append(list(turn.efforts))
            async for event in original(turn):
                yield event

        self.provider.run_turn = record
        await self.chat_from_rehydrated_registry('high', model_efforts=['low', None, 'high'])
        self.assertEqual(observed, [['low', 'high']])

    async def test_malformed_carried_levels_are_ignored_and_current_support_still_filters(self):
        self.server.efforts = ['low']
        await self.chat_from_rehydrated_registry('high', model_efforts='high')
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_warm_authoritative_metadata_avoids_discovery_despite_stale_carried_levels(self):
        self.server.efforts = ['low']
        await self.provider.list_models(ProviderSettings(), self.machine)
        await self.run_turn('high', efforts=['high'])
        self.assertEqual(len(self.model_reads()), 1)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_loaded_metadata_is_reused_for_supported_efforts(self):
        await self.provider.list_models(ProviderSettings(), self.machine)
        await self.run_turn('high')
        await self.run_turn('medium')
        self.assertEqual(len(self.model_reads()), 1)
        self.assertEqual([params['effort'] for params in self.turn_params()], ['high', 'medium'])

    async def test_app_server_restart_refreshes_supported_efforts(self):
        await self.provider.list_models(ProviderSettings(), self.machine)
        self.server.generation += 1
        self.server.efforts = ['low', 'medium']
        await self.run_turn('high')
        self.assertEqual(len(self.model_reads()), 2)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_replacement_app_server_does_not_reuse_old_host_metadata(self):
        await self.provider.list_models(ProviderSettings(), self.machine)
        self.server = OfflineAppServer(efforts=['medium'])
        self.provider._server_for.return_value = self.server
        await self.run_turn('high')
        self.assertEqual(len(self.model_reads()), 1)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_metadata_failure_does_not_start_a_turn_or_trust_registry_efforts(self):
        self.server.model_error = SubscriptionError('Fixture metadata unavailable')
        with self.assertRaisesRegex(SubscriptionError, 'metadata unavailable'):
            await self.run_turn('high')
        self.assertEqual(self.turn_params(), [])

    async def test_restart_during_metadata_load_does_not_cache_or_start_a_turn(self):
        self.server.restart_during_model_read = True
        with self.assertRaisesRegex(SubscriptionError, 'restarted while loading model metadata'):
            await self.run_turn('high')
        self.assertEqual(self.turn_params(), [])
        self.assertEqual(self.provider._model_efforts, {})

    async def test_restart_during_thread_setup_refreshes_before_resolving_effort(self):
        for method in ('config/read', 'thread/start'):
            with self.subTest(method=method):
                self.setUp()
                self.server.restart_during_setup = method
                await self.run_turn('high')
                self.assertEqual(len(self.model_reads()), 2)
                self.assertNotIn('effort', self.turn_params()[-1])
                self.assertEqual(self.provider._model_efforts_generation, self.server.generation)

    async def test_known_empty_supported_levels_do_not_reload_or_accept_an_effort(self):
        self.server.efforts = []
        await self.run_turn('high', efforts=['high'])
        await self.run_turn('medium', efforts=['medium'])
        self.assertEqual(len(self.model_reads()), 1)
        self.assertTrue(all('effort' not in params for params in self.turn_params()))

    async def test_provider_stop_clears_effort_metadata(self):
        await self.provider.list_models(ProviderSettings(), self.machine)
        self.provider.stop()
        self.assertEqual(self.provider._model_efforts, {})
        self.server = OfflineAppServer(efforts=['medium'])
        self.provider._server_for.return_value = self.server
        await self.run_turn('high')
        self.assertEqual(len(self.model_reads()), 1)
        self.assertNotIn('effort', self.turn_params()[-1])

    async def test_tasks_and_minimal_use_the_current_models_lowest_effort(self):
        self.server.efforts = ['medium', 'high']
        await self.run_turn('high', is_task=True)
        await self.run_turn('minimal')
        self.assertEqual([params['effort'] for params in self.turn_params()], ['medium', 'medium'])

    async def test_unspecified_effort_does_not_require_model_discovery(self):
        await self.run_turn()
        self.assertEqual(self.model_reads(), [])
        self.assertNotIn('effort', self.turn_params()[-1])


if __name__ == '__main__':
    unittest.main()
