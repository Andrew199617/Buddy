"""The project folder API for chat folders: checking a folder on a computer, and saving each person's choice.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_project_folder_api.py -v
"""

import types
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
    """A computer added by address that has some folders, and can go offline."""

    def __init__(self, config, existing):
        super().__init__(config['id'], config['name'], config['url'], config['key'])
        self.existing = set(existing)
        self.offline = False

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
        [computer] = project['computers']
        self.assertEqual(computer['machine_id'], 'pc-1')
        self.assertEqual((computer['kind'], computer['platform'], computer['can_browse']), ('address', 'win32', False))
        self.assertEqual(computer['providers'], [{'id': 'claude', 'name': 'Claude', 'access': 'full'}])
        self.assertEqual(self.machine.path_checks, [])

    async def test_a_saved_folder_round_trips_with_recent_paths(self):
        project = await self.save(OWNER_USER, 'folder', {'pc-1': 'C:/code/site'})
        self.assertEqual(project['own']['paths'], {'pc-1': {'path': 'C:\\code\\site', 'problem': None}})
        self.assertEqual(project['computers'][0]['recent_paths'], ['C:\\code\\site'])

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

    async def test_a_deleted_users_choices_are_forgotten(self):
        await self.save(MEMBER_USER, 'chat')
        await self.service.forget_user(MEMBER)
        self.assertEqual(self.config.values['subscriptions.users.member-1.project_folders'], {})


if __name__ == '__main__':
    unittest.main()
