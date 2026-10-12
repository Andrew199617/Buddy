"""Storing a Claude token or the user's own API key, and giving it to their machines.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_credential_modes.py -v

The service runs with in-memory config and stored sign-ins. Every secret here
is a fixture, and the tests check none reaches the logs.
"""

import json
import logging
import time
import types
import unittest

from test_subscription_machines import OWNER, ROOT, FakeCodexProvider, FakeProvider, MemoryConfig, load_service

from open_webui.utils.subscriptions.common import (
    CREDENTIAL_API_KEY,
    CREDENTIAL_MACHINE,
    CREDENTIAL_PLAN_TOKEN,
)
from open_webui.utils.subscriptions.events import SubscriptionError

FIXTURE_API_KEY = 'sk-ant-api03-fixture-key-0000000000000000AbCd'
FIXTURE_OPENAI_KEY = 'sk-proj-fixture-openai-key-00000000000WxYz'
FIXTURE_TOKEN = 'sk-ant-oat01-fixture-token-000000000000000Tail'


class IdleProcess:
    returncode = None
    cleanup_confirmed = False
    disposal_started = False


class RecordingMachine:
    id = 'local'
    name = 'This server'

    def __init__(self):
        self.started = []

    async def start_process(self, args, cwd, extra_env=None, temp_files=None):
        self.started.append(extra_env)
        return IdleProcess()


class ServiceCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig({'subscriptions.claude': {'machine_id': 'local'}, 'subscriptions.codex': {'machine_id': 'local'}})
        self.service = load_service(self.config, ROOT / 'unused-offline-credential-data')
        self.claude = FakeProvider()
        self.codex = FakeCodexProvider()
        self.service._providers = {
            self.service.ProviderRef(OWNER, 'claude'): self.claude,
            self.service.ProviderRef(OWNER, 'codex'): self.codex,
        }
        self.log_records = []
        handler = logging.Handler(level=logging.DEBUG)
        handler.emit = lambda record: self.log_records.append(record.getMessage())
        root = logging.getLogger()
        root.addHandler(handler)
        previous_level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous_level)

    def ref(self, provider_id):
        return self.service.ProviderRef(OWNER, provider_id)

    def tearDown(self):
        for secret in (FIXTURE_API_KEY, FIXTURE_OPENAI_KEY, FIXTURE_TOKEN):
            self.assertFalse(any(secret in message for message in self.log_records), 'A secret reached the logs')

    async def test_an_api_key_is_stored_used_and_shown_only_by_fingerprint(self):
        described = await self.service.save_api_key(OWNER, 'claude', f'  {FIXTURE_API_KEY}\n')
        self.assertEqual(described['mode'], CREDENTIAL_API_KEY)
        self.assertNotIn(FIXTURE_API_KEY, json.dumps(described))
        self.assertEqual(described['stored']['api_key']['fingerprint'], '…AbCd')
        self.assertEqual(self.config.values['subscriptions.claude.credential'], CREDENTIAL_API_KEY)
        settings = await self.service.get_settings(OWNER, 'claude')
        tracked = await self.service._tracked(self.ref('claude'), settings)
        self.assertEqual(tracked.credential_env, {'ANTHROPIC_API_KEY': FIXTURE_API_KEY})
        self.assertEqual(self.claude.credential_mode, CREDENTIAL_API_KEY)

    async def test_the_claude_token_must_look_like_one_and_lasts_a_year(self):
        with self.assertRaisesRegex(SubscriptionError, 'not a Claude token'):
            await self.service.save_plan_token(OWNER, FIXTURE_API_KEY)
        described = await self.service.save_plan_token(OWNER, FIXTURE_TOKEN)
        expires_at = described['stored']['plan_token']['expires_at']
        self.assertAlmostEqual(expires_at, time.time() + 365 * 86400, delta=60)
        self.assertFalse(described['stored']['plan_token']['expires_soon'])
        tracked = await self.service._tracked(self.ref('claude'), await self.service.get_settings(OWNER, 'claude'))
        self.assertEqual(tracked.credential_env, {'CLAUDE_CODE_OAUTH_TOKEN': FIXTURE_TOKEN})

    async def test_the_cli_receives_the_sign_in_with_its_own_variables_on_top(self):
        machine = RecordingMachine()
        tracked = self.service._tracking_machine(self.ref('claude'), machine, {'ANTHROPIC_API_KEY': FIXTURE_API_KEY})
        await tracked.start_process(['claude'], '.', extra_env={'BROWSER': 'none'})
        self.assertEqual(machine.started, [{'ANTHROPIC_API_KEY': FIXTURE_API_KEY, 'BROWSER': 'none'}])

    async def test_codex_gets_its_key_through_the_provider_not_the_environment(self):
        await self.service.save_api_key(OWNER, 'codex', FIXTURE_OPENAI_KEY)
        tracked = await self.service._tracked(self.ref('codex'), await self.service.get_settings(OWNER, 'codex'))
        self.assertEqual(tracked.credential_env, {})
        self.assertEqual((self.codex.credential_mode, self.codex.api_key), (CREDENTIAL_API_KEY, FIXTURE_OPENAI_KEY))

    async def test_removing_the_key_goes_back_to_the_computers_sign_in(self):
        await self.service.save_api_key(OWNER, 'claude', FIXTURE_API_KEY)
        described = await self.service.delete_credential(OWNER, 'claude', 'api_key')
        self.assertEqual(described, {'mode': CREDENTIAL_MACHINE, 'stored': {}})
        tracked = await self.service._tracked(self.ref('claude'), await self.service.get_settings(OWNER, 'claude'))
        self.assertEqual(tracked.credential_env, {})

    async def test_a_stored_key_can_be_used_again_without_pasting_it(self):
        await self.service.save_api_key(OWNER, 'claude', FIXTURE_API_KEY)
        await self.service.use_machine_sign_in(OWNER, 'claude')
        described = await self.service.use_stored_credential(OWNER, 'claude', 'api_key')
        self.assertEqual(described['mode'], CREDENTIAL_API_KEY)
        with self.assertRaisesRegex(SubscriptionError, 'Nothing is stored'):
            await self.service.use_stored_credential(OWNER, 'claude', 'plan_token')
        with self.assertRaisesRegex(SubscriptionError, 'Unknown kind'):
            await self.service.use_stored_credential(OWNER, 'codex', 'plan_token')

    async def test_signing_out_of_a_stored_key_never_signs_the_computer_out(self):
        await self.service.save_api_key(OWNER, 'claude', FIXTURE_API_KEY)
        # FakeProvider has no logout; calling it would fail this test.
        await self.service.logout(OWNER, 'claude')
        self.assertEqual(await self.service.SubscriptionCredentials.secret(OWNER, 'claude', 'api_key'), None)
        self.assertEqual(self.config.values['subscriptions.claude.credential'], CREDENTIAL_MACHINE)

    async def test_the_policy_decides_which_sign_ins_may_be_used(self):
        await self.service.save_policy({'plan_sign_in': False})
        settings = await self.service.get_settings(OWNER, 'claude')
        with self.assertRaisesRegex(SubscriptionError, 'plan is turned off'):
            await self.service._tracked(self.ref('claude'), settings)
        with self.assertRaisesRegex(SubscriptionError, 'turned off'):
            await self.service.save_plan_token(OWNER, FIXTURE_TOKEN)
        await self.service.save_api_key(OWNER, 'claude', FIXTURE_API_KEY)
        await self.service._tracked(self.ref('claude'), settings)
        await self.service.save_policy({'plan_sign_in': True, 'api_keys': False})
        with self.assertRaisesRegex(SubscriptionError, 'API keys are turned off'):
            await self.service._tracked(self.ref('claude'), settings)

    async def test_model_tags_name_the_sign_in(self):
        model = types.SimpleNamespace(key='opus', value='opus', name='Opus', description='', efforts=[])
        self.assertEqual(self.service._model_entry('claude', model)['tags'], [{'name': 'Claude plan'}])
        self.assertEqual(self.service._model_entry('claude', model, CREDENTIAL_API_KEY)['tags'],
                         [{'name': 'Anthropic API key'}])
        self.assertEqual(self.service._model_entry('codex', model, CREDENTIAL_API_KEY)['tags'],
                         [{'name': 'OpenAI API key'}])


if __name__ == '__main__':
    unittest.main()
