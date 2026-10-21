"""Super users: the server owner, and the administrators the owner names.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_super_users.py -v
"""

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


if __name__ == '__main__':
    unittest.main()
