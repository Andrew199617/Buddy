"""Ultra turns on the server.

The person's worker choice resolves on the computer that runs the turn, and
while the turn runs, the person can stop its workers one by one, through
the stop route of their own chat. Stopping the chat stops them all.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_turns.py -v
"""

import ast
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_provider_host_transitions import (
    ActionCodexProvider,
    ActionProvider,
    OfflineStreamingResponse,
    ProcessMachine,
    fixture_collect_events,
    fixture_failure_events,
    fixture_stream_events,
)
from test_subscription_machines import (
    OWNER,
    OWNER_USER,
    ROOT,
    MemoryConfig,
    enabled_settings,
    load_service,
    machine_config,
    owner_providers,
)
import test_ultra_claude as claude_replay
import test_ultra_codex as codex_replay

# After the test helpers put backend/ on the path.
from open_webui.utils.subscriptions import claude_code, codex, streaming, ultra  # noqa: E402
from open_webui.utils.subscriptions.common import ProviderModel  # noqa: E402
from open_webui.utils.subscriptions.conversation import parse_messages  # noqa: E402
from open_webui.utils.subscriptions.events import StatusUpdate, SubscriptionError, WorkerUpdate  # noqa: E402

PROJECT = 'C:\\code\\site'
CHAT_MODEL_ID = 'claude-code.fixture-model'
WORKER_MODEL_ID = 'claude-code.fixture-worker'
CODEX_MODEL_ID = 'codex.fixture-codex'
SAME_AS_CHAT = {'worker_model': None, 'worker_effort': None}
OFF_FOR_CHAT_MODEL = "Ultra is off for this reply: Claude on your computer can't run workers with Fixture."
OFF_FOR_WORKER_MODEL = "Ultra is off for this reply: Claude on your computer can't run workers with Fixture Worker."
OTHER_PROVIDER_MESSAGE = "Ultra's workers run on this chat's Claude plan. Choose one of its models for them."
NOT_OFFERED_MESSAGE = "Claude on your computer does not offer the workers' model. Choose another worker model."
OFF_UNLISTED = "Ultra is off for this reply: Claude on your computer hasn't listed this chat's model yet."
# What another user's computer reported under the same model IDs.
OTHER_USERS_TEXT = 'Text from another user for your orchestrator'
ROUTER_PATH = ROOT / 'backend' / 'open_webui' / 'routers' / 'subscriptions.py'
WORKER_NOT_RUNNING = "That worker isn't running any more."
CHAT_NOT_FOUND = 'That chat no longer exists.'
# Worker ids as Claude Code and Codex make them: a task id and a thread id.
CLAUDE_WORKER_ID = 'a11e9815d2c932410'
OTHER_CLAUDE_WORKER_ID = 'a3531475ffe57698f'
CODEX_WORKER_ID = '01a15c83-ea2e-7c33-a7e8-6bbabc140f75'


def plan_model(key, name, efforts, ultra=True):
    return ProviderModel(key=key, value=f'{key}-cli', name=name, efforts=list(efforts), ultra=ultra)


class HeldTurn:
    """A fake provider turn that runs until the test ends it, with the workers it runs meanwhile."""

    def __init__(self, *worker_ids):
        self.running_workers = set(worker_ids)
        # The request the service passed to run_turn, once the turn has started.
        self.request = None
        self.started = asyncio.Event()
        self.over = False
        self._end = asyncio.Event()
        self._failure = None

    def end(self, failure=None):
        """Let the turn finish, or fail with this error."""
        self._failure = failure
        self._end.set()

    async def run(self, request):
        self.request = request
        self.started.set()
        try:
            await self._end.wait()
            if self._failure is not None:
                raise self._failure
            yield {'text': 'fixture response'}
        finally:
            self.over = True

    def stop_worker(self, worker_id):
        """As the providers do: False once the turn is over or the worker isn't running."""
        if self.over or worker_id not in self.running_workers:
            return False
        self.running_workers.remove(worker_id)
        return True


class UltraServiceTestCase(unittest.IsolatedAsyncioTestCase):
    """A chat on the owner's computer, with fake Claude and Codex plans that can run Ultra."""

    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [machine_config()],
            'subscriptions.claude': enabled_settings('runner-a'),
            'subscriptions.codex': enabled_settings('runner-a'),
        })
        self.service = load_service(self.config, ROOT / 'unused-ultra-turn-data')
        self.providers = {'claude': ActionProvider(), 'codex': ActionCodexProvider()}
        self.service._providers = owner_providers(self.service, self.providers)
        machine_configs = self.config.values['subscriptions.machines']
        self.machines = {config['id']: ProcessMachine(config) for config in machine_configs}
        self.service._remote_machines = dict(self.machines)
        self.service.parse_messages = parse_messages
        self.service.collect_events = fixture_collect_events
        self.service.failure_events = fixture_failure_events
        self.service.stream_events = fixture_stream_events
        self.service.StreamingResponse = OfflineStreamingResponse

        async def preset(request, payload, metadata, user, models):
            return payload['model'], payload, False

        self.service._apply_preset = preset

        self.claude_models = [
            plan_model('fixture-model', 'Fixture', ['low', 'medium', 'high']),
            plan_model('fixture-worker', 'Fixture Worker', ['low', 'max']),
        ]
        self.codex_models = [plan_model('fixture-codex', 'Fixture Codex', ['low', 'xhigh'])]
        self.signed_in('claude', self.claude_models)
        self.signed_in('codex', self.codex_models)
        self.registry = {
            CHAT_MODEL_ID: self.registry_entry('claude', 'fixture-model', 'Fixture', ['low', 'medium', 'high']),
            WORKER_MODEL_ID: self.registry_entry('claude', 'fixture-worker', 'Fixture Worker', ['low', 'max']),
            CODEX_MODEL_ID: self.registry_entry('codex', 'fixture-codex', 'Fixture Codex', ['low', 'xhigh']),
            # A Workspace preset carries no plan details of its own.
            'fixture-preset': {'name': 'Fixture preset', 'preset': True},
        }

        self.service.project_folders.Folders.add('website', user_id=OWNER, name='Website')
        self.service.Chats.add('chat-1', OWNER, 'website')
        self.turns = []

        async def record_turn(turn):
            self.turns.append(turn)
            yield {'text': 'fixture response'}

        self.providers['claude'].turn_hook = record_turn
        self.providers['codex'].turn_hook = record_turn

        # No worker limit unless a test sets one.
        environment = dict(os.environ)
        environment.pop(ultra.MAX_WORKERS_ENV, None)
        environment_patch = patch.dict(os.environ, environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

    def registry_entry(self, provider_id, key, name, efforts, ultra=True):
        """A model registry entry, built the way the server builds them from a model list."""
        return self.service._model_entry(provider_id, plan_model(key, name, efforts, ultra))

    def other_users_entry(self, key, ultra=True):
        """The shared registry's entry after another user's computer listed a model under the same ID."""
        model = ProviderModel(key=key, value='other-user-cli', name=OTHER_USERS_TEXT, efforts=['other'], ultra=ultra)
        return self.service._model_entry('claude', model)

    def add_computer(self, machine_id):
        """Another of the person's computers. Model lists ask only the plan's own computer."""
        config = machine_config(machine_id, f'host-{machine_id}')
        self.config.values['subscriptions.machines'].append(config)
        self.machines[machine_id] = ProcessMachine(config)
        self.service._remote_machines[machine_id] = self.machines[machine_id]

    def signed_in(self, provider_id, models, machine_id='runner-a'):
        """The plan is signed in on the computer, which listed these models, or none when models is None."""
        on_computer = self.service.ProviderRef(OWNER, provider_id).on(machine_id)
        self.service.status_cache._entries[on_computer] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        if models is not None:
            self.service.models_cache._entries[on_computer] = self.service._CacheEntry(models, time.monotonic() + 60)

    def forget_model_list(self, provider_id):
        """As right after a restart, before the computer's model list loads again."""
        on_computer = self.service.ProviderRef(OWNER, provider_id).on('runner-a')
        del self.service.models_cache._entries[on_computer]

    async def bind(self, access='full', path=PROJECT, machine_id='runner-a'):
        folder = {'mode': 'auto'}
        if path is not None:
            folder = {'mode': 'path', 'path': path}
        request = {
            'kind': 'computer', 'machine_id': machine_id, 'expected_revision': f'fixture-revision-{machine_id}',
            'folder': folder, 'access': access,
        }
        await self.service.bind_chat_target(OWNER_USER, 'chat-1', request)

    async def chat(self, ultra_choice=SAME_AS_CHAT, model=CHAT_MODEL_ID, system=None, stream=False, **metadata):
        """Send one chat turn, as main.py hands it on with the person's Ultra choice in metadata."""
        messages = [{'role': 'user', 'content': 'Fixture message'}]
        if system:
            messages.insert(0, {'role': 'system', 'content': system})
        metadata.setdefault('chat_id', 'chat-1')
        if ultra_choice is not None:
            metadata['ultra'] = ultra_choice
        self.form = {'model': model, 'messages': messages, 'metadata': metadata, 'stream': stream}
        return await self.service.generate_subscription_chat_completion(
            types.SimpleNamespace(state=types.SimpleNamespace()), self.form, OWNER_USER, self.registry,
        )

    def options(self, **choices):
        """UltraOptions as the service under test builds them."""
        return self.service.UltraOptions(**choices)


class UltraTurnTests(UltraServiceTestCase):
    async def test_same_as_chat_means_the_workers_inherit_the_chats_model_and_level(self):
        await self.bind()
        await self.chat()
        turn = self.turns[0]
        self.assertEqual(turn.ultra_options, self.options())
        self.assertIs(turn.ultra, turn.ultra_options)

    async def test_the_workers_model_and_level_resolve_on_the_computer(self):
        await self.bind()
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'max'})
        options = self.turns[0].ultra_options
        self.assertEqual(
            (options.worker_model, options.worker_label, options.worker_effort),
            ('fixture-worker-cli', 'Fixture Worker', 'max'),
        )

    async def test_a_level_the_workers_model_does_not_offer_is_dropped(self):
        await self.bind()
        # The chat's model offers high, but the workers' model doesn't.
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'high'})
        await self.chat({'worker_model': None, 'worker_effort': 'max'})
        await self.chat({'worker_model': None, 'worker_effort': 'high'})
        efforts = [turn.ultra_options.worker_effort for turn in self.turns]
        self.assertEqual(efforts, [None, None, 'high'])
        self.assertEqual(self.turns[0].ultra_options.worker_model, 'fixture-worker-cli')

    async def test_a_worker_model_from_another_provider_or_a_preset_is_refused(self):
        await self.bind()
        for worker_model in (CODEX_MODEL_ID, 'fixture-preset'):
            events = await self.chat({'worker_model': worker_model, 'worker_effort': None})
            self.assertEqual(events, [{'error': OTHER_PROVIDER_MESSAGE}])
        self.assertEqual(self.turns, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_a_worker_model_the_computer_does_not_offer_is_refused(self):
        await self.bind()
        self.signed_in('claude', self.claude_models[:1])
        # The shared registry knows the first, but this computer offers neither.
        for worker_model in (WORKER_MODEL_ID, 'claude-code.not-listed'):
            events = await self.chat({'worker_model': worker_model, 'worker_effort': None})
            self.assertEqual(events, [{'error': NOT_OFFERED_MESSAGE}])
        self.assertEqual(self.turns, [])

    async def test_ultra_never_takes_a_model_or_name_from_the_shared_registry(self):
        await self.bind()
        self.registry[CHAT_MODEL_ID] = self.other_users_entry('fixture-model', ultra=False)
        self.registry[WORKER_MODEL_ID] = self.other_users_entry('fixture-worker')
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'max'})
        turn = self.turns[0]
        options = self.options(worker_model='fixture-worker-cli', worker_label='Fixture Worker', worker_effort='max')
        self.assertEqual(turn.ultra_options, options)
        self.assertNotIn(OTHER_USERS_TEXT, turn.conversation.system)

    async def test_why_ultra_is_off_names_the_model_as_the_computer_lists_it(self):
        await self.bind()
        self.claude_models[0].ultra = False
        self.registry[CHAT_MODEL_ID] = self.other_users_entry('fixture-model')
        events = await self.chat()
        self.assertIsNone(self.turns[0].ultra_options)
        self.assertEqual(events[0], StatusUpdate(OFF_FOR_CHAT_MODEL))

    async def test_after_a_restart_the_computer_lists_its_models_for_ultra(self):
        await self.bind()
        self.forget_model_list('claude')
        self.registry[WORKER_MODEL_ID] = self.other_users_entry('fixture-worker')
        self.providers['claude'].models_hook = AsyncMock(return_value=self.claude_models)
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'max'})
        options = self.options(worker_model='fixture-worker-cli', worker_label='Fixture Worker', worker_effort='max')
        self.assertEqual(self.turns[0].ultra_options, options)
        self.assertIn(('list_models', 'runner-a', 'runner-a'), self.providers['claude'].calls)

    async def test_on_a_computer_without_a_model_list_the_persons_own_list_decides(self):
        self.add_computer('runner-b')
        self.signed_in('claude', None, 'runner-b')
        await self.bind(machine_id='runner-b')
        self.registry[WORKER_MODEL_ID] = self.other_users_entry('fixture-worker')
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'max'})
        turn = self.turns[0]
        self.assertEqual(turn.settings.machine_id, 'runner-b')
        # The plan's own computer listed the workers' model.
        options = self.options(worker_model='fixture-worker-cli', worker_label='Fixture Worker', worker_effort='max')
        self.assertEqual(turn.ultra_options, options)
        self.assertNotIn(('list_models', 'runner-b', 'runner-b'), self.providers['claude'].calls)

    async def test_ultra_is_off_while_no_model_list_has_the_chats_model(self):
        await self.bind()
        self.forget_model_list('claude')
        # The CLI lists nothing, so only the shared registry knows the chat's model.
        events = await self.chat()
        self.assertIsNone(self.turns[0].ultra_options)
        self.assertEqual(events, [StatusUpdate(OFF_UNLISTED), {'text': 'fixture response'}])

    async def test_the_model_list_says_which_models_run_ultra(self):
        # The composer offers Ultra, and lists workers' models, from these flags.
        self.claude_models[1].ultra = False
        models = await self.service.get_user_subscription_models(OWNER_USER)
        flags = {model['id']: model['subscription']['ultra'] for model in models}
        self.assertEqual(flags, {CHAT_MODEL_ID: True, WORKER_MODEL_ID: False, CODEX_MODEL_ID: True})

    async def test_a_chat_model_without_ultra_runs_normally_and_says_why(self):
        await self.bind()
        self.claude_models[0].ultra = False
        events = await self.chat(system='Folder instructions.')
        turn = self.turns[0]
        self.assertIsNone(turn.ultra_options)
        self.assertEqual(turn.conversation.system, 'Folder instructions.')
        self.assertEqual(events, [StatusUpdate(OFF_FOR_CHAT_MODEL), {'text': 'fixture response'}])

    async def test_a_worker_model_without_ultra_turns_it_off_for_the_reply(self):
        await self.bind()
        self.claude_models[1].ultra = False
        events = await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': None})
        self.assertIsNone(self.turns[0].ultra_options)
        self.assertEqual(events[0], StatusUpdate(OFF_FOR_WORKER_MODEL))

    async def test_a_streamed_reply_starts_with_why_ultra_is_off(self):
        await self.bind()
        self.claude_models[0].ultra = False
        response = await self.chat(stream=True)
        events = [event async for event in response.body_iterator]
        self.assertEqual(events, [StatusUpdate(OFF_FOR_CHAT_MODEL), {'text': 'fixture response'}])

    async def test_tasks_and_turns_without_ultra_get_no_workers_note_or_status(self):
        await self.bind()
        self.claude_models[0].ultra = False
        no_choice = await self.chat(None)
        task = await self.chat(SAME_AS_CHAT, task='title_generation')
        self.assertEqual(no_choice, [{'text': 'fixture response'}])
        self.assertEqual(task, [{'text': 'fixture response'}])
        self.claude_models[0].ultra = True
        await self.chat(SAME_AS_CHAT, task='follow_up_generation')
        self.assertEqual([turn.ultra_options for turn in self.turns], [None, None, None])
        self.assertEqual([turn.conversation.system for turn in self.turns], ['', '', ''])

    async def test_the_orchestrator_note_leads_the_instructions_for_this_turn_only(self):
        await self.bind(path=None)
        await self.chat(system='Folder instructions.')
        system = self.turns[0].conversation.system
        note = ultra.orchestrator_note('claude', 'full', self.options())
        self.assertTrue(system.startswith(f'{note}\n\nNo project folder is set for this chat'))
        self.assertTrue(system.endswith('Folder instructions.'))
        # The chat's own messages are not changed, so the note is never saved with it.
        self.assertEqual(
            self.form['messages'],
            [{'role': 'system', 'content': 'Folder instructions.'}, {'role': 'user', 'content': 'Fixture message'}],
        )

    async def test_the_note_follows_the_turns_access_and_the_workers_choice(self):
        await self.bind(access='read')
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'low'})
        options = self.options(worker_model='fixture-worker-cli', worker_label='Fixture Worker', worker_effort='low')
        self.assertEqual(self.turns[0].ultra_options, options)
        self.assertEqual(self.turns[0].conversation.system, ultra.orchestrator_note('claude', 'read', options))

    async def test_the_admins_worker_limit_reaches_the_turn(self):
        await self.bind()
        os.environ[ultra.MAX_WORKERS_ENV] = '3'
        await self.chat()
        self.assertEqual(self.turns[0].ultra_options.max_workers, 3)
        self.assertIn('At most 3 workers can run at once', self.turns[0].conversation.system)

    async def test_a_codex_turn_gets_codex_instructions(self):
        # Outside chats, the turn runs where the plan runs.
        await self.chat({'worker_model': CODEX_MODEL_ID, 'worker_effort': 'xhigh'}, model=CODEX_MODEL_ID, chat_id=None)
        turn = self.turns[0]
        options = self.options(worker_model='fixture-codex-cli', worker_label='Fixture Codex', worker_effort='xhigh')
        self.assertEqual(turn.ultra_options, options)
        self.assertEqual(turn.conversation.system, ultra.orchestrator_note('codex', 'full', options))
        self.assertIn('Start every worker with model `fixture-codex-cli`', turn.conversation.system)


class LiveUltraTurnTestCase(UltraServiceTestCase):
    """Ultra turns that run until the test ends them, with the stops the providers were asked for."""

    def setUp(self):
        super().setUp()
        # The held turns that the next turns run as, in order.
        self.next_turns = []
        self.held_turns = []
        # The stops the providers were asked for: (provider id, worker id).
        self.stops = []
        self.providers['claude'].turn_hook = self.run_next_turn
        self.providers['codex'].turn_hook = self.run_next_turn
        self.providers['claude'].stop_worker = self.claude_stop_worker
        self.providers['codex'].stop_worker = self.codex_stop_worker

    async def run_next_turn(self, request):
        held = self.next_turns.pop(0)
        self.held_turns.append(held)
        async for event in held.run(request):
            yield event

    async def claude_stop_worker(self, request, worker_id):
        return self.stop_held_worker('claude', request, worker_id)

    async def codex_stop_worker(self, request, worker_id):
        return self.stop_held_worker('codex', request, worker_id)

    def stop_held_worker(self, provider_id, request, worker_id):
        """As the providers do, find the turn by the very request that was passed to run_turn."""
        self.stops.append((provider_id, worker_id))
        for held in self.held_turns:
            if held.request is request:
                return held.stop_worker(worker_id)
        return False

    async def start_turn(self, held, ultra_choice=SAME_AS_CHAT, model=CHAT_MODEL_ID, **metadata):
        """Send a chat turn that runs as this held turn, and return once the provider runs it."""
        self.next_turns.append(held)
        reply = asyncio.create_task(self.chat(ultra_choice, model=model, **metadata))
        await asyncio.wait_for(held.started.wait(), 5)
        return reply

    def live_turns(self, chat_id='chat-1'):
        return self.service._live_ultra_turns.get(chat_id, [])

    async def stop_worker(self, worker_id, user=OWNER_USER, chat_id='chat-1'):
        return await self.service.stop_worker(user, chat_id, worker_id)


class LiveUltraTurnTests(LiveUltraTurnTestCase):
    async def test_an_ultra_turn_is_live_from_its_start_until_it_ends(self):
        await self.bind()
        held = HeldTurn('worker-1')
        reply = await self.start_turn(held, message_id='message-1')
        [live] = self.live_turns()
        self.assertEqual(
            (live.chat_id, live.message_id, live.owner_id, live.provider_id),
            ('chat-1', 'message-1', OWNER, 'claude'),
        )
        self.assertIs(live.provider, self.providers['claude'])
        self.assertIs(live.turn, held.request)
        held.end()
        self.assertEqual(await reply, [{'text': 'fixture response'}])
        self.assertEqual(self.service._live_ultra_turns, {})

    async def test_stopping_a_worker_asks_the_provider_that_runs_the_turn(self):
        await self.bind()
        held = HeldTurn('worker-1', 'worker-2')
        reply = await self.start_turn(held)
        self.assertTrue(await self.stop_worker('worker-2'))
        self.assertEqual(held.running_workers, {'worker-1'})
        self.assertEqual(self.stops, [('claude', 'worker-2')])
        held.end()
        await reply

    async def test_a_codex_turns_workers_are_stopped_by_codex(self):
        await self.bind()
        held = HeldTurn('worker-thread-1')
        reply = await self.start_turn(held, model=CODEX_MODEL_ID)
        [live] = self.live_turns()
        self.assertEqual(live.provider_id, 'codex')
        self.assertTrue(await self.stop_worker('worker-thread-1'))
        self.assertEqual(self.stops, [('codex', 'worker-thread-1')])
        held.end()
        await reply

    async def test_a_worker_that_is_not_running_is_not_stopped(self):
        await self.bind()
        held = HeldTurn('worker-1')
        reply = await self.start_turn(held)
        self.assertTrue(await self.stop_worker('worker-1'))
        # Stopped already, and never started.
        self.assertFalse(await self.stop_worker('worker-1'))
        self.assertFalse(await self.stop_worker('worker-2'))
        held.end()
        await reply

    async def test_nothing_is_stopped_without_a_live_turn_in_the_chat(self):
        await self.bind()
        self.assertFalse(await self.stop_worker('worker-1'))
        held = HeldTurn('worker-1')
        reply = await self.start_turn(held)
        self.assertFalse(await self.stop_worker('worker-1', chat_id='chat-2'))
        held.end()
        await reply
        # The turn has ended, so its workers have too.
        self.assertFalse(await self.stop_worker('worker-1'))
        self.assertEqual(self.stops, [])

    async def test_only_the_person_who_runs_the_turn_can_stop_its_workers(self):
        await self.bind()
        held = HeldTurn('worker-1')
        reply = await self.start_turn(held)
        someone_else = types.SimpleNamespace(id='user-2', role='admin')
        self.assertFalse(await self.stop_worker('worker-1', user=someone_else))
        self.assertEqual(self.stops, [])
        self.assertEqual(held.running_workers, {'worker-1'})
        held.end()
        await reply

    async def test_a_failed_turn_leaves_the_registry(self):
        await self.bind()
        held = HeldTurn('worker-1')
        reply = await self.start_turn(held)
        # As when the app-server ends during the turn.
        held.end(SubscriptionError('Fixture app-server ended'))
        with self.assertRaises(SubscriptionError):
            await reply
        self.assertEqual(self.service._live_ultra_turns, {})

    async def test_a_stopped_reply_leaves_the_registry_once_the_provider_has_stopped(self):
        await self.bind()
        held = HeldTurn('worker-1')
        reply = await self.start_turn(held)
        # As when the person stops the chat, which cancels the reply's task.
        reply.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reply
        self.assertTrue(held.over)
        self.assertEqual(self.service._live_ultra_turns, {})
        self.assertFalse(await self.stop_worker('worker-1'))

    async def test_a_streamed_reply_is_live_while_it_streams(self):
        await self.bind()
        held = HeldTurn('worker-1')
        response = await self.chat(stream=True)
        self.next_turns.append(held)
        # Nothing runs until the reply is read.
        self.assertEqual(self.service._live_ultra_turns, {})
        reading = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(held.started.wait(), 5)
        [live] = self.live_turns()
        self.assertIs(live.turn, held.request)
        held.end()
        self.assertEqual(await reading, {'text': 'fixture response'})
        with self.assertRaises(StopAsyncIteration):
            await anext(response.body_iterator)
        self.assertEqual(self.service._live_ultra_turns, {})

    async def test_turns_without_ultra_are_never_live(self):
        await self.bind()
        without_ultra = HeldTurn()
        title = HeldTurn()
        ultra_off = HeldTurn()
        outside_chats = HeldTurn()
        replies = [
            await self.start_turn(without_ultra, None),
            await self.start_turn(title, task='title_generation'),
        ]
        self.claude_models[0].ultra = False
        replies.append(await self.start_turn(ultra_off))
        self.claude_models[0].ultra = True
        replies.append(await self.start_turn(outside_chats, chat_id=None))
        self.assertIsNotNone(outside_chats.request.ultra)
        self.assertEqual(self.service._live_ultra_turns, {})
        for held in (without_ultra, title, ultra_off, outside_chats):
            held.end()
        await asyncio.gather(*replies)

    async def test_stopping_a_worker_asks_each_live_turn_of_the_chat(self):
        # A request that names two models runs a turn for each in the same chat.
        await self.bind()
        first = HeldTurn('worker-1')
        second = HeldTurn('worker-2')
        first_reply = await self.start_turn(first, message_id='message-1')
        second_reply = await self.start_turn(second, message_id='message-2')
        message_ids = [live.message_id for live in self.live_turns()]
        self.assertEqual(message_ids, ['message-1', 'message-2'])
        self.assertTrue(await self.stop_worker('worker-2'))
        self.assertEqual((first.running_workers, second.running_workers), ({'worker-1'}, set()))
        first.end()
        await first_reply
        [live] = self.live_turns()
        self.assertIs(live.turn, second.request)
        second.end()
        await second_reply
        self.assertEqual(self.service._live_ultra_turns, {})


def router_functions(*names):
    """The router's own definitions of these functions, parsed from its source."""
    tree = ast.parse(ROUTER_PATH.read_text(encoding='utf-8'))
    functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return [functions[name] for name in names]


def load_stop_route(service, chats):
    """The real stop route and its id check, run without FastAPI, sign-in or the database.

    ``chats`` answers whether the person owns a chat. The tests pass the
    signed-in user to the route themselves.
    """
    nodes = router_functions('_is_plain_id', 'stop_chat_worker')
    for node in nodes:
        node.decorator_list = []
    module = types.ModuleType('ultra_stop_route_under_test')
    module.__dict__.update({
        're': re,
        'Depends': lambda dependency: None,
        'get_machine_session_user': None,
        'HTTPException': service.HTTPException,
        'status': types.SimpleNamespace(HTTP_404_NOT_FOUND=404),
        'Chats': chats,
        'service': service,
    })
    tree = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(tree, str(ROUTER_PATH), 'exec'), module.__dict__)
    return module.stop_chat_worker


class OwnerChecks:
    """The service's chats, noting each chat the route asks about."""

    def __init__(self, chats):
        self.chats = chats
        self.asked = []

    async def is_chat_owner(self, chat_id, user_id):
        self.asked.append(chat_id)
        return await self.chats.is_chat_owner(chat_id, user_id)


class StopWorkerRouteTests(LiveUltraTurnTestCase):
    def setUp(self):
        super().setUp()
        self.owner_checks = OwnerChecks(self.service.Chats)
        self.stop_route = load_stop_route(self.service, self.owner_checks)

    async def post_stop(self, worker_id, chat_id='chat-1', user=OWNER_USER):
        return await self.stop_route(chat_id, worker_id, user=user)

    async def assert_not_found(self, detail, worker_id, chat_id='chat-1', user=OWNER_USER):
        with self.assertRaises(self.service.HTTPException) as refused:
            await self.post_stop(worker_id, chat_id, user)
        self.assertEqual((refused.exception.status_code, str(refused.exception)), (404, detail))

    def test_the_route_is_a_post_that_needs_a_signed_in_session(self):
        # Like changing machines or sign-ins, stopping a worker is refused to API keys.
        [route] = router_functions('stop_chat_worker')
        decorators = [ast.unparse(decorator) for decorator in route.decorator_list]
        self.assertEqual(decorators, ["router.post('/chats/{chat_id}/workers/{worker_id}/stop')"])
        user_default = route.args.defaults[-1]
        self.assertEqual(ast.unparse(user_default), 'Depends(get_machine_session_user)')

    async def test_the_owner_stops_a_running_claude_worker(self):
        await self.bind()
        held = HeldTurn(CLAUDE_WORKER_ID, OTHER_CLAUDE_WORKER_ID)
        reply = await self.start_turn(held)
        self.assertEqual(await self.post_stop(CLAUDE_WORKER_ID), {'stopped': True})
        self.assertEqual(self.stops, [('claude', CLAUDE_WORKER_ID)])
        self.assertEqual(held.running_workers, {OTHER_CLAUDE_WORKER_ID})
        held.end()
        await reply

    async def test_the_owner_stops_a_running_codex_worker(self):
        await self.bind()
        held = HeldTurn(CODEX_WORKER_ID)
        reply = await self.start_turn(held, model=CODEX_MODEL_ID)
        self.assertEqual(await self.post_stop(CODEX_WORKER_ID), {'stopped': True})
        self.assertEqual(self.stops, [('codex', CODEX_WORKER_ID)])
        self.assertEqual(held.running_workers, set())
        held.end()
        await reply

    async def test_a_worker_that_is_not_running_is_not_found(self):
        await self.bind()
        # No reply runs in the chat yet.
        await self.assert_not_found(WORKER_NOT_RUNNING, CLAUDE_WORKER_ID)
        held = HeldTurn(CLAUDE_WORKER_ID)
        reply = await self.start_turn(held)
        await self.post_stop(CLAUDE_WORKER_ID)
        # Stopped already, and never started.
        await self.assert_not_found(WORKER_NOT_RUNNING, CLAUDE_WORKER_ID)
        await self.assert_not_found(WORKER_NOT_RUNNING, OTHER_CLAUDE_WORKER_ID)
        held.end()
        await reply
        # The reply has ended, and its workers with it.
        await self.assert_not_found(WORKER_NOT_RUNNING, CLAUDE_WORKER_ID)

    async def test_someone_elses_chat_is_answered_as_if_it_did_not_exist(self):
        await self.bind()
        held = HeldTurn(CLAUDE_WORKER_ID)
        reply = await self.start_turn(held)
        someone_else = types.SimpleNamespace(id='user-2', role='admin')
        await self.assert_not_found(CHAT_NOT_FOUND, CLAUDE_WORKER_ID, user=someone_else)
        await self.assert_not_found(CHAT_NOT_FOUND, CLAUDE_WORKER_ID, chat_id='chat-gone')
        self.assertEqual(self.stops, [])
        self.assertEqual(held.running_workers, {CLAUDE_WORKER_ID})
        held.end()
        await reply

    async def test_ids_buddy_never_makes_are_refused_before_anything_is_asked(self):
        await self.bind()
        held = HeldTurn(CLAUDE_WORKER_ID)
        reply = await self.start_turn(held)
        bad_chat_ids = ('temporary:chat-1', 'channel:chat-1', '', 'chat 1', 'chat-1\n', 'c' * 129)
        for chat_id in bad_chat_ids:
            with self.subTest(chat_id=chat_id):
                await self.assert_not_found(CHAT_NOT_FOUND, CLAUDE_WORKER_ID, chat_id=chat_id)
        bad_worker_ids = ('', 'a11e9815 d2c932410', f'{CLAUDE_WORKER_ID}\n', f'../{CLAUDE_WORKER_ID}', 'a' * 129)
        for worker_id in bad_worker_ids:
            with self.subTest(worker_id=worker_id):
                await self.assert_not_found(WORKER_NOT_RUNNING, worker_id)
        # Only the well-formed chat id reached the database, and no provider was asked.
        self.assertEqual(set(self.owner_checks.asked), {'chat-1'})
        self.assertEqual(self.stops, [])
        self.assertEqual(held.running_workers, {CLAUDE_WORKER_ID})
        held.end()
        await reply


TASKS_PATH = ROOT / 'backend' / 'open_webui' / 'tasks.py'
MAIN_PATH = ROOT / 'backend' / 'open_webui' / 'main.py'
MIDDLEWARE_PATH = ROOT / 'backend' / 'open_webui' / 'utils' / 'middleware.py'
CHAT_STOPPED = 'All tasks for item chat-1 stopped.'


def load_chat_tasks():
    """tasks.py's own registry of chat tasks and its stop functions, as they run without Redis.

    main.py runs each reply as a task of its chat with create_task. Its stop
    route for a chat calls stop_item_tasks, which cancels each of the chat's
    tasks and waits until it has ended.
    """
    names = {'cleanup_task', 'create_task', 'list_task_ids_by_item_id', 'stop_task', 'stop_item_tasks'}
    tree = ast.parse(TASKS_PATH.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if getattr(node, 'name', None) in names]
    if len(nodes) != len(names):
        raise AssertionError(f'tasks.py no longer defines all of {sorted(names)}')
    # Its Redis annotations then stay unevaluated.
    future_annotations = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = types.ModuleType('chat_tasks_under_test')
    module.__dict__.update({
        'asyncio': asyncio,
        'uuid4': uuid.uuid4,
        'tasks': {},
        'item_tasks': {},
        'response_streams': {},
    })
    tree = ast.fix_missing_locations(ast.Module(body=[future_annotations, *nodes], type_ignores=[]))
    exec(compile(tree, str(TASKS_PATH), 'exec'), module.__dict__)
    return module


def chunk_payload(chunk: str) -> dict:
    """What a chunk of a streamed reply carries, or {} for the stream's last line."""
    if not chunk.startswith('data: {'):
        return {}
    return json.loads(chunk[len('data: '):])


def is_workers_snapshot(chunk: str) -> bool:
    event = chunk_payload(chunk).get('event') or {}
    return event.get('type') == streaming.WORKERS_EVENT_TYPE


def is_reply_text(chunk: str) -> bool:
    for choice in chunk_payload(chunk).get('choices') or []:
        if choice['delta'].get('content'):
            return True
    return False


class ChatReader:
    """Stands in for middleware.py's reader of a streamed reply, which runs as the chat's task.

    It hands each chunk on, to the chat page and the chat's saved copy, before
    it reads the next, and the reply waits meanwhile. When Stop cancels the
    task, it closes the reply's stream and ends, as middleware.py does.
    """

    def __init__(self, response, hold_when=None):
        self.body = response.body_iterator
        self.chunks = []
        # Which chunk the chat is slow to hand on, until Stop lands.
        self.hold_when = hold_when
        self.holding = asyncio.Event()

    async def read(self):
        try:
            async for chunk in self.body:
                self.chunks.append(chunk)
                await self.hand_on(chunk)
        except asyncio.CancelledError:
            await asyncio.shield(self.body.aclose())
            raise

    async def hand_on(self, chunk: str) -> None:
        if self.hold_when is None or not self.hold_when(chunk):
            return
        self.holding.set()
        # Until Stop cancels the chat's task.
        await asyncio.Event().wait()

    def reply(self) -> str:
        texts = []
        for chunk in self.chunks:
            for choice in chunk_payload(chunk).get('choices') or []:
                texts.append(choice['delta'].get('content') or '')
        return ''.join(texts)

    def last_workers_snapshot(self) -> dict:
        snapshots = []
        for chunk in self.chunks:
            if is_workers_snapshot(chunk):
                snapshots.append(chunk_payload(chunk)['event']['data'])
        return snapshots[-1]

    def activities(self) -> dict[str, str | None]:
        """What each worker was doing, by its id, as the last snapshot that reached the chat says."""
        activities = {}
        for item in self.last_workers_snapshot()['items']:
            activities[item['id']] = item['activity']
        return activities


class ReplayComputer(ProcessMachine):
    """The person's computer, with Claude Code and Codex installed."""

    async def find_tool(self, tool, configured_path=''):
        return tool


class ClaudeCodeReplay(claude_replay.ReplayProcess):
    """A replayed ``claude -p`` that can say more while its turn waits."""

    def say(self, *messages: dict) -> None:
        self._say(list(messages))


class CodexReplay(codex_replay.InterruptibleProcess):
    """A replayed, interruptible ``codex app-server`` that can say more while its turn waits."""

    def say(self, *messages: dict) -> None:
        for message in messages:
            self._lines.put_nowait(json.dumps(message))


def claude_worker_step(task_started: dict, description: str) -> dict:
    """What Claude Code says when a worker takes a step.

    It has the fields of the recorded task_progress messages that Buddy reads.
    """
    return {
        'type': 'system',
        'subtype': 'task_progress',
        'task_id': task_started['task_id'],
        'tool_use_id': task_started['tool_use_id'],
        'description': description,
        'usage': {'total_tokens': 2048, 'tool_uses': 0, 'duration_ms': 20000},
    }


def codex_turn_id(messages: list[dict], thread_id: str) -> str:
    """The id of a thread's recorded turn."""
    return codex_replay.turn_start(messages, thread_id)['params']['turn']['id']


def codex_worker_step(messages: list[dict], thread_id: str) -> dict:
    """What a worker's own thread says when it starts to think again, shaped like the recorded item/started messages."""
    item = {'type': 'reasoning', 'id': 'rs_fixture_step', 'summary': [], 'content': []}
    params = {'threadId': thread_id, 'turnId': codex_turn_id(messages, thread_id), 'item': item}
    return {'method': 'item/started', 'params': params}


class ChatStopTests(UltraServiceTestCase):
    """Stopping a chat ends its reply before Stop returns: every worker of an Ultra reply, and the CLI of any reply.

    The reply runs on the real Claude and Codex providers, against Claude Code
    and Codex answering as they did in recorded Ultra turns (see
    test_ultra_claude.py and test_ultra_codex.py), and streams through the real
    stream_events. It runs as the chat's task under tasks.py's own code, read
    by a stand-in for middleware.py's reader, and the chat is stopped with
    tasks.py's stop_item_tasks, as main.py's stop route stops it.

    Each Ultra reply has answered and waits while its workers write on. Stop
    may land while the reply waits for its CLI, or while the chat hands on a
    chunk, such as a worker's latest step, and the reply waits for the chat.
    """

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.claude = claude_code.ClaudeCodeProvider(Path(directory.name))
        self.codex = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(self.codex.stop)
        self.service._providers = owner_providers(self.service, {'claude': self.claude, 'codex': self.codex})
        self.computer = ReplayComputer(self.config.values['subscriptions.machines'][0])
        self.service._remote_machines['runner-a'] = self.computer
        self.service.stream_events = streaming.stream_events
        self.chat_tasks = load_chat_tasks()
        # What the chat's event emitter is sent once Stop has closed the reply's stream.
        self.sent_after_stop = []

        async def chat_event_emitter(request_info):
            async def emit(event):
                self.sent_after_stop.append((request_info, event))

            return emit

        self.service.get_event_emitter = chat_event_emitter
        # A worker's step goes out to the chat at once, rather than at most once a second.
        snapshot_interval = patch.object(streaming, 'WORKER_SNAPSHOT_INTERVAL', 0)
        snapshot_interval.start()
        self.addCleanup(snapshot_interval.stop)

    async def start_reply(self, process, model, hold_when=None):
        """Send a message in the chat. Its streamed reply runs as the chat's task, with ``process`` as its CLI."""
        self.computer.process_factory = lambda: process
        await self.bind()
        # Python closes an async generator that was left unclosed once nothing
        # refers to it, later and in a task of its own. Stop must not rely on
        # that, so here each one is kept, unclosed, where stop_chat() sees it.
        # The loop puts its own hook back when the test ends.
        self.left_unclosed = []
        sys.set_asyncgen_hooks(finalizer=self.left_unclosed.append)
        response = await self.chat(model=model, stream=True, message_id='message-1')
        self.reader = ChatReader(response, hold_when)
        _, task = await self.chat_tasks.create_task(None, self.reader.read(), id='chat-1')
        self.addAsyncCleanup(self.finish, task)

    async def finish(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def stop_chat(self):
        """Stop the chat as main.py's stop route does, and return when that would.

        Each step of the reply, from its stream down to the provider's run of
        the CLI, has been closed by the step that reads it.
        """
        result = await asyncio.wait_for(self.chat_tasks.stop_item_tasks(None, 'chat-1'), 10)
        self.assertEqual(result['message'], CHAT_STOPPED)
        self.assertEqual(self.left_unclosed, [])

    def assert_chat_sent_the_workers_stopped(self):
        """The chat was sent the reply's workers after Stop, with each one that still ran as stopped.

        It no longer reads the reply then, so the snapshot goes to its event
        emitter, which saves it as the message's workers.
        """
        before = self.reader.last_workers_snapshot()
        self.assertGreater(before['running'], 0)
        [(request_info, event)] = self.sent_after_stop
        self.assertEqual((request_info['chat_id'], request_info['message_id']), ('chat-1', 'message-1'))
        self.assertEqual(event['type'], streaming.WORKERS_EVENT_TYPE)
        self.assertEqual(event['data']['running'], 0)
        expected_states = {}
        for item in before['items']:
            if item['state'] == 'running':
                expected_states[item['id']] = 'stopped'
            else:
                expected_states[item['id']] = item['state']
        states = {}
        for item in event['data']['items']:
            states[item['id']] = item['state']
            if item['state'] == 'stopped':
                self.assertIsNotNone(item['ended_at'])
        self.assertEqual(states, expected_states)

    async def claude_reply_with_a_worker_running(self):
        """A Claude reply that has answered twice while its essay worker writes on, as recorded.

        Returns the replayed Claude Code and the essay worker's task_started message.
        """
        answer, turn_output = claude_replay.recorded_turn('claude-background.jsonl')
        process = ClaudeCodeReplay(answer, turn_output, silent=True)
        await self.start_reply(process, CHAT_MODEL_ID)
        await asyncio.wait_for(process.idle.wait(), 5)
        last_result = claude_replay.results(turn_output)[-1]
        self.assertTrue(self.reader.reply().endswith(last_result['result']))
        self.assertEqual(self.reader.last_workers_snapshot()['running'], 1)
        essay = claude_replay.system_messages(turn_output, 'task_started')[0]
        return process, essay

    def assert_claude_workers_stopped(self, process, essay):
        """Claude Code was asked to stop the essay worker and said it had, and then the CLI ended."""
        self.assertEqual(claude_replay.stop_requests(process), [essay['task_id']])
        self.assertEqual(process.calls, ['initialize', 'user', 'stop_task', 'close_stdin', 'kill'])
        self.assertEqual(process.unread_lines(), 0)
        self.assertEqual(self.service._live_ultra_turns, {})

    async def codex_reply_with_workers_running(self):
        """A Codex reply whose orchestrator's turn has ended while its two essay workers write on, as recorded.

        Returns the replayed app-server, the recording, the workers' threads and the live turn.
        """
        messages = codex_replay.recorded('codex-stop-children.jsonl')
        workers = list(codex_replay.recorded_workers(messages).values())
        process = CodexReplay(messages, waiting=workers)
        await self.start_reply(process, CODEX_MODEL_ID)
        await asyncio.wait_for(claude_replay.until(lambda: self.codex_handled(process)), 5)
        [run] = self.codex._ultra_runs
        self.assertTrue(run.parser.finished)
        self.assertEqual(self.reader.last_workers_snapshot()['running'], 2)
        return process, messages, workers, run

    def codex_handled(self, process) -> bool:
        """Whether Buddy has read and handled all that the replayed app-server wrote once the turn started."""
        server = self.codex._servers.get('runner-a')
        if server is None or not process.sent('turn/start') or not process.drained():
            return False
        return all(queue.empty() for queue in server._thread_queues.values())

    def assert_codex_workers_stopped(self, process, messages, workers, run):
        """Each worker's turn was interrupted on its own thread, and Codex said it had ended."""
        interrupts = []
        for thread_id in workers:
            interrupts.append({'threadId': thread_id, 'turnId': codex_turn_id(messages, thread_id)})
        # The orchestrator's turn had ended, so only the workers' turns were interrupted.
        self.assertEqual(process.sent('turn/interrupt'), interrupts)
        self.assertFalse(run.parser.workers.running())
        self.assertEqual(self.codex._ultra_runs, [])
        self.assertEqual(self.service._live_ultra_turns, {})

    def test_the_chats_own_reader_closes_the_reply_when_stopped_as_the_stand_in_does(self):
        # ChatReader stands in for it in the other tests.
        middleware = MIDDLEWARE_PATH.read_text(encoding='utf-8')
        self.assertIn('await asyncio.shield(response.body_iterator.aclose())', middleware)

    async def test_stopping_the_chat_stops_a_claude_replys_workers(self):
        process, essay = await self.claude_reply_with_a_worker_running()
        await self.stop_chat()
        self.assert_claude_workers_stopped(process, essay)
        self.assert_chat_sent_the_workers_stopped()

    async def test_a_stop_while_the_chat_hands_on_a_claude_workers_step_stops_the_workers_too(self):
        process, essay = await self.claude_reply_with_a_worker_running()
        self.reader.hold_when = is_workers_snapshot
        # The essay worker takes a step, and Stop lands while the chat hands it on.
        process.say(claude_worker_step(essay, 'Writing about suspension bridges'))
        await asyncio.wait_for(self.reader.holding.wait(), 5)
        self.assertEqual(self.reader.activities()[essay['task_id']], 'Writing about suspension bridges')
        await self.stop_chat()
        self.assert_claude_workers_stopped(process, essay)
        self.assert_chat_sent_the_workers_stopped()

    async def test_stopping_the_chat_stops_a_codex_replys_workers(self):
        process, messages, workers, run = await self.codex_reply_with_workers_running()
        await self.stop_chat()
        self.assert_codex_workers_stopped(process, messages, workers, run)
        self.assert_chat_sent_the_workers_stopped()

    async def test_a_stop_while_the_chat_hands_on_a_codex_workers_step_stops_the_workers_too(self):
        process, messages, workers, run = await self.codex_reply_with_workers_running()
        self.reader.hold_when = is_workers_snapshot
        # The first worker starts to think again, and Stop lands while the chat hands that on.
        process.say(codex_worker_step(messages, workers[0]))
        await asyncio.wait_for(self.reader.holding.wait(), 5)
        self.assertEqual(self.reader.activities()[workers[0]], 'Thinking')
        await self.stop_chat()
        self.assert_codex_workers_stopped(process, messages, workers, run)
        self.assert_chat_sent_the_workers_stopped()

    async def test_a_stop_while_the_chat_hands_on_text_ends_the_cli_of_a_reply_without_ultra(self):
        # Ultra is off for this reply, which starts by saying why.
        self.claude_models[0].ultra = False
        process = ClaudeCodeReplay(turn_output=claude_replay.plain_reply('Working on it')[:2], silent=True)
        await self.start_reply(process, CHAT_MODEL_ID, hold_when=is_reply_text)
        await asyncio.wait_for(self.reader.holding.wait(), 5)
        first_status = chunk_payload(self.reader.chunks[0])['event']['data']
        self.assertEqual(first_status['description'], OFF_FOR_CHAT_MODEL)
        self.assertEqual(self.reader.reply(), 'Working on it')
        await self.stop_chat()
        self.assertEqual(process.calls, ['user', 'close_stdin', 'kill'])
        # It had no workers to list as stopped.
        self.assertEqual(self.sent_after_stop, [])

    def test_a_reply_outside_a_chat_has_no_chat_to_send_its_workers_to(self):
        self.assertIsNone(self.service._stopped_reply_emitter({'chat_id': 'chat-1'}))
        self.assertIsNone(self.service._stopped_reply_emitter({'message_id': 'message-1'}))
        self.assertIsNotNone(self.service._stopped_reply_emitter({'chat_id': 'chat-1', 'message_id': 'message-1'}))


STOPPED_AT = 1_800_000_100


class SavedChatRows:
    """The saved chat and its chat_message rows, as main.py's stop route reads and writes them.

    The rows have no column for a message's Ultra workers; only the chat's own copy keeps them.
    """

    def __init__(self, reply_workers):
        question = {'id': 'question', 'role': 'user', 'content': 'Fix the parser.', 'done': True}
        reply = {'id': 'message-1', 'parentId': 'question', 'role': 'assistant', 'content': 'Fixed.', 'done': False}
        self.rows = {'question': question, 'message-1': reply}
        saved_reply = dict(reply)
        if reply_workers is not None:
            saved_reply['workers'] = reply_workers
        history = {'currentId': 'message-1', 'messages': {'question': question, 'message-1': saved_reply}}
        self.chat = types.SimpleNamespace(id='chat-1', user_id=OWNER, chat={'history': history})
        self.upserts = []

    async def get_chat_by_id(self, chat_id):
        return self.chat

    async def get_messages_map_by_chat_id(self, chat_id):
        return {message_id: dict(row) for message_id, row in self.rows.items()}

    async def upsert_message_to_chat_by_id_and_message_id(self, chat_id, message_id, message, *, touch=True):
        self.upserts.append((message_id, message, touch))


def load_chat_stop_route(chats, sent):
    """main.py's stop route for a chat, with Redis, Socket.IO and the database left out.

    Its chat has no running task, as after a server restart.
    """
    tree = ast.parse(MAIN_PATH.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if getattr(node, 'name', None) == 'stop_tasks_by_chat_id_endpoint']
    if len(nodes) != 1:
        raise AssertionError('main.py no longer defines stop_tasks_by_chat_id_endpoint')
    nodes[0].decorator_list = []

    async def no_tasks_found(redis, chat_id):
        return {'status': True, 'message': f'No tasks found for item {chat_id}.'}

    async def chat_event_emitter(request_info, update_db=True):
        async def emit(event):
            sent.append(event)

        return emit

    async def may_not_change_others_chat(user):
        return False

    namespace = {
        'Depends': lambda dependency: None,
        'get_verified_user': None,
        'get_temporary_chat_session_id': lambda chat_id: None,
        'Chats': chats,
        'may_change_others_chat': may_not_change_others_chat,
        'stop_item_tasks': no_tasks_found,
        'get_event_emitter': chat_event_emitter,
        'stopped_saved_workers': streaming.stopped_saved_workers,
        'WORKERS_EVENT_TYPE': streaming.WORKERS_EVENT_TYPE,
        'time': types.SimpleNamespace(time=lambda: STOPPED_AT + 0.5),
    }
    # Its annotations then stay unevaluated.
    future_annotations = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future_annotations, *nodes], type_ignores=[]))
    exec(compile(module, str(MAIN_PATH), 'exec'), namespace)
    return namespace['stop_tasks_by_chat_id_endpoint']


class StopAfterRestartTests(unittest.IsolatedAsyncioTestCase):
    """Stop finishes a reply whose task is gone, as after a server restart, and stops the workers it lists."""

    def setUp(self):
        workers = streaming.WorkerSnapshots(clock=lambda: STOPPED_AT - 60)
        workers.update(WorkerUpdate(CLAUDE_WORKER_ID, None, 1, 'Write the essay', 'running'))
        workers.update(WorkerUpdate(OTHER_CLAUDE_WORKER_ID, None, 1, 'Check the facts', 'done'))
        self.saved_workers = workers.snapshot()['event']['data']
        self.sent = []

    async def stop(self, reply_workers):
        chats = SavedChatRows(reply_workers)
        route = load_chat_stop_route(chats, self.sent)
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(redis=None)))
        await route(request, 'chat-1', OWNER_USER)
        return chats

    async def test_the_workers_still_listed_as_running_are_saved_and_sent_as_stopped(self):
        chats = await self.stop(self.saved_workers)
        [(message_id, finished, touch)] = chats.upserts
        self.assertEqual(message_id, 'message-1')
        self.assertFalse(touch)
        self.assertTrue(finished['done'])
        workers = finished['workers']
        self.assertEqual(workers['running'], 0)
        essay, facts = workers['items']
        self.assertEqual((essay['id'], essay['state'], essay['ended_at']), (CLAUDE_WORKER_ID, 'stopped', STOPPED_AT))
        self.assertEqual(facts, self.saved_workers['items'][1])
        # The open chat is told, between the reply finishing and its tasks ending.
        types_sent = [event['type'] for event in self.sent]
        self.assertEqual(types_sent, ['chat:completion', streaming.WORKERS_EVENT_TYPE, 'chat:tasks:cancel'])
        self.assertEqual(self.sent[1]['data'], workers)

    async def test_a_reply_without_workers_is_only_finished(self):
        chats = await self.stop(None)
        self.assertEqual(chats.upserts, [('message-1', {'done': True}, False)])
        self.assertEqual([event['type'] for event in self.sent], ['chat:completion', 'chat:tasks:cancel'])


if __name__ == '__main__':
    unittest.main()
