"""How the Claude and Codex providers sign in with a stored token or the user's API key.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_credential_providers.py -v

Providers run against fake machines; nothing starts a real CLI.
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions import claude_code, codex  # noqa: E402
from open_webui.utils.subscriptions.common import (  # noqa: E402
    CREDENTIAL_API_KEY,
    CREDENTIAL_MACHINE,
    CREDENTIAL_PLAN_TOKEN,
    ProviderSettings,
)
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402

FIXTURE_OPENAI_KEY = 'sk-proj-fixture-openai-key-00000000000WxYz'


def claude_status(credential_mode, auth_method, logged_in=True):
    class FakeMachine:
        id = 'fake'
        name = 'Fake machine'

        async def find_tool(self, tool, configured_path=''):
            return 'claude'

        async def chat_dir(self):
            return '.'

        async def run(self, args, cwd, timeout, extra_env=None):
            if '--version' in args:
                return 0, '2.1.295 (Claude Code)', ''
            return 0, json.dumps({'loggedIn': logged_in, 'authMethod': auth_method}), ''

    with tempfile.TemporaryDirectory() as directory:
        provider = claude_code.ClaudeCodeProvider(Path(directory))
        provider.credential_mode = credential_mode
        return asyncio.run(provider.status(ProviderSettings(enable=True), FakeMachine()))


class ClaudeCredentialStatusTests(unittest.TestCase):
    def test_each_mode_accepts_only_its_own_sign_in(self):
        expectations = [
            (CREDENTIAL_MACHINE, 'claude.ai', 'plan'),
            (CREDENTIAL_PLAN_TOKEN, 'oauth_token', 'plan'),
            (CREDENTIAL_API_KEY, 'api_key', 'api_key'),
        ]
        for mode, auth_method, billing in expectations:
            with self.subTest(mode=mode):
                status = claude_status(mode, auth_method)
                self.assertNotIn('api_billing', status)
                self.assertEqual(status['billing'], billing)

    def test_a_different_sign_in_than_the_mode_expects_is_refused(self):
        mismatches = [
            (CREDENTIAL_MACHINE, 'oauth_token'),
            (CREDENTIAL_MACHINE, 'api_key'),
            (CREDENTIAL_PLAN_TOKEN, 'claude.ai'),
            (CREDENTIAL_PLAN_TOKEN, 'api_key'),
            (CREDENTIAL_API_KEY, 'claude.ai'),
            (CREDENTIAL_API_KEY, 'oauth_token'),
        ]
        for mode, auth_method in mismatches:
            with self.subTest(mode=mode, auth_method=auth_method):
                status = claude_status(mode, auth_method)
                self.assertTrue(status['api_billing'])
                self.assertNotIn('billing', status)

    def test_signing_in_on_the_computer_is_refused_while_a_stored_sign_in_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = claude_code.ClaudeCodeProvider(Path(directory))
            provider.credential_mode = CREDENTIAL_API_KEY
            with self.assertRaisesRegex(SubscriptionError, 'stored token or key'):
                asyncio.run(provider.start_login(ProviderSettings(enable=True), object(), 'browser'))


class FakeAppServerProcess:
    """Answers initialize and an API-key sign-in, and reports what was sent."""

    def __init__(self, account_type):
        self.account_type = account_type
        self.returncode = None
        self.sent = []
        self._lines = asyncio.Queue()

    def _reply(self, request_id, result):
        self._lines.put_nowait(json.dumps({'id': request_id, 'result': result}))

    async def write(self, text):
        message = json.loads(text)
        self.sent.append(message)
        method = message.get('method')
        if method == 'initialize':
            self._reply(message['id'], {})
        elif method == 'account/login/start':
            self._reply(message['id'], {'type': 'apiKey'})
        elif method == 'account/read':
            self._reply(message['id'], {'account': {'type': self.account_type}})
        elif method == 'account/rateLimits/read':
            self._reply(message['id'], {'rateLimits': None})

    async def read_line(self):
        line = await self._lines.get()
        if line is None:
            return None
        return line

    def stderr_text(self):
        return ''

    def close_stdin(self):
        pass

    def kill(self):
        self.returncode = 1
        self._lines.put_nowait(None)


class FakeCodexMachine:
    id = 'fake'
    name = 'Fake machine'

    def __init__(self, account_type):
        self.account_type = account_type
        self.started = []
        self.processes = []

    async def find_tool(self, tool, configured_path=''):
        return 'codex'

    async def chat_dir(self):
        return '.'

    async def start_process(self, args, cwd, extra_env=None, temp_files=None):
        self.started.append((args, extra_env))
        process = FakeAppServerProcess(self.account_type)
        self.processes.append(process)
        return process


class CodexApiKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_key_signs_the_app_server_in_from_memory_only(self):
        provider = codex.CodexProvider()
        provider.use_credential(CREDENTIAL_API_KEY, FIXTURE_OPENAI_KEY)
        machine = FakeCodexMachine('apiKey')
        status = await provider.status(ProviderSettings(enable=True), machine)
        args, extra_env = machine.started[0]
        self.assertIn('cli_auth_credentials_store="ephemeral"', args)
        self.assertNotIn(FIXTURE_OPENAI_KEY, ' '.join(args))
        self.assertFalse(extra_env)
        login = [message for message in machine.processes[0].sent if message.get('method') == 'account/login/start']
        self.assertEqual(login[0]['params'], {'type': 'apiKey', 'apiKey': FIXTURE_OPENAI_KEY})
        self.assertEqual(status['billing'], 'api_key')
        self.assertNotIn('api_billing', status)
        provider.stop()

    async def test_without_a_key_an_api_key_account_is_still_refused(self):
        provider = codex.CodexProvider()
        machine = FakeCodexMachine('apiKey')
        status = await provider.status(ProviderSettings(enable=True), machine)
        self.assertTrue(status['api_billing'])
        self.assertNotIn('cli_auth_credentials_store="ephemeral"', machine.started[0][0])
        provider.stop()

    async def test_key_mode_refuses_a_plan_account_and_computer_sign_in(self):
        provider = codex.CodexProvider()
        provider.use_credential(CREDENTIAL_API_KEY, FIXTURE_OPENAI_KEY)
        status = await provider.status(ProviderSettings(enable=True), FakeCodexMachine('chatgpt'))
        self.assertTrue(status['api_billing'])
        with self.assertRaisesRegex(SubscriptionError, 'API key'):
            await provider.start_login(ProviderSettings(enable=True), FakeCodexMachine('chatgpt'), 'device')
        provider.stop()

    async def test_stopping_one_computer_keeps_the_other_computers_app_servers(self):
        provider = codex.CodexProvider()
        laptop = FakeCodexMachine('chatgpt')
        laptop.id = 'laptop'
        desktop = FakeCodexMachine('chatgpt')
        desktop.id = 'desktop'
        await provider.status(ProviderSettings(enable=True), laptop)
        await provider.status(ProviderSettings(enable=True), desktop)
        provider.stop_machine('laptop')
        self.assertEqual(laptop.processes[0].returncode, 1)
        self.assertIsNone(desktop.processes[0].returncode)
        self.assertEqual(list(provider._servers), ['desktop'])
        provider.stop()

    def test_changing_the_credential_restarts_codex_and_repeating_it_does_not(self):
        provider = codex.CodexProvider()
        stops = []
        provider.stop = lambda: stops.append(True)
        provider.use_credential(CREDENTIAL_API_KEY, FIXTURE_OPENAI_KEY)
        provider.use_credential(CREDENTIAL_API_KEY, FIXTURE_OPENAI_KEY)
        provider.use_credential(CREDENTIAL_MACHINE, None)
        self.assertEqual(len(stops), 2)


if __name__ == '__main__':
    unittest.main()
