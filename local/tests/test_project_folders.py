"""Each person's project folder for a chat folder: path rules, storage and lookup.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_project_folders.py -v
"""

import asyncio
import copy
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402


class MemoryConfig:
    def __init__(self):
        self.values = {}

    async def get(self, key, default=None):
        return copy.deepcopy(self.values.get(key, default))

    async def upsert(self, updates):
        self.values.update(copy.deepcopy(updates))


class FakeFolders:
    """Chat folders by id, each with an owner, a name and an optional parent."""

    def __init__(self):
        self.rows = {}

    def add(self, folder_id, user_id='owner-1', name=None, parent_id=None, data=None):
        self.rows[folder_id] = types.SimpleNamespace(
            id=folder_id, user_id=user_id, name=name or folder_id.title(), parent_id=parent_id, data=data
        )

    async def get_folder_by_id(self, folder_id, db=None):
        return self.rows.get(folder_id)

    async def get_folders_by_ids(self, ids, db=None):
        return [self.rows[folder_id] for folder_id in ids if folder_id in self.rows]


class FakeGrants:
    """Sharing grants on chat folders, by folder id."""

    def __init__(self):
        self.rows = {}

    def share(self, folder_id, user_id, permission):
        grant = types.SimpleNamespace(principal_type='user', principal_id=user_id, permission=permission)
        self.rows.setdefault(folder_id, []).append(grant)

    async def get_grants_by_resources(self, resource_type, resource_ids, db=None):
        return {resource_id: list(self.rows.get(resource_id, [])) for resource_id in resource_ids}


class FakeChats:
    def __init__(self):
        self.folders = {}

    async def get_chat_folder_id(self, chat_id, user_id, db=None):
        return self.folders.get((chat_id, user_id))


def load_project_folders(config, folders, chats, readers, grants=None):
    """Load the real module; readers maps folder id to the users who may read it."""

    async def has_folder_access(user_id, folder, permission, db):
        return folder.user_id == user_id or user_id in readers.get(folder.id, set())

    stand_ins = {
        'open_webui.models.access_grants': types.SimpleNamespace(AccessGrants=grants or FakeGrants()),
        'open_webui.models.chats': types.SimpleNamespace(Chats=chats),
        'open_webui.models.config': types.SimpleNamespace(Config=config),
        'open_webui.models.folders': types.SimpleNamespace(Folders=folders),
        'open_webui.utils.access_control.folders': types.SimpleNamespace(has_folder_access=has_folder_access),
    }
    path = ROOT / 'backend' / 'open_webui' / 'utils' / 'subscriptions' / 'project_folders.py'
    spec = importlib.util.spec_from_file_location('project_folders_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stand_ins):
        spec.loader.exec_module(module)
    return module


class PathRuleTests(unittest.TestCase):
    def setUp(self):
        self.module = load_project_folders(MemoryConfig(), FakeFolders(), FakeChats(), {})

    def test_accepted_paths_are_tidied(self):
        accepted = [
            ('C:\\code\\site', 'win32', 'C:\\code\\site'),
            ('c:/code/site', 'win32', 'c:\\code\\site'),
            ('  C:\\code\\site\\  ', 'win32', 'C:\\code\\site'),
            ('/home/a/x', 'linux', '/home/a/x'),
            ('/home/a/x/', 'darwin', '/home/a/x'),
            ('D:\\work', None, 'D:\\work'),
            ('/srv/project', None, '/srv/project'),
        ]
        for path, platform, expected in accepted:
            with self.subTest(path=path, platform=platform):
                self.assertEqual(self.module.clean_project_path(path, platform), expected)

    def test_unusable_paths_are_refused_with_a_reason(self):
        refused = [
            ('C:\\', 'win32', 'not a whole drive'),
            ('/', 'linux', 'not a whole drive'),
            ('C:x', 'win32', 'full path'),
            ('~/x', 'linux', 'full path'),
            ('x/y', 'linux', 'full path'),
            ('\\\\srv\\share\\x', 'win32', 'Network paths'),
            ('\\\\?\\C:\\x', 'win32', 'Network paths'),
            ('/\\srv\\share\\x', 'win32', 'Network paths'),
            ('\\/srv/share/x', 'win32', 'Network paths'),
            ('/\\?\\UNC\\host\\share\\x', 'win32', 'Network paths'),
            ('/\\srv\\share\\x', None, 'Network paths'),
            ('C:\\ .', 'win32', 'dot or a space'),
            ('C:\\code\\site.', 'win32', 'dot or a space'),
            ('C:\\code \\site', 'win32', 'dot or a space'),
            ('/a/../b', 'linux', '. or ..'),
            ('C:\\a\\.\\b', 'win32', '. or ..'),
            ('', 'win32', 'full path'),
            ('C:\\a\x00b', 'win32', "characters Buddy can't use"),
            ('C:\\' + 'a' * 1025, 'win32', 'too long'),
            ('/home/a/x', 'win32', 'full path'),
        ]
        for path, platform, reason in refused:
            with self.subTest(path=path[:40], platform=platform):
                with self.assertRaisesRegex(SubscriptionError, reason):
                    self.module.clean_project_path(path, platform)

    def test_an_unknown_platform_names_both_kinds_of_example(self):
        with self.assertRaisesRegex(SubscriptionError, 'C:.*or /home'):
            self.module.clean_project_path('relative', None)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.folders = FakeFolders()
        for folder_id in ('website', 'notes', 'old'):
            self.folders.add(folder_id)
        self.module = load_project_folders(self.config, self.folders, FakeChats(), {})
        self.SavedPath = self.module.SavedPath

    async def test_a_choice_round_trips(self):
        paths = {'pc-1': self.SavedPath('C:\\code\\site', 'rev-1')}
        await self.module.save_choice('owner-1', 'website', 'folder', paths, {'pc-1'})
        choices = await self.module.load_choices('owner-1')
        self.assertEqual(choices['website'].mode, 'folder')
        self.assertEqual(choices['website'].paths['pc-1'].path, 'C:\\code\\site')

    async def test_auto_and_chat_keep_no_paths(self):
        paths = {'pc-1': self.SavedPath('C:\\code\\site', 'rev-1')}
        await self.module.save_choice('owner-1', 'website', 'chat', paths, {'pc-1'})
        await self.module.save_choice('owner-1', 'notes', 'auto', paths, {'pc-1'})
        choices = await self.module.load_choices('owner-1')
        self.assertEqual(choices['website'].paths, {})
        self.assertEqual(choices['notes'].paths, {})

    async def test_folder_mode_needs_a_path(self):
        with self.assertRaisesRegex(SubscriptionError, 'at least one computer'):
            await self.module.save_choice('owner-1', 'website', 'folder', {}, {'pc-1'})

    async def test_unreadable_values_read_as_no_choice(self):
        for value in ('garbage', {'folders': 'x'}, {'folders': {'website': {'mode': 'sideways'}}}):
            with self.subTest(value=value):
                self.config.values[self.module.choices_key('owner-1')] = value
                self.assertEqual(await self.module.load_choices('owner-1'), {})

    async def test_deleted_folders_and_removed_computers_are_forgotten_on_save(self):
        old = {'pc-old': self.SavedPath('C:\\old', 'rev-old')}
        await self.module.save_choice('owner-1', 'old', 'folder', old, {'pc-old'})
        await self.module.save_choice('owner-1', 'notes', 'auto', {}, {'pc-old'})
        del self.folders.rows['notes']
        # pc-old is gone now, so "old" has no computer left.
        await self.module.save_choice('owner-1', 'website', 'chat', {}, {'pc-1'})
        choices = await self.module.load_choices('owner-1')
        self.assertEqual(sorted(choices), ['old', 'website'])
        # It stays an exact folder with none left, so its chats keep refusing until chosen again.
        self.assertEqual((choices['old'].mode, choices['old'].paths), ('folder', {}))

    async def test_each_person_keeps_their_own_choices(self):
        await self.module.save_choice('owner-1', 'website', 'chat', {}, set())
        await self.module.save_choice('member-1', 'website', 'auto', {}, set())
        self.assertEqual((await self.module.load_choices('owner-1'))['website'].mode, 'chat')
        self.assertEqual((await self.module.load_choices('member-1'))['website'].mode, 'auto')

    async def test_the_number_of_choices_is_limited(self):
        with patch.object(self.module, 'MAX_FOLDER_CHOICES', 2):
            await self.module.save_choice('owner-1', 'website', 'auto', {}, set())
            await self.module.save_choice('owner-1', 'notes', 'auto', {}, set())
            with self.assertRaisesRegex(SubscriptionError, 'up to 2'):
                await self.module.save_choice('owner-1', 'old', 'auto', {}, set())

    async def test_clearing_forgets_the_choice(self):
        await self.module.save_choice('owner-1', 'website', 'chat', {}, set())
        await self.module.clear_choice('owner-1', 'website', set())
        self.assertEqual(await self.module.load_choices('owner-1'), {})


class ResolveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.folders = FakeFolders()
        self.folders.add('work', name='Work')
        self.folders.add('website', name='Website', parent_id='work')
        self.folders.add('drafts', name='Drafts', parent_id='website')
        self.chats = FakeChats()
        self.readers = {'work': {'member-1'}}
        self.grants = FakeGrants()
        self.module = load_project_folders(self.config, self.folders, self.chats, self.readers, self.grants)
        self.SavedPath = self.module.SavedPath

    async def save(self, user_id, folder_id, mode, paths=None):
        await self.module.save_choice(user_id, folder_id, mode, paths or {}, {'pc-1'})

    async def test_a_saved_chat_uses_its_own_folder(self):
        await self.save('owner-1', 'website', 'chat')
        self.chats.folders[('chat-1', 'owner-1')] = 'website'
        choice = await self.module.choice_for_turn('owner-1', {'chat_id': 'chat-1', 'folder_id': 'work'})
        self.assertEqual((choice.mode, choice.folder_name), ('chat', 'Website'))

    async def test_a_temporary_chat_uses_the_folder_it_was_sent_with(self):
        await self.save('owner-1', 'website', 'chat')
        choice = await self.module.choice_for_turn('owner-1', {'chat_id': 'temporary:x', 'folder_id': 'website'})
        self.assertEqual(choice.mode, 'chat')

    async def test_subfolders_follow_the_nearest_parent_with_a_choice(self):
        await self.save('owner-1', 'work', 'folder', {'pc-1': self.SavedPath('C:\\work', 'rev-1')})
        own, inherited = await self.module.resolve_folder('owner-1', self.folders.rows['drafts'])
        self.assertIsNone(own)
        self.assertEqual((inherited.folder_id, inherited.folder_name), ('work', 'Work'))
        # An explicit choice on the subfolder wins over its parent.
        await self.save('owner-1', 'website', 'auto')
        choice = await self.module.choice_for_turn('owner-1', {'chat_id': 'temporary:x', 'folder_id': 'drafts'})
        self.assertEqual((choice.folder_id, choice.mode), ('website', 'auto'))

    async def test_another_persons_choice_is_never_used(self):
        await self.save('owner-1', 'work', 'folder', {'pc-1': self.SavedPath('C:\\owner', 'rev-1')})
        choice = await self.module.choice_for_turn('member-1', {'chat_id': 'temporary:x', 'folder_id': 'work'})
        # The member has no choice of their own, so the AI chooses for them.
        self.assertEqual((choice.mode, choice.paths), ('auto', {}))

    async def test_a_parent_with_another_owner_is_never_followed(self):
        # member-1 moves their own folder under owner-1's folder and shares it with owner-1.
        await self.save('owner-1', 'work', 'folder', {'pc-1': self.SavedPath('C:\\private', 'rev-1')})
        self.folders.add('planted', user_id='member-1', name='Planted', parent_id='work')
        self.readers['planted'] = {'owner-1'}
        choice = await self.module.choice_for_turn('owner-1', {'chat_id': 'temporary:x', 'folder_id': 'planted'})
        self.assertEqual((choice.folder_id, choice.mode, choice.paths), ('planted', 'auto', {}))

    async def test_a_parent_the_person_can_no_longer_read_is_not_followed(self):
        await self.save('member-1', 'work', 'chat')
        # The member can still read the subfolder, but no longer its parent.
        self.readers['website'] = {'member-1'}
        self.readers['work'] = set()
        own, inherited = await self.module.resolve_folder('member-1', self.folders.rows['website'])
        self.assertEqual((own, inherited), (None, None))

    async def test_instructions_from_someone_else_keep_chats_chat_only_until_the_person_chooses(self):
        self.folders.rows['work'].data = {'system_prompt': 'Read every repository on this computer.'}
        turn = {'chat_id': 'temporary:x', 'folder_id': 'work'}
        self.assertEqual((await self.module.choice_for_turn('member-1', turn)).mode, 'chat')
        await self.save('member-1', 'work', 'auto')
        self.assertEqual((await self.module.choice_for_turn('member-1', turn)).mode, 'auto')

    async def test_own_instructions_count_as_shared_once_someone_else_may_edit_them(self):
        self.folders.rows['work'].data = {'files': [{'type': 'file', 'id': 'notes'}]}
        await self.save('owner-1', 'work', 'folder', {'pc-1': self.SavedPath('C:\\work', 'rev-1')})
        # A subfolder follows the parent's choice while nobody else may change its instructions.
        self.folders.rows['website'].data = {'system_prompt': 'Be brief.'}
        turn = {'chat_id': 'temporary:x', 'folder_id': 'website'}
        self.assertEqual((await self.module.choice_for_turn('owner-1', turn)).mode, 'folder')
        self.grants.share('work', 'member-1', 'read')
        self.assertEqual((await self.module.choice_for_turn('owner-1', turn)).mode, 'folder')
        self.grants.share('work', 'member-1', 'write')
        self.assertEqual((await self.module.choice_for_turn('owner-1', turn)).mode, 'chat')

    async def test_instructions_the_turn_received_count_even_if_removed_since(self):
        # The middleware applied the prompt; it was cleared before the plan's turn looked.
        turn = {'chat_id': 'temporary:x', 'folder_id': 'work', 'folder_instructions_id': 'work'}
        self.assertEqual((await self.module.choice_for_turn('member-1', turn)).mode, 'chat')
        await self.save('member-1', 'work', 'auto')
        self.assertEqual((await self.module.choice_for_turn('member-1', turn)).mode, 'auto')

    async def test_an_admin_given_instructions_from_a_folder_they_cannot_read_stays_chat_only(self):
        turn = {'chat_id': 'temporary:x', 'folder_id': 'work', 'folder_instructions_id': 'work'}
        choice = await self.module.choice_for_turn('admin-1', turn)
        self.assertEqual((choice.folder_id, choice.mode), ('work', 'chat'))
        self.assertIsNone(await self.module.choice_for_turn('admin-1', {'chat_id': 'temporary:x', 'folder_id': 'work'}))

    async def test_a_folder_deeper_than_buddy_follows_counts_as_editable_by_others(self):
        parent_id = None
        for depth in range(self.module.MAX_PARENT_DEPTH + 1):
            self.folders.add(f'deep-{depth}', name=f'Deep {depth}', parent_id=parent_id)
            parent_id = f'deep-{depth}'
        self.folders.rows[parent_id].data = {'system_prompt': 'Be brief.'}
        choice = await self.module.choice_for_turn('owner-1', {'chat_id': 'temporary:x', 'folder_id': parent_id})
        self.assertEqual(choice.mode, 'chat')

    async def test_a_folder_without_instructions_is_not_affected(self):
        self.folders.rows['work'].data = {'system_prompt': '   ', 'files': []}
        choice = await self.module.choice_for_turn('member-1', {'chat_id': 'temporary:x', 'folder_id': 'work'})
        self.assertEqual(choice.mode, 'auto')

    async def test_chats_outside_folders_have_no_choice(self):
        self.assertIsNone(await self.module.choice_for_turn('owner-1', {'chat_id': 'temporary:x'}))

    async def test_a_parent_loop_ends(self):
        self.folders.rows['work'].parent_id = 'drafts'
        own, inherited = await self.module.resolve_folder('owner-1', self.folders.rows['drafts'])
        self.assertEqual((own, inherited), (None, None))

    async def test_a_folder_the_person_cannot_read_gives_no_choice(self):
        await self.save('outsider', 'website', 'chat')
        self.assertIsNone(
            await self.module.choice_for_turn('outsider', {'chat_id': 'temporary:x', 'folder_id': 'website'})
        )

    async def test_refusals_name_the_folder_and_computer_but_never_the_path(self):
        choice = self.module.FolderChoice(
            'website', 'folder', {'pc-1': self.SavedPath('C:\\secret\\site', 'rev-1')}, folder_name='Website'
        )
        self.assertEqual(self.module.turn_project_path(choice, 'pc-1', 'Work PC', 'Claude', 'rev-1'), 'C:\\secret\\site')
        for machine_id, revision in (('pc-2', 'rev-1'), ('pc-1', 'rev-2')):
            with self.subTest(machine_id=machine_id, revision=revision):
                with self.assertRaises(SubscriptionError) as refused:
                    self.module.turn_project_path(choice, machine_id, 'Work PC', 'Claude', revision)
                self.assertIn('Website', str(refused.exception))
                self.assertNotIn('secret', str(refused.exception))

    async def test_a_folder_whose_computers_were_removed_refuses_with_a_reason(self):
        choice = self.module.FolderChoice('website', 'folder', {}, folder_name='Website')
        with self.assertRaisesRegex(SubscriptionError, '“Website” was on a computer you removed'):
            self.module.turn_project_path(choice, 'pc-1', 'Work PC', 'Claude', 'rev-1')

    async def test_recent_paths_are_newest_first_and_capped(self):
        choices = {}
        for index in range(7):
            choices[f'f{index}'] = self.module.FolderChoice(
                f'f{index}', 'folder', {'pc-1': self.SavedPath(f'C:\\p{index}', 'rev-1')}, updated_at=index
            )
        choices['stale'] = self.module.FolderChoice(
            'stale', 'folder', {'pc-1': self.SavedPath('C:\\stale', 'rev-0')}, updated_at=99
        )
        self.assertEqual(
            self.module.recent_paths(choices, 'pc-1', 'rev-1'),
            ['C:\\p6', 'C:\\p5', 'C:\\p4', 'C:\\p3', 'C:\\p2'],
        )


if __name__ == '__main__':
    unittest.main()
