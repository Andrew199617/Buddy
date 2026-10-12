"""Plan turns run where their chat's target says: a computer and folder, or Detached.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_run_target_turns.py -v
"""

import time
import types
import unittest
from unittest.mock import AsyncMock

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
from open_webui.utils.subscriptions.conversation import parse_messages  # noqa: E402

PROJECT = 'C:\\code\\site'
REVISION_A = 'fixture-revision-runner-a'
REVISION_B = 'fixture-revision-runner-b'
CHAT_DIRECTORY = 'fixture-empty-chat-workspace'
MEMBER_USER = types.SimpleNamespace(id='member-1', role='user')


class ChatRunTargetTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [machine_config(), machine_config('runner-b', 'host-b')],
            'subscriptions.claude': enabled_settings('runner-a'),
        })
        self.service = load_service(self.config, ROOT / 'unused-run-target-turn-data')
        self.providers = {'claude': ActionProvider(), 'codex': ActionCodexProvider()}
        self.service._providers = owner_providers(self.service, self.providers)
        self.machines = {config['id']: ProcessMachine(config) for config in self.config.values['subscriptions.machines']}
        self.service._remote_machines = dict(self.machines)
        self.service.parse_messages = parse_messages
        self.service.collect_events = fixture_collect_events
        self.service.failure_events = fixture_failure_events
        self.service.stream_events = fixture_stream_events
        self.service.StreamingResponse = OfflineStreamingResponse
        self.foreign_preset = False

        async def preset(request, payload, metadata, user, models):
            return payload['model'], payload, self.foreign_preset

        self.service._apply_preset = preset
        for machine_id in ('runner-a', 'runner-b'):
            self.signed_in_on(machine_id)

        self.folders = self.service.project_folders.Folders
        self.chats = self.service.Chats
        self.folders.add('website', user_id=OWNER, name='Website')
        self.chats.add('chat-1', OWNER, 'website')
        self.turns = []

        async def record_turn(turn):
            self.turns.append(turn)
            yield {'text': 'fixture response'}

        self.providers['claude'].turn_hook = record_turn

    def signed_in_on(self, machine_id):
        on_computer = self.service.ProviderRef(OWNER, 'claude').on(machine_id)
        self.service.status_cache._entries[on_computer] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        model = types.SimpleNamespace(key='fixture-model', value='fixture-model', name='Fixture', description='', efforts=[])
        self.service.models_cache._entries[on_computer] = self.service._CacheEntry([model], time.monotonic() + 60)

    async def bind(self, request, chat_id='chat-1'):
        return await self.service.bind_chat_target(OWNER_USER, chat_id, request)

    def on_computer(self, machine_id='runner-a', revision=REVISION_A, path=PROJECT, access='full', **extra):
        folder = {'mode': 'auto'}
        if path is not None:
            folder = {'mode': 'path', 'path': path}
        request = {
            'kind': 'computer', 'machine_id': machine_id, 'expected_revision': revision, 'folder': folder,
            'access': access,
        }
        request.update(extra)
        return request

    async def chat(self, metadata=None, system=None, user=OWNER_USER):
        messages = [{'role': 'user', 'content': 'Fixture message'}]
        if system:
            messages.insert(0, {'role': 'system', 'content': system})
        form = {
            'model': 'claude-code.fixture-model',
            'messages': messages,
            'metadata': metadata if metadata is not None else {'chat_id': 'chat-1'},
        }
        return await self.service.generate_subscription_chat_completion(
            types.SimpleNamespace(state=types.SimpleNamespace()),
            form,
            user,
            {'claude-code.fixture-model': {'subscription': {'provider': 'claude', 'model': 'fixture-model'}}},
        )

    async def test_a_chat_runs_in_its_folder_with_its_access(self):
        await self.bind(self.on_computer(access='read'))
        await self.chat()
        turn = self.turns[0]
        self.assertEqual((turn.machine.id, turn.cwd, turn.settings.access), ('runner-a', PROJECT, 'read'))
        # An exact folder needs no note about choosing one.
        self.assertEqual(turn.conversation.system, '')

    async def test_a_chat_on_another_computer_runs_there_whatever_the_plans_computer(self):
        await self.bind(self.on_computer('runner-b', REVISION_B))
        await self.chat()
        self.assertEqual((self.turns[0].machine.id, self.turns[0].cwd), ('runner-b', PROJECT))
        self.assertEqual(self.config.values['subscriptions.claude']['machine_id'], 'runner-a')

    async def test_a_detached_chat_runs_chat_only(self):
        await self.bind({'kind': 'detached'})
        await self.chat()
        self.assertEqual((self.turns[0].settings.access, self.turns[0].cwd), ('chat', CHAT_DIRECTORY))
        self.assertEqual(self.machines['runner-a'].path_checks, [])

    async def test_tasks_and_channel_replies_run_chat_only(self):
        await self.bind(self.on_computer())
        await self.chat(metadata={'chat_id': 'chat-1', 'task': 'title_generation'})
        await self.chat(metadata={'chat_id': 'channel:fixture'})
        self.assertEqual([turn.settings.access for turn in self.turns], ['chat', 'chat'])
        self.assertEqual([turn.cwd for turn in self.turns], [CHAT_DIRECTORY] * 2)

    async def test_a_task_in_an_unbound_chat_does_not_fix_where_it_runs(self):
        await self.chat(metadata={'chat_id': 'chat-1', 'task': 'title_generation'})
        self.assertIsNone(await self.service.ChatRunTargets.get('chat-1'))

    async def test_a_chat_from_before_targets_keeps_running_where_its_plan_runs(self):
        self.chats.add('chat-old', OWNER)
        await self.chat(metadata={'chat_id': 'chat-old'})
        turn = self.turns[0]
        self.assertEqual((turn.machine.id, turn.settings.access, turn.cwd), ('runner-a', 'full', 'C:\\fixture-project'))
        # It keeps the prompt it started with.
        self.assertEqual(turn.conversation.system, '')
        row = await self.service.ChatRunTargets.get('chat-old')
        self.assertEqual((row.machine_id, row.folder_mode, row.note), ('runner-a', 'auto', 'plan'))

    async def test_a_chat_created_without_the_bar_after_targets_started_runs_detached(self):
        # Such as an automation's chat: it gets the default like any new chat.
        self.config.values['subscriptions.run_targets_started_at'] = 1000
        self.chats.add('chat-automation', OWNER, created_at=2000)
        await self.chat(metadata={'chat_id': 'chat-automation'})
        self.assertEqual((self.turns[0].settings.access, self.turns[0].cwd), ('chat', CHAT_DIRECTORY))
        row = await self.service.ChatRunTargets.get('chat-automation')
        self.assertEqual((row.kind, row.source), ('detached', 'default'))

    async def test_a_chat_from_before_targets_in_a_folder_uses_the_folders_choice(self):
        saved = {'runner-a': self.service.project_folders.SavedPath(PROJECT, REVISION_A)}
        await self.service.project_folders.save_choice(OWNER, 'website', 'folder', saved, {'runner-a', 'runner-b'})
        await self.chat()
        self.assertEqual(self.turns[0].cwd, PROJECT)

    async def test_a_computer_set_up_again_is_refused(self):
        await self.bind(self.on_computer())
        self.config.values['subscriptions.machines'][0]['revision'] = 'fixture-revision-new'
        events = await self.chat()
        self.assertIn('set up again after this chat started', str(events))
        self.assertEqual(self.turns, [])

    async def test_a_removed_computer_is_refused(self):
        await self.bind(self.on_computer('runner-b', REVISION_B))
        self.config.values['subscriptions.machines'].pop(1)
        events = await self.chat()
        self.assertIn('was removed', str(events))
        self.assertEqual(self.turns, [])

    async def test_a_folder_that_is_gone_is_refused_without_starting(self):
        await self.bind(self.on_computer())
        self.machines['runner-a'].path_exists = AsyncMock(return_value=False)
        events = await self.chat()
        self.assertIn('no longer exists', str(events))
        self.assertNotIn(PROJECT, str(events))
        self.assertEqual(self.turns, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_full_access_is_told_it_chooses_where_to_work_when_the_ai_chooses(self):
        await self.bind(self.on_computer(path=None))
        await self.chat(system='Folder instructions.')
        system = self.turns[0].conversation.system
        self.assertTrue(system.startswith('No project folder is set for this chat'))
        self.assertIn('C:\\fixture-project', system)
        self.assertTrue(system.endswith('Folder instructions.'))
        self.assertEqual(self.turns[0].cwd, 'C:\\fixture-project')

    async def test_read_access_and_turns_outside_chats_get_no_note(self):
        await self.bind(self.on_computer(path=None, access='read'))
        await self.chat()
        await self.chat(metadata={})
        self.assertEqual([turn.conversation.system for turn in self.turns], ['', ''])

    async def test_someone_elses_chat_runs_chat_only_on_the_posters_own_computers(self):
        await self.bind(self.on_computer())
        await self.chat(user=OWNER_USER.__class__(id='admin-2', role='admin'))
        self.assertEqual(self.turns, [])
        # The poster has no computer of their own, so nothing runs, and the owner's target is never used.
        self.assertEqual(self.machines['runner-a'].path_checks, [])

    async def test_a_preset_someone_else_wrote_runs_chat_only(self):
        await self.bind(self.on_computer())
        self.foreign_preset = True
        await self.chat()
        self.assertEqual((self.turns[0].settings.access, self.turns[0].cwd), ('chat', CHAT_DIRECTORY))

    async def test_received_shared_instructions_keep_a_turn_chat_only_unless_acknowledged(self):
        self.folders.add('team', user_id=OWNER, name='Team', data={'system_prompt': 'Fixture instructions'})
        self.service.project_folders.AccessGrants.share('team', 'member-1', 'write')
        self.chats.add('chat-2', OWNER, 'team')
        await self.bind(self.on_computer(acknowledged_instructions_folder_id='team'), 'chat-2')
        await self.bind(self.on_computer(access='chat'), 'chat-1')
        received = {'chat_id': 'chat-2', 'folder_instructions_id': 'team'}
        await self.chat(metadata=received)
        self.assertEqual(self.turns[0].settings.access, 'full')
        # Another chat that received the same instructions without acknowledging them stays chat only.
        await self.service.ChatRunTargets.insert_if_absent(
            (await self.service.ChatRunTargets.get('chat-2')).model_copy(
                update={'chat_id': 'chat-3', 'acknowledged_instructions_folder_id': None}
            )
        )
        self.chats.add('chat-3', OWNER, 'team')
        await self.chat(metadata={'chat_id': 'chat-3', 'folder_instructions_id': 'team'})
        self.assertEqual(self.turns[1].settings.access, 'chat')

    async def test_a_temporary_chat_uses_the_choice_each_turn_carries(self):
        metadata = {'chat_id': 'temporary:session-1', 'requested_run_target': self.on_computer('runner-b', REVISION_B)}
        await self.chat(metadata=metadata)
        await self.chat(metadata={'chat_id': 'temporary:session-1'})
        self.assertEqual([turn.machine.id for turn in self.turns], ['runner-b', 'runner-a'])
        self.assertEqual([turn.settings.access for turn in self.turns], ['full', 'chat'])
        self.assertEqual(self.service.ChatRunTargets.rows, {})


if __name__ == '__main__':
    unittest.main()
