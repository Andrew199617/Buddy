"""Ultra turns on the server.

The person's worker choice resolves on the computer that runs the turn, and
while the turn runs, the person can stop its workers one by one.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_turns.py -v
"""

import asyncio
import os
import time
import types
import unittest
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

# After the test helpers put backend/ on the path.
from open_webui.utils.subscriptions import ultra  # noqa: E402
from open_webui.utils.subscriptions.common import ProviderModel  # noqa: E402
from open_webui.utils.subscriptions.conversation import parse_messages  # noqa: E402
from open_webui.utils.subscriptions.events import StatusUpdate, SubscriptionError  # noqa: E402

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


class LiveUltraTurnTests(UltraServiceTestCase):
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


if __name__ == '__main__':
    unittest.main()
