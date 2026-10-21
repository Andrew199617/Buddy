"""Ultra turns resolve the person's worker choice on the computer that runs them.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_turns.py -v
"""

import os
import time
import types
import unittest
from unittest.mock import patch

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
from open_webui.utils.subscriptions.events import StatusUpdate  # noqa: E402

PROJECT = 'C:\\code\\site'
REVISION_A = 'fixture-revision-runner-a'
CHAT_MODEL_ID = 'claude-code.fixture-model'
WORKER_MODEL_ID = 'claude-code.fixture-worker'
CODEX_MODEL_ID = 'codex.fixture-codex'
SAME_AS_CHAT = {'worker_model': None, 'worker_effort': None}
OFF_FOR_CHAT_MODEL = "Ultra is off for this reply: Claude on your computer can't run workers with Fixture."
OFF_FOR_WORKER_MODEL = "Ultra is off for this reply: Claude on your computer can't run workers with Fixture Worker."
OTHER_PROVIDER_MESSAGE = "Ultra's workers run on this chat's Claude plan. Choose one of its models for them."
NOT_OFFERED_MESSAGE = "Claude on your computer does not offer the workers' model. Choose another worker model."


def plan_model(key, name, efforts, ultra=True):
    return ProviderModel(key=key, value=f'{key}-cli', name=name, efforts=list(efforts), ultra=ultra)


class UltraTurnTests(unittest.IsolatedAsyncioTestCase):
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

    def signed_in(self, provider_id, models):
        on_computer = self.service.ProviderRef(OWNER, provider_id).on('runner-a')
        self.service.status_cache._entries[on_computer] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        self.service.models_cache._entries[on_computer] = self.service._CacheEntry(models, time.monotonic() + 60)

    def forget_model_list(self, provider_id):
        """As right after a restart, before the computer's model list loads again."""
        on_computer = self.service.ProviderRef(OWNER, provider_id).on('runner-a')
        del self.service.models_cache._entries[on_computer]

    async def bind(self, access='full', path=PROJECT):
        folder = {'mode': 'auto'}
        if path is not None:
            folder = {'mode': 'path', 'path': path}
        request = {
            'kind': 'computer', 'machine_id': 'runner-a', 'expected_revision': REVISION_A, 'folder': folder,
            'access': access,
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
        for worker_model in (CODEX_MODEL_ID, 'fixture-preset', 'claude-code.not-listed'):
            events = await self.chat({'worker_model': worker_model, 'worker_effort': None})
            self.assertEqual(events, [{'error': OTHER_PROVIDER_MESSAGE}])
        self.assertEqual(self.turns, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_a_worker_model_the_computer_does_not_offer_is_refused(self):
        await self.bind()
        self.signed_in('claude', self.claude_models[:1])
        events = await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': None})
        self.assertEqual(events, [{'error': NOT_OFFERED_MESSAGE}])
        self.assertEqual(self.turns, [])

    async def test_before_the_model_list_loads_the_registry_decides(self):
        await self.bind()
        self.forget_model_list('claude')
        await self.chat({'worker_model': WORKER_MODEL_ID, 'worker_effort': 'max'})
        options = self.turns[0].ultra_options
        self.assertEqual((options.worker_model, options.worker_effort), ('fixture-worker-cli', 'max'))
        # The registry says the chat's model can't run Ultra, so the reply runs without it.
        self.registry[CHAT_MODEL_ID] = self.registry_entry('claude', 'fixture-model', 'Fixture', ['low'], ultra=False)
        events = await self.chat()
        self.assertIsNone(self.turns[1].ultra_options)
        self.assertEqual(events[0], StatusUpdate(OFF_FOR_CHAT_MODEL))

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


if __name__ == '__main__':
    unittest.main()
