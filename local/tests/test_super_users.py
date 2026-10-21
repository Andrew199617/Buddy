"""Super users: the server owner, and the administrators the owner names.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_super_users.py -v
"""

import ast
import importlib.util
import types
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / 'backend' / 'open_webui' / 'utils' / 'super_users.py'

OWNER = types.SimpleNamespace(id='owner', role='admin')
ADMIN = types.SimpleNamespace(id='admin-2', role='admin')
MEMBER = types.SimpleNamespace(id='member', role='user')


class MemoryConfig:
    def __init__(self):
        self.values = {}

    async def get(self, key, default=None):
        return self.values.get(key, default)

    async def upsert(self, updates):
        self.values.update(updates)


def load_module(config):
    spec = importlib.util.spec_from_file_location('super_users_under_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def fake_config():
        return config

    async def fake_owner():
        return OWNER.id

    module._config = fake_config
    module.server_owner_id = fake_owner
    module._admin_chat_access = lambda: True
    return module


class SuperUserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.module = load_module(self.config)

    async def test_the_server_owner_always_is_one_and_other_admins_are_not(self):
        self.assertTrue(await self.module.is_super_user(OWNER))
        self.assertFalse(await self.module.is_super_user(ADMIN))
        self.assertFalse(await self.module.is_super_user(MEMBER))
        self.assertFalse(await self.module.is_super_user(None))

    async def test_the_owner_can_name_and_remove_an_administrator(self):
        described = await self.module.set_super_user(OWNER, ADMIN, True)
        self.assertEqual(described, {'owner_id': 'owner', 'user_ids': ['admin-2']})
        self.assertTrue(await self.module.is_super_user(ADMIN))
        # Naming twice keeps one entry.
        await self.module.set_super_user(OWNER, ADMIN, True)
        self.assertEqual(await self.module.named_super_user_ids(), ['admin-2'])
        await self.module.set_super_user(OWNER, ADMIN, False)
        self.assertFalse(await self.module.is_super_user(ADMIN))

    async def test_only_the_owner_chooses_and_only_administrators_can_be_chosen(self):
        with self.assertRaisesRegex(self.module.SuperUserError, 'Only the server owner'):
            await self.module.set_super_user(ADMIN, ADMIN, True)
        with self.assertRaisesRegex(self.module.SuperUserError, 'administrator first'):
            await self.module.set_super_user(OWNER, MEMBER, True)
        with self.assertRaisesRegex(self.module.SuperUserError, 'always a super user'):
            await self.module.set_super_user(OWNER, OWNER, False)
        with self.assertRaisesRegex(self.module.SuperUserError, 'no longer exists'):
            await self.module.set_super_user(OWNER, None, True)
        self.assertEqual(self.config.values, {})

    async def test_an_administrator_who_loses_the_role_stops_being_one(self):
        await self.module.set_super_user(OWNER, ADMIN, True)
        demoted = types.SimpleNamespace(id=ADMIN.id, role='user')
        self.assertFalse(await self.module.is_super_user(demoted))

    async def test_a_damaged_setting_names_nobody(self):
        self.config.values[self.module.SUPER_USERS_CONFIG_KEY] = 'admin-2'
        self.assertEqual(await self.module.named_super_user_ids(), [])
        self.config.values[self.module.SUPER_USERS_CONFIG_KEY] = ['admin-2', 7, '']
        self.assertEqual(await self.module.named_super_user_ids(), ['admin-2'])


    async def test_only_the_owner_or_the_person_changes_a_super_users_account(self):
        await self.module.set_super_user(OWNER, ADMIN, True)
        other_admin = types.SimpleNamespace(id='admin-3', role='admin')
        self.assertFalse(await self.module.may_change_account(other_admin, ADMIN))
        self.assertFalse(await self.module.may_change_account(other_admin, OWNER))
        self.assertTrue(await self.module.may_change_account(OWNER, ADMIN))
        self.assertTrue(await self.module.may_change_account(ADMIN, ADMIN))
        # Ordinary accounts stay with every administrator.
        self.assertTrue(await self.module.may_change_account(other_admin, MEMBER))
        self.assertTrue(await self.module.may_change_account(ADMIN, other_admin))

    async def test_a_demoted_super_user_is_not_one_again_when_promoted(self):
        await self.module.set_super_user(OWNER, ADMIN, True)
        await self.module.forget_super_user(ADMIN.id)
        self.assertFalse(await self.module.is_super_user(ADMIN))
        self.assertEqual(await self.module.named_super_user_ids(), [])
        # Forgetting someone who was never named writes nothing.
        self.config.values.clear()
        await self.module.forget_super_user(MEMBER.id)
        self.assertEqual(self.config.values, {})

    async def test_only_a_super_user_writes_into_someone_elses_chat(self):
        await self.module.set_super_user(OWNER, ADMIN, True)
        other_admin = types.SimpleNamespace(id='admin-3', role='admin')
        self.assertTrue(await self.module.may_change_others_chat(ADMIN))
        self.assertTrue(await self.module.may_change_others_chat(OWNER))
        self.assertFalse(await self.module.may_change_others_chat(other_admin))
        self.assertFalse(await self.module.may_change_others_chat(MEMBER))

    async def test_nobody_does_while_admins_may_not_see_chats(self):
        await self.module.set_super_user(OWNER, ADMIN, True)
        self.module._admin_chat_access = lambda: False
        self.assertFalse(await self.module.may_change_others_chat(ADMIN))
        self.assertFalse(await self.module.may_change_others_chat(OWNER))

    def test_config_files_never_carry_the_owner_or_super_users(self):
        imported = {
            self.module.SUPER_USERS_CONFIG_KEY: ['admin-2'],
            self.module.SERVER_OWNER_CONFIG_KEY: 'admin-2',
            'ui.default_locale': 'en-US',
        }
        self.assertEqual(self.module.without_server_identity(imported), {'ui.default_locale': 'en-US'})

    def test_the_owner_key_matches_the_subscription_service(self):
        service = (MODULE_PATH.parent / 'subscriptions' / 'service.py').read_text(encoding='utf-8')
        self.assertIn(f"SERVER_OWNER_CONFIG_KEY = '{self.module.SERVER_OWNER_CONFIG_KEY}'", service)



def load_changes_account():
    router = MODULE_PATH.parents[1] / 'routers' / 'users.py'
    parsed = ast.parse(router.read_text(encoding='utf-8'))
    selected = [node for node in parsed.body if getattr(node, 'name', None) == '_changes_account']
    if len(selected) != 1:
        raise AssertionError(f'_changes_account is missing from {router}')
    for node in selected:
        node.args.args[0].annotation = None
        node.args.args[1].annotation = None
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(router), 'exec'), namespace)
    return namespace['_changes_account']


class ChangesAccountTests(unittest.TestCase):
    def setUp(self):
        self.changes_account = load_changes_account()
        self.stored = types.SimpleNamespace(email='b@example.com', role='admin')

    def form(self, **fields):
        values = {'password': None, 'email': None, 'role': None, 'name': None}
        values.update(fields)
        return types.SimpleNamespace(**values)

    def test_the_same_email_and_role_with_a_new_name_is_not_an_account_change(self):
        # Edit User always sends both.
        form = self.form(email='B@example.com', role='admin', name='Bea')
        self.assertFalse(self.changes_account(form, self.stored))

    def test_a_new_password_email_or_role_is(self):
        self.assertTrue(self.changes_account(self.form(password='new-secret'), self.stored))
        self.assertTrue(self.changes_account(self.form(email='other@example.com'), self.stored))
        self.assertTrue(self.changes_account(self.form(role='user'), self.stored))


if __name__ == '__main__':
    unittest.main()
