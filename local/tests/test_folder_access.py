"""Who may read, edit, add chats to, and delete someone else's chat folder (routers/folders.py _folder_access).

Run from the repository root:
    python -m unittest discover -s local/tests -p test_folder_access.py -v

The helper is read straight from the router, so the test runs it without importing Buddy's database or configuration.
"""

import ast
import types
import unittest
from dataclasses import dataclass
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[2] / 'backend' / 'open_webui' / 'routers' / 'folders.py'

OWNER = types.SimpleNamespace(id='owner', role='user')
EDITOR = types.SimpleNamespace(id='editor', role='user')
READER = types.SimpleNamespace(id='reader', role='user')
STRANGER = types.SimpleNamespace(id='stranger', role='user')
ADMIN = types.SimpleNamespace(id='admin', role='admin')
GRANTED_ADMIN = types.SimpleNamespace(id='granted-admin', role='admin')
SUPER_USER = types.SimpleNamespace(id='super', role='admin')

FOLDER = types.SimpleNamespace(id='website', user_id=OWNER.id)

GRANTS = {
    (EDITOR.id, 'write'),
    (EDITOR.id, 'read'),
    (READER.id, 'read'),
    (GRANTED_ADMIN.id, 'write'),
    (GRANTED_ADMIN.id, 'read'),
}


def load_folder_access():
    parsed = ast.parse(ROUTER.read_text(encoding='utf-8'))
    wanted = {'FolderAccess', '_folder_access'}
    selected = [node for node in parsed.body if getattr(node, 'name', None) in wanted]
    if len(selected) != len(wanted):
        raise AssertionError(f'Missing definitions in {ROUTER}: {wanted}')

    async def has_folder_access(user_id, folder, permission, db):
        return user_id == folder.user_id or (user_id, permission) in GRANTS

    async def is_super_user(user):
        return user.id == SUPER_USER.id

    namespace = {'dataclass': dataclass, '_has_folder_access': has_folder_access, 'is_super_user': is_super_user}
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(ROUTER), 'exec'), namespace)
    return namespace['_folder_access']


folder_access = load_folder_access()


class FolderAccessTests(unittest.IsolatedAsyncioTestCase):
    async def access(self, user):
        found = await folder_access(user, FOLDER, None)
        return {'read': found.read, 'edit': found.edit, 'add_chats': found.add_chats, 'delete': found.delete}

    async def test_the_owner_may_do_everything(self):
        self.assertEqual(await self.access(OWNER), {'read': True, 'edit': True, 'add_chats': True, 'delete': True})

    async def test_a_write_grant_edits_and_adds_chats_but_cannot_delete(self):
        self.assertEqual(await self.access(EDITOR), {'read': True, 'edit': True, 'add_chats': True, 'delete': False})

    async def test_a_read_grant_only_reads(self):
        self.assertEqual(await self.access(READER), {'read': True, 'edit': False, 'add_chats': False, 'delete': False})

    async def test_anyone_else_gets_nothing(self):
        self.assertEqual(
            await self.access(STRANGER), {'read': False, 'edit': False, 'add_chats': False, 'delete': False}
        )

    async def test_an_administrator_reads_every_folder_but_changes_none(self):
        self.assertEqual(await self.access(ADMIN), {'read': True, 'edit': False, 'add_chats': False, 'delete': False})

    async def test_an_administrator_with_a_write_grant_is_an_ordinary_editor(self):
        self.assertEqual(
            await self.access(GRANTED_ADMIN), {'read': True, 'edit': True, 'add_chats': True, 'delete': False}
        )

    async def test_a_super_user_edits_and_deletes_but_needs_a_grant_to_start_chats(self):
        # A new chat there would run with the owner's folder instructions and settings.
        self.assertEqual(
            await self.access(SUPER_USER), {'read': True, 'edit': True, 'add_chats': False, 'delete': True}
        )


if __name__ == '__main__':
    unittest.main()
