"""Offline tests for local/. Run from the open-webui folder:

    .venv\\Scripts\\python.exe -m unittest discover -s local\\tests -v
"""

import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

LOCAL_DIR = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REASONING = load('reasoning_level', LOCAL_DIR / 'functions' / 'reasoning_level.py')
PATCHES = load('owui_local_patches_under_test', LOCAL_DIR / 'owui_local_patches.py')

CONNECTIONS = {0: {'api_type': 'responses'}, 1: {}}


async def fake_get_openai_connection(idx):
    return 'url', 'key', CONNECTIONS[idx]


def stub_modules(**modules):
    """Install fake open_webui submodules for the duration of a test."""
    return patch.dict(sys.modules, {name: types.SimpleNamespace(**attrs) for name, attrs in modules.items()})


class ReasoningLevelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.filter = REASONING.Filter()
        self.request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(MODELS={'gpt-6-luna': {'urlIdx': 0}}))
        )
        self.stubs = stub_modules(**{'open_webui.routers.openai': {'get_openai_connection': fake_get_openai_connection}})
        self.stubs.start()
        self.addCleanup(self.stubs.stop)

    async def run_inlet(self, body, model):
        return await self.filter.inlet(body, __model__=model, __request__=self.request)

    async def test_responses_gateway_gets_reasoning_object(self):
        body = await self.run_inlet(
            {'model': 'gpt-6-sol', 'reasoning_effort': 'xhigh'},
            {'id': 'gpt-6-sol', 'owned_by': 'openai', 'urlIdx': 0},
        )
        self.assertEqual(body, {'model': 'gpt-6-sol', 'reasoning': {'effort': 'xhigh'}})

    async def test_chosen_level_overrides_preset_custom_reasoning(self):
        # "luna" is a workspace preset over gpt-6-luna with custom_params reasoning {"effort":"max"}.
        body = await self.run_inlet(
            {'model': 'luna', 'reasoning_effort': 'low', 'reasoning': {'effort': 'max', 'summary': 'auto'}},
            {'id': 'luna', 'owned_by': 'openai', 'info': {'base_model_id': 'gpt-6-luna'}},
        )
        self.assertEqual(body['reasoning'], {'effort': 'low', 'summary': 'auto'})
        self.assertNotIn('reasoning_effort', body)

    async def test_preset_effort_kept_when_no_level_chosen(self):
        body = {'model': 'luna', 'reasoning': {'effort': 'max'}}
        await self.run_inlet(body, {'id': 'luna', 'owned_by': 'openai', 'info': {'base_model_id': 'gpt-6-luna'}})
        self.assertEqual(body, {'model': 'luna', 'reasoning': {'effort': 'max'}})

    async def test_chat_completions_servers_keep_reasoning_effort(self):
        body = await self.run_inlet(
            {'model': 'QwenMOE', 'reasoning_effort': 'none'},
            {'id': 'QwenMOE', 'owned_by': 'openai', 'urlIdx': 1},
        )
        self.assertEqual(body, {'model': 'QwenMOE', 'reasoning_effort': 'none'})

    async def test_non_openai_model_is_left_alone(self):
        body = await self.run_inlet({'model': 'x', 'reasoning_effort': 'high'}, {'id': 'x', 'owned_by': 'ollama'})
        self.assertEqual(body, {'model': 'x', 'reasoning_effort': 'high'})

    async def test_connection_lookup_failure_leaves_body_alone(self):
        async def broken(idx):
            raise RuntimeError('moved upstream')

        with stub_modules(**{'open_webui.routers.openai': {'get_openai_connection': broken}}):
            body = await self.run_inlet(
                {'model': 'gpt-6-sol', 'reasoning_effort': 'high'},
                {'id': 'gpt-6-sol', 'owned_by': 'openai', 'urlIdx': 0},
            )
        self.assertEqual(body, {'model': 'gpt-6-sol', 'reasoning_effort': 'high'})

    def test_is_an_always_on_filter(self):
        self.assertFalse(getattr(self.filter, 'toggle', False))
        self.assertFalse(hasattr(REASONING.Filter, 'UserValves'))


class WebLoaderTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.static = Path(tempfile.mkdtemp())
        self.loader = self.static / 'loader.js'

    def test_appends_scripts_after_upstream_loader(self):
        self.loader.write_text('// upstream hook\n', encoding='utf-8')
        PATCHES.add_web_scripts_to_loader(self.static)
        text = self.loader.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('// upstream hook'))
        self.assertIn(PATCHES.LOADER_MARKER, text)
        self.assertIn('owui-reasoning-chip', text)
        self.assertIn('owui-mobile-chat-layout-style', text)
        self.assertIn('owui-mobile-ui-polish-style', text)
        self.assertIn('owui-chat-usage-style', text)
        self.assertIn('owui-model-activity-style', text)
        self.assertIn('owui-chat-actions-style', text)
        self.assertIn('owui-chat-header-style', text)
        self.assertIn('owui-mobile-settings-style', text)
        self.assertIn('owui-model-edit-shortcut-style', text)

    def test_is_idempotent(self):
        self.loader.write_text('', encoding='utf-8')
        PATCHES.add_web_scripts_to_loader(self.static)
        once = self.loader.read_text(encoding='utf-8')
        PATCHES.add_web_scripts_to_loader(self.static)
        self.assertEqual(self.loader.read_text(encoding='utf-8'), once)
        self.assertEqual(once.count(PATCHES.LOADER_MARKER), 1)

    def test_missing_static_dir_is_skipped(self):
        with self.assertLogs('owui_local_patches', 'WARNING'):
            PATCHES.add_web_scripts_to_loader(self.static / 'missing')


class FakeSessions:
    def __init__(self, session):
        self.session = session
        self.deleted = False

    async def get_session_by_provider_and_user_id(self, provider, user_id):
        return None if self.deleted else self.session

    async def update_session_by_id(self, session_id, token):
        self.session = SimpleNamespace(**{**vars(self.session), 'token': token, 'expires_at': token['expires_at']})
        return self.session


class OAuthKeepAliveTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self):
        sessions = self.sessions

        class FakeOAuthClientManager:
            # Mirrors Open WebUI: no refresh token at expiry -> delete the session.
            async def get_oauth_token(self, user_id, client_id, force_refresh=False):
                session = await sessions.get_session_by_provider_and_user_id(client_id, user_id)
                if not session:
                    return None
                if force_refresh or time.time() + 300 >= session.expires_at:
                    if not session.token.get('refresh_token'):
                        sessions.deleted = True
                        return None
                return session.token

            async def get_client_info(self, client_id):
                return SimpleNamespace(resource=None)

        return FakeOAuthClientManager

    def setUp(self):
        now = time.time()
        self.sessions = FakeSessions(
            SimpleNamespace(
                id='s1',
                provider='mcp:12',
                created_at=int(now - 9 * 3600),
                expires_at=int(now - 60),
                token={'access_token': 'secret-token', 'issued_at': now - 9 * 3600, 'expires_at': int(now - 60)},
            )
        )
        self.Manager = self.make_manager()
        self.probe_calls = []

        async def config_get(key, default=None):
            return [{'url': 'https://mcp.example/dcr/mcp', 'type': 'mcp', 'info': {'id': '12'}}]

        self.stubs = stub_modules(
            **{
                'open_webui.utils.oauth': {'OAuthClientManager': self.Manager},
                'open_webui.models.oauth_sessions': {'OAuthSessions': self.sessions},
                'open_webui.models.config': {'Config': SimpleNamespace(get=config_get)},
            }
        )
        self.stubs.start()
        self.addCleanup(self.stubs.stop)
        PATCHES.keep_mcp_oauth_sessions_without_refresh_tokens()

    def probe_returns(self, status):
        async def probe(url, token):
            self.probe_calls.append((url, token))
            return status

        return patch.object(PATCHES, '_probe', probe)

    async def get_token(self, client_id='mcp:12'):
        return await self.Manager().get_oauth_token('user', client_id)

    async def test_accepted_token_is_kept_and_rechecked_later(self):
        with self.probe_returns(200):
            token = await self.get_token()
        self.assertEqual(token['access_token'], 'secret-token')
        self.assertFalse(self.sessions.deleted)
        self.assertEqual(self.probe_calls, [('https://mcp.example/dcr/mcp', 'secret-token')])
        self.assertAlmostEqual(self.sessions.session.expires_at, time.time() + 1800 + 300, delta=5)

        with self.probe_returns(200):
            await self.get_token()
        self.assertEqual(len(self.probe_calls), 1, 'should not re-probe before the recheck interval')

    async def test_rejected_token_falls_back_to_sign_in(self):
        with self.probe_returns(401):
            self.assertIsNone(await self.get_token())
        self.assertTrue(self.sessions.deleted)

    async def test_unreachable_server_keeps_session_and_retries_soon(self):
        with self.probe_returns(None):
            token = await self.get_token()
        self.assertEqual(token['access_token'], 'secret-token')
        self.assertFalse(self.sessions.deleted)
        self.assertAlmostEqual(self.sessions.session.expires_at, time.time() + 300 + 300, delta=5)

    async def test_unexpected_status_uses_default_behavior(self):
        with self.probe_returns(404):
            self.assertIsNone(await self.get_token())
        self.assertTrue(self.sessions.deleted)

    async def test_sessions_with_refresh_tokens_are_untouched(self):
        self.sessions.session.token['refresh_token'] = 'r'
        self.sessions.session.expires_at = int(time.time() + 3600)
        with self.probe_returns(200):
            await self.get_token()
        self.assertEqual(self.probe_calls, [])

    async def test_valid_token_is_not_probed(self):
        self.sessions.session.expires_at = int(time.time() + 3600)
        with self.probe_returns(200):
            self.assertEqual((await self.get_token())['access_token'], 'secret-token')
        self.assertEqual(self.probe_calls, [])

    async def test_patch_applies_once(self):
        patched = self.Manager.get_oauth_token
        PATCHES.keep_mcp_oauth_sessions_without_refresh_tokens()
        self.assertIs(self.Manager.get_oauth_token, patched)

    def test_skips_when_upstream_signature_changes(self):
        class Changed:
            async def get_oauth_token(self, user_id, provider):
                return None

        original = Changed.get_oauth_token
        with stub_modules(
            **{
                'open_webui.utils.oauth': {'OAuthClientManager': Changed},
                'open_webui.models.oauth_sessions': {'OAuthSessions': self.sessions},
            }
        ):
            with self.assertLogs('owui_local_patches', 'WARNING'):
                PATCHES.keep_mcp_oauth_sessions_without_refresh_tokens()
        self.assertIs(Changed.get_oauth_token, original)


class LauncherTests(unittest.TestCase):
    def test_patches_apply_before_uvicorn_starts(self):
        import uvicorn

        calls = []
        real_run = uvicorn.run
        fake_patches = types.SimpleNamespace(apply=lambda: calls.append('apply'))
        try:
            uvicorn.run = lambda *a, **k: calls.append(('run', a))
            with patch.dict(sys.modules, {'owui_local_patches': fake_patches}):
                load('local_serve_under_test', LOCAL_DIR / 'serve.py')
                uvicorn.run('open_webui.main:app')
        finally:
            uvicorn.run = real_run
        self.assertEqual(calls, ['apply', ('run', ('open_webui.main:app',))])


if __name__ == '__main__':
    unittest.main()
