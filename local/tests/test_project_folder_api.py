"""The project folder API for chat folders: checking a folder on a computer, and saving each person's choice.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_project_folder_api.py -v
"""

import types
from unittest.mock import patch
import unittest

from test_subscription_machines import (
    OWNER,
    OWNER_USER,
    ROOT,
    FakeRemoteMachine,
    MemoryConfig,
    SubscriptionError,
    enabled_settings,
    load_service,
    machine_config,
)

MEMBER = 'member-1'
MEMBER_USER = types.SimpleNamespace(id=MEMBER, role='user')
OUTSIDER_USER = types.SimpleNamespace(id='outsider', role='user')


class FolderMachine(FakeRemoteMachine):
    """A computer that has some folders, can go offline, and answers folder listings."""

    def __init__(self, config, existing):
        super().__init__(config['id'], config['name'], config['url'], config['key'])
        self.existing = set(existing)
        self.offline = False
        self.browsing_reply = None
        self.listed = []
        self.suggest_calls = 0

    async def list_folders(self, path, show_hidden, prefix):
        self.listed.append((path, show_hidden, prefix))
        if isinstance(self.browsing_reply, Exception):
            raise self.browsing_reply
        return self.browsing_reply

    async def suggest_folders(self):
        self.suggest_calls += 1
        if isinstance(self.browsing_reply, Exception):
            raise self.browsing_reply
        return self.browsing_reply

    def matches(self, *args):
        return True

    async def path_exists(self, path, kind):
        self.path_checks.append((path, kind))
        if self.offline:
            raise SubscriptionError(f'{self.name} is offline.')
        return path in self.existing


class ProjectFolderApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pc = machine_config('pc-1')
        member_pc = {**machine_config('member-pc'), 'user_id': MEMBER}
        self.config = MemoryConfig({
            'subscriptions.machines': [pc, member_pc],
            'subscriptions.claude': enabled_settings('pc-1'),
        })
        self.service = load_service(self.config, ROOT / 'unused-project-folder-data')
        self.service.Users.users[MEMBER] = MEMBER_USER
        self.machine = FolderMachine(pc, {'C:\\code\\site', 'C:\\code\\docs'})
        self.member_machine = FolderMachine(member_pc, {'D:\\member\\site'})
        self.service._remote_machines['pc-1'] = self.machine
        self.service._remote_machines['member-pc'] = self.member_machine
        folders = self.service.project_folders
        folders.Folders.add('website', user_id=OWNER, name='Website')

        # The owner's folder is shared with the member for reading.
        async def has_folder_access(user_id, folder, permission, db):
            return user_id in (folder.user_id, MEMBER)

        folders.has_folder_access = has_folder_access

    async def save(self, user, mode, paths=None, folder_id='website'):
        return await self.service.save_folder_project(user, folder_id, mode, paths or {})

    def computer(self, project, machine_id='pc-1'):
        return next(computer for computer in project['computers'] if computer['machine_id'] == machine_id)

    # --- Checking a folder on a computer ---------------------------------------

    async def test_a_typed_folder_is_checked_and_tidied(self):
        cleaned = await self.service.check_machine_folder(OWNER_USER, 'pc-1', 'C:/code/site/')
        self.assertEqual(cleaned, 'C:\\code\\site')
        self.assertEqual(self.machine.path_checks, [('C:\\code\\site', 'dir')])

    async def test_a_missing_or_relative_folder_is_refused(self):
        with self.assertRaisesRegex(SubscriptionError, 'has no folder at that path'):
            await self.service.check_machine_folder(OWNER_USER, 'pc-1', 'C:\\nowhere')
        with self.assertRaisesRegex(SubscriptionError, 'full path'):
            await self.service.check_machine_folder(OWNER_USER, 'pc-1', 'code\\site')

    async def test_only_your_own_computers_can_be_checked(self):
        with self.assertRaisesRegex(SubscriptionError, 'no longer exists'):
            await self.service.check_machine_folder(OWNER_USER, 'member-pc', 'D:\\member\\site')
        self.assertEqual(self.member_machine.path_checks, [])

    async def test_this_server_is_only_for_its_owner(self):
        with self.assertRaises(SubscriptionError):
            await self.service.check_machine_folder(MEMBER_USER, 'local', '/srv/project')

    # --- Describing and saving a chat folder's choice ---------------------------

    async def test_a_folder_you_cannot_read_is_not_found(self):
        with self.assertRaises(self.service.HTTPException) as refused:
            await self.service.describe_folder_project(OUTSIDER_USER, 'website')
        self.assertEqual(refused.exception.status_code, 404)

    async def test_describing_lists_the_computers_and_asks_none_of_them(self):
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual((project['own'], project['inherited'], project['has_plans']), (None, None, True))
        # Every computer plans can run on: this server for its owner, and their own computers.
        self.assertEqual([computer['machine_id'] for computer in project['computers']], ['local', 'pc-1'])
        computer = self.computer(project)
        self.assertEqual((computer['kind'], computer['platform'], computer['can_browse']), ('address', 'win32', False))
        self.assertEqual(computer['providers'], [{'id': 'claude', 'name': 'Claude', 'access': 'full'}])
        self.assertEqual(self.machine.path_checks, [])

    async def test_describing_lists_each_plan_with_its_computer(self):
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual(project['plans'], [
            {
                'id': 'claude', 'name': 'Claude', 'enable': True, 'access': 'full',
                'machine': {'id': 'pc-1', 'name': 'Test pc-1', 'revision': 'fixture-revision-pc-1'},
            },
            {
                'id': 'codex', 'name': 'ChatGPT', 'enable': False, 'access': 'chat',
                'machine': {'id': 'local', 'name': 'This server', 'revision': 'local'},
            },
        ])
        self.assertEqual(self.machine.path_checks, [])

    async def test_a_chat_only_plan_is_listed_with_its_computer(self):
        # Its access is where new chats start, so the folder settings can offer to raise it.
        self.config.values['subscriptions.claude'] = {**enabled_settings('pc-1'), 'access': 'chat'}
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        claude = project['plans'][0]
        self.assertEqual((claude['enable'], claude['access'], claude['machine']['id']), (True, 'chat', 'pc-1'))
        self.assertTrue(project['has_plans'])

    async def test_a_plan_without_a_usable_computer_has_none(self):
        self.config.values['subscriptions.claude'] = enabled_settings('removed-pc')
        await self.service.save_policy({'server_machine': False})
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual([plan['machine'] for plan in project['plans']], [None, None])

    async def test_a_computer_added_by_address_without_its_key_is_not_usable(self):
        # As after a config import onto a server where that computer's address changed.
        del self.config.values['subscriptions.machines'][0]['key']
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertIsNone(project['plans'][0]['machine'])

    async def test_a_described_plan_saves_with_the_computer_it_reports(self):
        self.config.values['subscriptions.claude'] = {
            'enable': True, 'access': 'chat', 'workspace': '', 'cli_path': '', 'machine_id': 'pc-1'
        }
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        for plan in project['plans']:
            with self.subTest(plan=plan['id']):
                machine = plan['machine']
                saved = await self.service.save_settings(
                    OWNER, plan['id'], {'access': 'read'}, machine['id'], machine['revision']
                )
                self.assertEqual((saved.access, saved.machine_id), ('read', machine['id']))

    async def test_a_members_plans_name_only_their_own_computers(self):
        self.config.values['subscriptions.users.member-1.claude'] = enabled_settings('pc-1')
        self.config.values['subscriptions.users.member-1.codex'] = {
            **enabled_settings('member-pc'), 'access': 'chat'
        }
        project = await self.service.describe_folder_project(MEMBER_USER, 'website')
        [claude, codex] = project['plans']
        self.assertIsNone(claude['machine'])
        self.assertEqual(codex['machine'], {
            'id': 'member-pc', 'name': 'Test member-pc', 'revision': 'fixture-revision-member-pc'
        })

    async def test_chat_only_plans_still_list_every_computer(self):
        # A plan's access is only where new chats start, so a folder can still be chosen.
        self.config.values['subscriptions.claude']['access'] = 'chat'
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual(self.computer(project)['providers'], [{'id': 'claude', 'name': 'Claude', 'access': 'chat'}])

    async def test_a_saved_folder_round_trips_with_recent_paths(self):
        project = await self.save(OWNER_USER, 'folder', {'pc-1': 'C:/code/site'})
        self.assertEqual(project['own']['paths'], {'pc-1': {'path': 'C:\\code\\site', 'problem': None}})
        self.assertEqual(self.computer(project)['recent_paths'], ['C:\\code\\site'])

    async def test_new_chat_defaults_round_trip_and_offered_folders_are_checked(self):
        project = await self.service.save_folder_project(
            OWNER_USER, 'website', 'folder', {'pc-1': 'C:\\code\\site'}, machine_id='pc-1', access='read',
            offered={'pc-1': ['C:/code/docs/']}
        )
        own = project['own']
        self.assertEqual((own['machine_id'], own['access']), ('pc-1', 'read'))
        self.assertEqual(own['offered'], {'pc-1': [{'path': 'C:\\code\\docs', 'problem': None}]})
        self.assertIn(('C:\\code\\docs', 'dir'), self.machine.path_checks)
        # Saving again with the same folders checks nothing on the computer.
        self.machine.path_checks.clear()
        await self.service.save_folder_project(
            OWNER_USER, 'website', 'folder', {'pc-1': 'C:\\code\\site'}, offered={'pc-1': ['C:\\code\\docs']}
        )
        self.assertEqual(self.machine.path_checks, [])

    async def test_offered_folders_are_limited_and_must_exist(self):
        too_many = {'pc-1': [f'C:\\code\\{index}' for index in range(6)]}
        with self.assertRaisesRegex(SubscriptionError, 'up to 5'):
            await self.service.save_folder_project(OWNER_USER, 'website', 'auto', {}, offered=too_many)
        with self.assertRaisesRegex(SubscriptionError, 'has no folder at that path'):
            await self.service.save_folder_project(OWNER_USER, 'website', 'auto', {}, offered={'pc-1': ['C:\\nowhere']})
        self.assertEqual(self.machine.path_checks, [('C:\\nowhere', 'dir')])

    async def test_folder_mode_needs_a_path(self):
        with self.assertRaisesRegex(SubscriptionError, 'at least one computer'):
            await self.save(OWNER_USER, 'folder')

    async def test_let_the_ai_choose_and_chat_only_keep_no_paths(self):
        for mode in ('auto', 'chat'):
            with self.subTest(mode=mode):
                project = await self.save(OWNER_USER, mode, {'pc-1': 'C:\\code\\site'})
                self.assertEqual((project['own']['mode'], project['own']['paths']), (mode, {}))
        self.assertEqual(self.machine.path_checks, [])

    async def test_a_changed_path_needs_the_computer_online(self):
        self.machine.offline = True
        with self.assertRaisesRegex(SubscriptionError, 'offline'):
            await self.save(OWNER_USER, 'folder', {'pc-1': 'C:\\code\\site'})

    async def test_an_unchanged_path_saves_while_the_computer_is_offline(self):
        await self.save(OWNER_USER, 'folder', {'pc-1': 'C:\\code\\site'})
        self.machine.offline = True
        checks = len(self.machine.path_checks)
        project = await self.save(OWNER_USER, 'folder', {'pc-1': 'C:\\code\\site'})
        self.assertEqual(project['own']['paths']['pc-1']['path'], 'C:\\code\\site')
        self.assertEqual(len(self.machine.path_checks), checks)

    async def test_a_computer_set_up_again_needs_its_folder_chosen_again(self):
        await self.save(OWNER_USER, 'folder', {'pc-1': 'C:\\code\\site'})
        self.config.values['subscriptions.machines'][0]['revision'] = 'fixture-revision-new'
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual(project['own']['paths']['pc-1']['problem'], 'computer_changed')

    async def test_instructions_from_someone_else_are_reported(self):
        self.service.project_folders.Folders.rows['website'].data = {'system_prompt': 'Be brief.'}
        member_view = await self.service.describe_folder_project(MEMBER_USER, 'website')
        owner_view = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertTrue(member_view['shared_instructions'])
        self.assertFalse(owner_view['shared_instructions'])

    async def test_a_member_of_a_shared_folder_chooses_for_themselves_only(self):
        await self.save(MEMBER_USER, 'folder', {'member-pc': 'D:\\member\\site'})
        member_view = await self.service.describe_folder_project(MEMBER_USER, 'website')
        owner_view = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual(member_view['own']['paths']['member-pc']['path'], 'D:\\member\\site')
        self.assertIsNone(owner_view['own'])

    async def test_clearing_follows_the_parent_again(self):
        self.service.project_folders.Folders.add('work', user_id=OWNER, name='Work')
        self.service.project_folders.Folders.rows['website'].parent_id = 'work'
        await self.save(OWNER_USER, 'chat', folder_id='work')
        await self.save(OWNER_USER, 'auto')
        project = await self.service.clear_folder_project(OWNER_USER, 'website')
        self.assertIsNone(project['own'])
        self.assertEqual((project['inherited']['folder_name'], project['inherited']['mode']), ('Work', 'chat'))

    async def test_this_servers_folders_survive_the_policy_turning_it_off_for_a_while(self):
        folders = self.service.project_folders
        local = {'local': folders.SavedPath('/srv/site', 'local')}
        await folders.save_choice(OWNER, 'website', 'folder', local, {'local'})
        folders.Folders.add('notes', user_id=OWNER, name='Notes')
        await self.service.save_policy({'server_machine': False})
        await self.save(OWNER_USER, 'chat', folder_id='notes')
        choices = await folders.load_choices(OWNER)
        self.assertEqual(choices['website'].paths['local'].path, '/srv/site')

    async def test_other_paths_can_change_while_this_server_is_turned_off(self):
        folders = self.service.project_folders
        paths = {
            'local': folders.SavedPath('/srv/site', 'local'),
            'pc-1': folders.SavedPath('C:\\code\\site', 'fixture-revision'),
        }
        await folders.save_choice(OWNER, 'website', 'folder', paths, {'local', 'pc-1'})
        await self.service.save_policy({'server_machine': False})
        await self.save(OWNER_USER, 'folder', {'local': '/srv/site', 'pc-1': 'C:\\code\\docs'})
        choice = (await folders.load_choices(OWNER))['website']
        self.assertEqual(choice.paths['local'].path, '/srv/site')
        self.assertEqual(choice.paths['pc-1'].path, 'C:\\code\\docs')

    # --- Browsing a computer's folders ------------------------------------------

    async def test_folder_listings_are_checked_before_anyone_sees_them(self):
        self.machine.browsing_reply = {'entries': [{'name': 'site', 'path': 'D:\\elsewhere'}, {'name': '..'}]}
        listing = await self.service.list_machine_folders(OWNER_USER, 'pc-1', 'C:/code', False, 'si')
        self.assertEqual(self.machine.listed, [('C:\\code', False, 'si')])
        self.assertEqual([entry['path'] for entry in listing['entries']], ['C:\\code\\site'])
        self.machine.browsing_reply = {'roots': [{'name': 'C:', 'path': 'C:\\', 'kind': 'drive'}]}
        roots = await self.service.list_machine_folders(OWNER_USER, 'pc-1', '', False, '')
        self.assertEqual(roots['roots'], [{'name': 'C:', 'path': 'C:\\', 'kind': 'drive'}])

    async def test_folder_browsing_turned_off_or_missing_says_what_to_do(self):
        self.machine.browsing_reply = self.service.RunnerCallError(403, 'off')
        with self.assertRaisesRegex(SubscriptionError, 'Turn on Folder suggestions in the Buddy app on Test pc-1'):
            await self.service.list_machine_folders(OWNER_USER, 'pc-1', 'C:\\code', False, '')
        self.machine.browsing_reply = self.service.RunnerCallError(404, 'unknown')
        with self.assertRaisesRegex(SubscriptionError, 'Update the Buddy app on Test pc-1'):
            await self.service.list_machine_folders(OWNER_USER, 'pc-1', 'C:\\code', False, '')

    async def test_only_your_own_computers_can_be_browsed(self):
        with self.assertRaisesRegex(SubscriptionError, 'no longer exists'):
            await self.service.list_machine_folders(OWNER_USER, 'member-pc', 'D:\\', False, '')
        self.assertEqual(self.member_machine.listed, [])

    async def test_network_paths_are_never_listed(self):
        with self.assertRaisesRegex(SubscriptionError, 'Network paths'):
            await self.service.list_machine_folders(OWNER_USER, 'pc-1', '\\\\server\\share', False, '')
        self.assertEqual(self.machine.listed, [])

    async def test_suggestions_need_an_online_app_computer_and_are_kept_a_minute(self):
        self.assertEqual(
            await self.service.suggested_machine_folders(OWNER_USER, 'pc-1'), {'state': 'unsupported', 'folders': []}
        )
        self.config.values['subscriptions.machines'][0]['kind'] = 'runner'
        self.service._tunnel_machines['pc-1'] = self.machine
        hub = types.SimpleNamespace(is_online=lambda machine_id: False)
        with patch.object(self.service, 'HUB', hub):
            offline = await self.service.suggested_machine_folders(OWNER_USER, 'pc-1')
        self.assertEqual(offline['state'], 'offline')
        hub = types.SimpleNamespace(is_online=lambda machine_id: True)
        self.machine.browsing_reply = {'folders': [
            {'path': 'C:\\code\\site', 'sources': ['claude'], 'last_used': 1760300000},
            {'path': '\\\\server\\x', 'sources': ['codex'], 'last_used': 1},
        ]}
        with patch.object(self.service, 'HUB', hub):
            first = await self.service.suggested_machine_folders(OWNER_USER, 'pc-1')
            second = await self.service.suggested_machine_folders(OWNER_USER, 'pc-1')
        self.assertEqual(first['folders'], [{'path': 'C:\\code\\site', 'sources': ['claude'], 'last_used': 1760300000}])
        self.assertEqual(second, first)
        self.assertEqual(self.machine.suggest_calls, 1)

    async def test_each_computer_says_whether_its_folders_can_be_browsed(self):
        await self.save(OWNER_USER, 'folder', {'pc-1': 'C:\\code\\site'})
        project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual(self.computer(project)['folders'], 'unsupported')
        config = self.config.values['subscriptions.machines'][0]
        config['kind'] = 'runner'
        config['capabilities'] = {'folderBrowsing': True}
        online = types.SimpleNamespace(is_online=lambda machine_id: True)
        with patch.object(self.service, 'HUB', online):
            project = await self.service.describe_folder_project(OWNER_USER, 'website')
            self.assertEqual(self.computer(project)['folders'], 'on')
            # Learned from the app's answer that browsing is off there.
            self.service._folder_browsing[(OWNER, 'pc-1')] = 'off'
            project = await self.service.describe_folder_project(OWNER_USER, 'website')
            self.assertEqual(self.computer(project)['folders'], 'off')
        offline = types.SimpleNamespace(is_online=lambda machine_id: False)
        with patch.object(self.service, 'HUB', offline):
            project = await self.service.describe_folder_project(OWNER_USER, 'website')
        self.assertEqual(self.computer(project)['folders'], 'offline')

    async def test_a_deleted_users_choices_are_forgotten(self):
        await self.save(MEMBER_USER, 'chat')
        await self.service.forget_user(MEMBER)
        self.assertEqual(self.config.values['subscriptions.users.member-1.project_folders'], {})


if __name__ == '__main__':
    unittest.main()
