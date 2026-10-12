"""Saving where a new chat runs: the bar's choice, checked, or the default.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_run_target_binding.py -v

Production service code runs with in-memory configuration, chats and targets.
No computer is contacted.
"""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_subscription_machines import OWNER, OWNER_USER, ROOT, MemoryConfig, load_service, machine_config  # noqa: E402

MEMBER = 'member-1'
MEMBER_USER = types.SimpleNamespace(id=MEMBER, role='user')
REVISION_B = 'fixture-revision-runner-b'


def member_machine():
    config = machine_config('member-pc', 'host-member')
    config['user_id'] = MEMBER
    return config


class RunTargetTestCase(unittest.IsolatedAsyncioTestCase):
    """Two of the owner's computers, someone else's, and a chat the owner started."""

    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [machine_config(), machine_config('runner-b', 'host-b'), member_machine()],
            'subscriptions.claude': {'enable': True, 'access': 'full', 'machine_id': 'runner-a'},
        })
        self.service = load_service(self.config, ROOT / 'unused-run-target-data')
        self.chats = self.service.Chats
        self.targets = self.service.ChatRunTargets
        self.folders = self.service.project_folders.Folders
        self.chats.add('chat-1', OWNER)

    async def bind(self, raw=None, chat_id='chat-1', user=OWNER_USER):
        return await self.service.bind_chat_target(user, chat_id, raw)

    def computer(self, machine_id='runner-b', **overrides):
        request = {
            'kind': 'computer', 'machine_id': machine_id, 'expected_revision': REVISION_B,
            'folder': {'mode': 'path', 'path': 'C:\\code\\site\\'}, 'access': 'full',
        }
        request.update(overrides)
        return request


class BindingTests(RunTargetTestCase):
    async def test_a_new_chat_outside_chat_folders_runs_detached(self):
        row = await self.bind()
        self.assertEqual((row.kind, row.machine_id, row.access, row.source), ('detached', None, 'chat', 'default'))

    async def test_a_chat_keeps_the_first_target_saved_for_it(self):
        first = await self.bind(self.computer())
        second = await self.bind({'kind': 'detached'})
        self.assertIs(second, first)
        self.assertEqual(second.kind, 'computer')

    async def test_the_bars_choice_is_saved_checked_and_tidied(self):
        row = await self.bind(self.computer())
        self.assertEqual((row.machine_id, row.machine_revision, row.source), ('runner-b', REVISION_B, 'bar'))
        self.assertEqual((row.folder_mode, row.path, row.access), ('path', 'C:\\code\\site', 'full'))

    async def test_a_choice_that_cant_be_used_runs_detached_and_says_why(self):
        self.config.values['subscriptions.policy'] = {'server_machine': False}
        refusals = (
            (self.computer('missing-pc'), 'computer_removed'),
            # Someone else's computer gets the same answer as an unknown one.
            (self.computer('member-pc'), 'computer_removed'),
            (self.computer(expected_revision='old-revision'), 'computer_reset'),
            (self.computer('local', expected_revision=None), 'server_off'),
            (self.computer(folder={'mode': 'path', 'path': '\\\\server\\share'}), 'bad_path'),
            ({'kind': 'computer', 'machine_id': 'runner-b', 'owner': 'someone'}, 'bad_request'),
        )
        for index, (request, note) in enumerate(refusals):
            with self.subTest(note=note):
                chat_id = f'chat-refused-{index}'
                self.chats.add(chat_id, OWNER)
                row = await self.bind(request, chat_id)
                self.assertEqual((row.kind, row.access, row.source, row.note), ('detached', 'chat', 'refused', note))

    async def test_only_the_owner_of_a_saved_chat_binds_it(self):
        self.assertIsNone(await self.bind(self.computer(), user=MEMBER_USER))
        self.assertIsNone(await self.bind(self.computer(), chat_id='temporary:session-1'))
        self.assertEqual(self.targets.rows, {})

    async def test_a_chat_folders_defaults_apply_to_new_chats_in_it(self):
        self.folders.add('website', OWNER)
        self.chats.add('chat-2', OWNER, 'website')
        saved = {'runner-b': self.service.project_folders.SavedPath('C:\\code\\site', REVISION_B)}
        await self.service.project_folders.save_choice(
            OWNER, 'website', 'folder', saved, {'runner-a', 'runner-b'}, machine_id='runner-b', access='read'
        )
        row = await self.bind(chat_id='chat-2')
        self.assertEqual((row.kind, row.machine_id, row.path, row.access), ('computer', 'runner-b', 'C:\\code\\site', 'read'))
        self.assertEqual(row.source, 'default')

    async def test_a_folder_that_lets_the_ai_choose_uses_the_plan_access_by_default(self):
        self.folders.add('notes', OWNER)
        self.chats.add('chat-3', OWNER, 'notes')
        await self.service.project_folders.save_choice(OWNER, 'notes', 'auto', {}, {'runner-a', 'runner-b'})
        row = await self.bind(chat_id='chat-3')
        self.assertEqual((row.machine_id, row.folder_mode, row.access), ('runner-a', 'auto', 'full'))

    async def test_shared_instructions_need_acknowledging_before_files_are_used(self):
        self.folders.add('team', OWNER, data={'system_prompt': 'Fixture instructions'})
        self.service.project_folders.AccessGrants.share('team', MEMBER, 'write')
        self.chats.add('chat-4', OWNER, 'team')
        self.chats.add('chat-5', OWNER, 'team')
        refused = await self.bind(self.computer(), chat_id='chat-4')
        self.assertEqual(refused.note, 'needs_acknowledgement')
        acknowledged = await self.bind(self.computer(acknowledged_instructions_folder_id='team'), chat_id='chat-5')
        self.assertEqual((acknowledged.kind, acknowledged.access), ('computer', 'full'))
        self.assertEqual(acknowledged.acknowledged_instructions_folder_id, 'team')


class OptionsTests(RunTargetTestCase):
    """What the composer's bar offers for a new chat."""

    async def test_options_list_the_persons_computers_with_each_plans_last_state(self):
        on_b = self.service.ProviderRef(OWNER, 'claude').on('runner-b')
        self.service.status_cache._entries[on_b] = self.service._CacheEntry(
            {'installed': True, 'signed_in': False, 'message': 'Fixture signed out'}, 0
        )
        options = await self.service.run_target_options(OWNER_USER)
        self.assertEqual((options['allowed'], options['has_plans'], options['plan_access']), (True, True, {'claude': 'full'}))
        computers = {computer['machine_id']: computer for computer in options['computers']}
        # Someone else's computer is never listed.
        self.assertEqual(list(computers), ['local', 'runner-a', 'runner-b'])
        self.assertEqual(computers['runner-b']['revision'], REVISION_B)
        self.assertEqual(computers['runner-b']['providers']['claude'], {'state': 'sign_in', 'message': 'Fixture signed out'})
        self.assertEqual(computers['runner-a']['providers']['claude']['state'], 'unknown')
        # Outside chat folders a new chat runs Detached.
        self.assertEqual(options['default']['kind'], 'detached')
        self.assertIsNone(options['chat_folder'])

    async def test_recent_folders_are_on_computers_not_set_up_again(self):
        await self.bind(self.computer())
        self.chats.add('chat-2', OWNER)
        await self.bind(self.computer(folder={'mode': 'path', 'path': 'C:\\code\\api'}), 'chat-2')
        self.config.values['subscriptions.machines'][1]['revision'] = 'fixture-revision-again'
        options = await self.service.run_target_options(OWNER_USER)
        self.assertEqual(options['recent'], [])
        self.config.values['subscriptions.machines'][1]['revision'] = REVISION_B
        options = await self.service.run_target_options(OWNER_USER)
        self.assertCountEqual([entry['path'] for entry in options['recent']], ['C:\\code\\api', 'C:\\code\\site'])

    async def test_options_in_a_chat_folder_carry_its_defaults(self):
        self.folders.add('website', OWNER)
        saved = {'runner-b': self.service.project_folders.SavedPath('C:\\code\\site', REVISION_B)}
        offered = {'runner-b': [self.service.project_folders.SavedPath('C:\\code\\docs', REVISION_B)]}
        await self.service.project_folders.save_choice(
            OWNER, 'website', 'folder', saved, {'runner-a', 'runner-b'}, machine_id='runner-b', offered=offered
        )
        options = await self.service.run_target_options(OWNER_USER, 'website', this_machine_id='member-pc')
        folder = options['chat_folder']
        self.assertEqual((folder['id'], folder['mode'], folder['shared_instructions']), ('website', 'folder', False))
        self.assertEqual(folder['paths'], {'runner-b': 'C:\\code\\site'})
        self.assertEqual(folder['offered'], {'runner-b': ['C:\\code\\docs']})
        self.assertEqual(options['default']['machine_id'], 'runner-b')
        self.assertEqual(options['default']['folder'], {'mode': 'path', 'path': 'C:\\code\\site'})

    async def test_a_person_who_may_not_use_computers_gets_nothing(self):
        self.config.values['subscriptions.policy'] = {'user_machines': 'admins'}
        self.assertEqual(await self.service.run_target_options(MEMBER_USER), {'allowed': False, 'has_plans': False})


if __name__ == '__main__':
    unittest.main()
