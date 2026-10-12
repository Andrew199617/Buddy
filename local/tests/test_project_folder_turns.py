"""Plan turns start in the chat folder's project folder.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_project_folder_turns.py -v
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


class ProjectFolderTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [machine_config(), machine_config('runner-b', 'host-b')],
            'subscriptions.claude': enabled_settings('runner-a'),
        })
        self.service = load_service(self.config, ROOT / 'unused-project-turn-data')
        self.providers = {'claude': ActionProvider(), 'codex': ActionCodexProvider()}
        self.service._providers = owner_providers(self.service, self.providers)
        self.machines = {config['id']: ProcessMachine(config) for config in self.config.values['subscriptions.machines']}
        self.service._remote_machines = dict(self.machines)
        self.service.parse_messages = parse_messages
        self.service.collect_events = fixture_collect_events
        self.service.failure_events = fixture_failure_events
        self.service.stream_events = fixture_stream_events
        self.service.StreamingResponse = OfflineStreamingResponse

        async def own_preset(request, payload, metadata, user, models):
            return payload['model'], payload, False

        self.service._apply_preset = own_preset
        on_runner_a = self.service.ProviderRef(OWNER, 'claude').on('runner-a')
        self.service.status_cache._entries[on_runner_a] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        model = types.SimpleNamespace(key='fixture-model', value='fixture-model', name='Fixture', description='', efforts=[])
        self.service.models_cache._entries[on_runner_a] = self.service._CacheEntry([model], time.monotonic() + 60)

        folders = self.service.project_folders
        folders.Folders.add('website', user_id=OWNER, name='Website')
        folders.Chats.folders[('chat-1', OWNER)] = 'website'
        self.turns = []

        async def record_turn(turn):
            self.turns.append(turn)
            yield {'text': 'fixture response'}

        self.providers['claude'].turn_hook = record_turn

    async def choose(self, mode, paths=None):
        saved = {
            machine_id: self.service.project_folders.SavedPath(path, f'fixture-revision-{machine_id}')
            for machine_id, path in (paths or {}).items()
        }
        await self.service.project_folders.save_choice(OWNER, 'website', mode, saved, {'runner-a', 'runner-b'})

    def set_access(self, access):
        self.config.values['subscriptions.claude']['access'] = access

    async def chat(self, metadata=None, system=None):
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
            OWNER_USER,
            {'claude-code.fixture-model': {'subscription': {'provider': 'claude', 'model': 'fixture-model'}}},
        )

    async def test_read_and_full_turns_start_in_the_project_folder(self):
        await self.choose('folder', {'runner-a': PROJECT})
        for access in ('read', 'full'):
            with self.subTest(access=access):
                self.set_access(access)
                await self.chat()
                self.assertEqual(self.turns[-1].cwd, PROJECT)
                # An exact folder needs no note about choosing one.
                self.assertNotIn('choose where to work', self.turns[-1].conversation.system)

    async def test_chat_only_turns_never_look_up_the_folder(self):
        self.set_access('chat')
        lookup = AsyncMock()
        self.service.project_folders.choice_for_turn = lookup
        await self.chat()
        await self.chat(metadata={'chat_id': 'chat-1', 'task': 'title_generation'})
        lookup.assert_not_called()
        self.assertEqual({turn.cwd for turn in self.turns}, {'fixture-empty-chat-workspace'})

    async def test_tasks_and_channel_replies_in_a_folder_stay_chat_only(self):
        await self.choose('folder', {'runner-a': PROJECT})
        await self.chat(metadata={'chat_id': 'chat-1', 'task': 'title_generation'})
        await self.chat(metadata={'chat_id': 'channel:fixture'})
        self.assertEqual([turn.settings.access for turn in self.turns], ['chat', 'chat'])
        self.assertEqual([turn.cwd for turn in self.turns], ['fixture-empty-chat-workspace'] * 2)

    async def test_no_files_just_chat_lowers_a_full_access_plan(self):
        await self.choose('chat')
        await self.chat()
        self.assertEqual((self.turns[0].settings.access, self.turns[0].cwd), ('chat', 'fixture-empty-chat-workspace'))
        self.assertEqual(self.machines['runner-a'].path_checks, [])

    async def test_a_project_folder_on_another_computer_is_refused(self):
        await self.choose('folder', {'runner-b': PROJECT})
        events = await self.chat()
        self.assertIn('another computer', str(events))
        self.assertIn('Website', str(events))
        self.assertNotIn(PROJECT, str(events))
        self.assertEqual(self.turns, [])

    async def test_a_computer_set_up_again_is_refused(self):
        await self.choose('folder', {'runner-a': PROJECT})
        self.config.values['subscriptions.machines'][0]['revision'] = 'fixture-revision-new'
        # Settings stay pointed at the same computer, now with a new revision.
        self.service._remote_machines = dict(self.machines)
        events = await self.chat()
        self.assertIn('set up again', str(events))
        self.assertEqual(self.turns, [])

    async def test_a_project_folder_that_is_gone_is_refused_without_starting(self):
        await self.choose('folder', {'runner-a': PROJECT})
        self.machines['runner-a'].path_exists = AsyncMock(return_value=False)
        events = await self.chat()
        self.assertIn('no longer exists', str(events))
        self.assertNotIn(PROJECT, str(events))
        self.assertEqual(self.turns, [])
        self.assertEqual(self.machines['runner-a'].processes, [])

    async def test_full_access_is_told_it_chooses_where_to_work_in_a_folder_that_lets_it(self):
        await self.choose('auto')
        await self.chat(system='Folder instructions.')
        system = self.turns[0].conversation.system
        self.assertTrue(system.startswith('No project folder is set for this chat'))
        self.assertIn('C:\\fixture-project', system)
        self.assertTrue(system.endswith('Folder instructions.'))
        self.assertEqual(self.turns[0].cwd, 'C:\\fixture-project')

    async def test_read_access_and_chats_outside_folders_get_no_note(self):
        await self.choose('auto')
        self.set_access('read')
        await self.chat()
        self.set_access('full')
        await self.chat(metadata={'chat_id': 'chat-outside'})
        self.assertEqual([turn.conversation.system for turn in self.turns], ['', ''])


if __name__ == '__main__':
    unittest.main()
