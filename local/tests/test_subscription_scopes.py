"""Each user's providers, machines, and models stay their own.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_subscription_scopes.py -v

The service runs with in-memory config and stand-in providers, as in
test_subscription_machines.py; nothing starts a CLI or touches a database.
"""

import time
import types
import unittest

from test_subscription_machines import (
    OWNER,
    OWNER_USER,
    ROOT,
    FakeCodexProvider,
    FakeGroups,
    FakeProvider,
    MemoryConfig,
    load_service,
)

MEMBER = 'member-1'
MEMBER_USER = types.SimpleNamespace(id=MEMBER, role='user')
OTHER = 'member-2'
OTHER_USER = types.SimpleNamespace(id=OTHER, role='user')


def member_machine(machine_id, user_id):
    return {
        'id': machine_id,
        'user_id': user_id,
        'name': f'Fixture {machine_id}',
        'url': f'https://{machine_id}.example.invalid:8083',
        'key': f'fixture-key-{machine_id}',
        'revision': f'fixture-revision-{machine_id}',
    }


def signed_in_model():
    return types.SimpleNamespace(key='fixture-model', value='fixture-model', name='Fixture model',
                                 description='', efforts=[])


class SubscriptionScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig({
            'subscriptions.machines': [
                {'id': 'owner-pc', 'name': 'Owner PC', 'url': 'https://owner-pc.example.invalid:8083',
                 'key': 'fixture-key-owner', 'revision': 'fixture-revision-owner'},
                member_machine('member-pc', MEMBER),
            ],
        })
        self.service = load_service(self.config, ROOT / 'unused-offline-scope-data')
        self.service._providers = {}
        for user_id in (OWNER, MEMBER, OTHER):
            self.service._providers[self.service.ProviderRef(user_id, 'claude')] = FakeProvider()
            self.service._providers[self.service.ProviderRef(user_id, 'codex')] = FakeCodexProvider()

    def ref(self, user_id, provider_id='claude'):
        return self.service.ProviderRef(user_id, provider_id)

    def enable_signed_in(self, user_id, settings_key, machine_id):
        self.config.values[settings_key] = {'enable': True, 'access': 'chat', 'machine_id': machine_id}
        ref = self.ref(user_id)
        self.service.status_cache._entries[ref] = self.service._CacheEntry(
            {'installed': True, 'signed_in': True, 'api_billing': False}, time.monotonic() + 60
        )
        self.service.models_cache._entries[ref] = self.service._CacheEntry([signed_in_model()], time.monotonic() + 60)

    async def test_the_owner_keeps_the_original_keys_and_members_get_their_own(self):
        self.assertEqual(await self.service._settings_key(self.ref(OWNER)), 'subscriptions.claude')
        self.assertEqual(await self.service._settings_key(self.ref(MEMBER)), 'subscriptions.users.member-1.claude')

    async def test_new_members_start_with_no_computer_chosen_and_the_owner_with_this_server(self):
        self.assertEqual((await self.service.get_settings(MEMBER, 'claude')).machine_id, '')
        self.assertEqual((await self.service.get_settings(OWNER, 'claude')).machine_id, 'local')

    async def test_a_member_sees_only_their_own_machines(self):
        self.assertEqual([machine['id'] for machine in await self.service.describe_machines(MEMBER)], ['member-pc'])
        self.assertEqual(await self.service.describe_machines(OTHER), [])
        owner_machines = [machine['id'] for machine in await self.service.describe_machines(OWNER)]
        self.assertEqual(owner_machines, ['local', 'owner-pc'])

    async def test_another_users_machine_cannot_be_used(self):
        with self.assertRaisesRegex(self.service.SubscriptionError, 'no longer exists'):
            await self.service.get_machine(OTHER, 'member-pc')
        with self.assertRaisesRegex(self.service.SubscriptionError, 'no longer exists'):
            await self.service.get_machine(MEMBER, 'owner-pc')
        with self.assertRaisesRegex(self.service.SubscriptionError, 'no longer exists'):
            await self.service.save_settings(OTHER, 'claude', {'machine_id': 'member-pc', 'enable': True})
        self.assertNotIn('subscriptions.users.member-2.claude', self.config.values)

    async def test_this_server_is_only_for_the_owner_and_only_while_the_policy_allows_it(self):
        with self.assertRaisesRegex(self.service.SubscriptionError, 'not available to your account'):
            await self.service.get_machine(MEMBER, 'local')
        self.assertIs(await self.service.get_machine(OWNER, 'local'), self.service.LOCAL_MACHINE)
        await self.service.save_policy({'server_machine': False})
        with self.assertRaisesRegex(self.service.SubscriptionError, 'not available to your account'):
            await self.service.get_machine(OWNER, 'local')
        self.assertEqual([machine['id'] for machine in await self.service.describe_machines(OWNER)], ['owner-pc'])

    async def test_a_member_without_a_computer_is_told_to_choose_one(self):
        description = await self.service.describe_provider(MEMBER, 'claude')
        self.assertFalse(description['status']['signed_in'])
        self.assertIn('Choose one of your computers', description['status']['message'])
        self.assertEqual(description['machine']['name'], 'No computer chosen')

    async def test_models_are_listed_only_for_the_user_whose_provider_offers_them(self):
        self.enable_signed_in(MEMBER, 'subscriptions.users.member-1.claude', 'member-pc')
        member_models = await self.service.get_user_subscription_models(MEMBER_USER)
        self.assertEqual([model['id'] for model in member_models], ['claude-code.fixture-model'])
        self.assertEqual(await self.service.get_user_subscription_models(OTHER_USER), [])
        self.assertTrue(await self.service.user_offers_model(MEMBER_USER, 'claude-code.fixture-model'))
        self.assertFalse(await self.service.user_offers_model(OTHER_USER, 'claude-code.fixture-model'))

    async def test_the_catalog_registers_every_known_model_but_each_user_sees_their_own(self):
        self.enable_signed_in(MEMBER, 'subscriptions.users.member-1.claude', 'member-pc')
        catalog = await self.service.get_subscription_catalog(MEMBER_USER)
        self.assertEqual([model['id'] for model in catalog], ['claude-code.fixture-model'])
        # Another user's request still registers it, so routing by ID works...
        catalog = await self.service.get_subscription_catalog(OTHER_USER)
        self.assertEqual([model['id'] for model in catalog], ['claude-code.fixture-model'])
        # ...but their filtered list leaves it out.
        self.assertEqual(await self.service.personalize_models(catalog, OTHER_USER), [])
        personalized = await self.service.personalize_models(catalog, MEMBER_USER)
        self.assertEqual([model['id'] for model in personalized], ['claude-code.fixture-model'])

    async def test_a_turn_on_a_model_the_user_does_not_offer_is_refused(self):
        self.enable_signed_in(OTHER, 'subscriptions.users.member-2.claude', 'member-pc')
        ref = self.ref(OTHER)
        self.service.models_cache._entries[ref] = self.service._CacheEntry([], time.monotonic() + 60)
        with self.assertRaisesRegex(self.service.SubscriptionError, 'does not offer this model'):
            self.service._turn_model(ref, 'claude-code.fixture-model', {'model': 'fixture-model'})

    async def test_removing_a_machine_affects_only_its_owner(self):
        self.config.values['subscriptions.users.member-1.claude'] = {
            'enable': True, 'access': 'full', 'workspace': 'C:\\fixture', 'machine_id': 'member-pc',
        }
        await self.service.delete_machine(OTHER, 'member-pc')
        self.assertIn('member-pc', [machine['id'] for machine in self.config.values['subscriptions.machines']])
        self.assertTrue(self.config.values['subscriptions.users.member-1.claude']['enable'])
        await self.service.delete_machine(MEMBER, 'member-pc')
        self.assertNotIn('member-pc', [machine['id'] for machine in self.config.values['subscriptions.machines']])
        settings = self.config.values['subscriptions.users.member-1.claude']
        self.assertFalse(settings['enable'])
        self.assertEqual(settings['machine_id'], 'member-pc')
        self.assertEqual(settings['access'], 'chat')

    async def test_forgetting_a_user_removes_their_machines_and_settings_only(self):
        self.config.values['subscriptions.users.member-1.claude'] = {'enable': True, 'machine_id': 'member-pc'}
        self.config.values['subscriptions.claude'] = {'enable': True, 'machine_id': 'owner-pc'}
        await self.service.forget_user(MEMBER)
        self.assertEqual([machine['id'] for machine in self.config.values['subscriptions.machines']], ['owner-pc'])
        self.assertEqual(self.config.values['subscriptions.users.member-1.claude'], {})
        self.assertEqual(self.config.values['subscriptions.claude'], {'enable': True, 'machine_id': 'owner-pc'})


class SubscriptionPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.service = load_service(self.config, ROOT / 'unused-offline-policy-data')

    async def test_everyone_may_use_machines_by_default(self):
        self.assertTrue(await self.service.may_use_machines(MEMBER_USER))
        self.assertTrue(await self.service.may_use_machines(OWNER_USER))
        self.assertFalse(await self.service.may_use_machines(types.SimpleNamespace(id='pending-1', role='pending')))
        self.assertFalse(await self.service.may_use_machines(None))

    async def test_administrators_only(self):
        await self.service.save_policy({'user_machines': 'admins'})
        self.assertFalse(await self.service.may_use_machines(MEMBER_USER))
        self.assertTrue(await self.service.may_use_machines(types.SimpleNamespace(id='admin-2', role='admin')))
        # The instance owner keeps their own setup whatever the policy says.
        self.assertTrue(await self.service.may_use_machines(OWNER_USER))

    async def test_selected_groups(self):
        self.service.Groups = FakeGroups({MEMBER: ['makers']})
        await self.service.save_policy({'user_machines': 'groups', 'machine_group_ids': ['makers', 'makers']})
        self.assertEqual((await self.service.get_policy()).machine_group_ids, ['makers'])
        self.assertTrue(await self.service.may_use_machines(MEMBER_USER))
        self.assertFalse(await self.service.may_use_machines(OTHER_USER))

    async def test_sign_in_switches_are_saved(self):
        policy = await self.service.save_policy({'plan_sign_in': False, 'api_keys': True})
        self.assertFalse(policy.plan_sign_in)
        self.assertTrue(policy.api_keys)
        self.assertFalse(self.config.values['subscriptions.policy']['plan_sign_in'])

    async def test_an_unknown_audience_is_refused(self):
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.save_policy({'user_machines': 'everyone-everywhere'})
        self.assertNotIn('subscriptions.policy', self.config.values)

    async def test_the_owner_is_the_first_administrator_and_is_remembered(self):
        self.service._server_owner_id = None
        self.assertEqual(await self.service.server_owner_id(), OWNER)
        self.assertEqual(self.config.values['subscriptions.owner'], OWNER)


if __name__ == '__main__':
    unittest.main()
