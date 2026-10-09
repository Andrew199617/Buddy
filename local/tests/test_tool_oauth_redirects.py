"""Offline tool OAuth callback regressions; no application DB or provider traffic.

Run with the repository virtualenv:
    .venv\\Scripts\\python.exe -m unittest discover -s local/tests -p test_tool_oauth_redirects.py -v
"""

import ast
import asyncio
import base64
import copy
import hashlib
import json
import logging
import os
import re
import sys
import unittest
import urllib
import urllib.parse
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Optional
from unittest.mock import Mock, patch

import httpx
from authlib.integrations.starlette_client import OAuth
from authlib.oauth2.rfc6749.errors import OAuth2Error
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from itsdangerous import TimestampSigner
from mcp.shared.auth import OAuthClientMetadata as MCPOAuthClientMetadata
from mcp.shared.auth import OAuthMetadata
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

ROOT_DIR = Path(__file__).resolve().parents[2]
OAUTH_SOURCE = ROOT_DIR / 'backend' / 'open_webui' / 'utils' / 'oauth.py'
CLIENT_KEY = 'mcp:offline-slack'
PROVIDER_ORIGIN = 'https://provider.example.invalid'
TOOL_URL = 'https://tools.example.invalid/mcp'


class FakeConfig:
    def __init__(self):
        self.values = {'webui.url': 'http://localhost:8080', 'tool_server.connections': []}

    async def get(self, key, default=None):
        return self.values.get(key, default)

    async def get_many(self, *keys):
        return {key: self.values[key] for key in keys if key in self.values}


class FakeSessions:
    def __init__(self):
        self.active = []
        self.created = []

    async def create_session(self, user_id, provider, token):
        session = SimpleNamespace(
            id=f'offline-session-{len(self.created) + 1}',
            user_id=user_id,
            provider=provider,
            token=copy.deepcopy(token),
            expires_at=token.get('expires_at'),
        )
        self.active.append(session)
        self.created.append(session)
        return session

    async def get_sessions_by_user_id(self, user_id):
        return [session for session in self.active if session.user_id == user_id]

    async def delete_session_by_id(self, session_id):
        self.active = [session for session in self.active if session.id != session_id]

    async def get_session_by_provider_and_user_id(self, provider, user_id):
        return next(
            (session for session in self.active if session.provider == provider and session.user_id == user_id), None
        )

    async def update_session_by_id(self, session_id, token):
        session = next(session for session in self.active if session.id == session_id)
        session.token = copy.deepcopy(token)
        session.expires_at = token.get('expires_at')
        return session


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status = status_code
        self.headers = {'content-type': 'application/json'}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return copy.deepcopy(self.data)

    async def text(self):
        return json.dumps(self.data)


class FakeProvider:
    def __init__(self):
        self.registrations = []
        self.token_forms = []
        self.refresh_forms = []
        self.returned_redirect_uris = None
        self.metadata = {
            'issuer': PROVIDER_ORIGIN,
            'authorization_endpoint': PROVIDER_ORIGIN + '/authorize',
            'token_endpoint': PROVIDER_ORIGIN + '/token',
            'registration_endpoint': PROVIDER_ORIGIN + '/register',
            'scopes_supported': ['tools:read'],
            'token_endpoint_auth_methods_supported': ['client_secret_post'],
            'code_challenge_methods_supported': ['S256'],
        }
        self.transport = httpx.MockTransport(self.handle_httpx)

    def metadata_response(self, url):
        if not str(url).startswith(PROVIDER_ORIGIN + '/'):
            raise AssertionError('Unexpected fake discovery origin')
        return FakeResponse(self.metadata)

    def register(self, data):
        self.registrations.append(copy.deepcopy(data))
        number = len(self.registrations)
        response = {
            **data,
            'client_id': f'offline-client-{number}',
            'client_secret': f'offline-secret-{number}',
        }
        if self.returned_redirect_uris is not None:
            response['redirect_uris'] = self.returned_redirect_uris
        return FakeResponse(response)

    def handle_httpx(self, request):
        if request.url.host != 'provider.example.invalid':
            raise AssertionError('OAuth request escaped the offline provider transport')
        if request.method == 'GET':
            return httpx.Response(200, json=self.metadata)
        form = urllib.parse.parse_qs(request.content.decode())
        self.token_forms.append(form)
        client_id = form.get('client_id', [''])[0]
        registered_index = int(client_id.rsplit('-', 1)[-1]) - 1
        registered = self.registrations[registered_index]
        if form.get('redirect_uri') != registered['redirect_uris']:
            return httpx.Response(
                400,
                json={
                    'error': 'invalid_grant',
                    'error_description': 'Offline provider requires the registered redirect URI',
                },
            )
        return httpx.Response(
            200,
            json={
                'access_token': 'offline-access-token',
                'refresh_token': 'offline-refresh-token',
                'token_type': 'Bearer',
                'expires_in': 3600,
            },
        )

    def refresh(self, data):
        self.refresh_forms.append(copy.deepcopy(data))
        return FakeResponse({'access_token': 'offline-refreshed-token', 'token_type': 'Bearer', 'expires_in': 3600})

    def client_session(self, *args, **kwargs):
        provider = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def get(self, url, **request_kwargs):
                return provider.metadata_response(url)

            def post(self, url, json=None, data=None, **request_kwargs):
                if str(url) == PROVIDER_ORIGIN + '/register':
                    return provider.register(json)
                if str(url) == PROVIDER_ORIGIN + '/token':
                    return provider.refresh(data)
                raise AssertionError('Unexpected offline provider POST')

        return Session()


class OfflineOAuth(OAuth):
    def __init__(self, provider):
        super().__init__()
        self.provider = provider

    def register(self, name, **kwargs):
        client_kwargs = dict(kwargs.get('client_kwargs') or {})
        client_kwargs['transport'] = self.provider.transport
        kwargs['client_kwargs'] = client_kwargs
        return super().register(name=name, **kwargs)


def load_oauth_source(config, sessions, provider):
    """Load source definitions, never the module's live imports or initialization."""
    tree = ast.parse(OAUTH_SOURCE.read_text(encoding='utf-8'))
    definitions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    wanted = {
        'OAuthClientMetadata',
        'OAuthClientInformationFull',
        'OAuthClientManager',
        'encrypt_data',
        'get_oauth_client_info_with_dynamic_client_registration',
        'get_oauth_client_info_with_static_credentials',
    }
    for name in ('resolve_mcp_oauth_base_url', 'get_mcp_oauth_callback_uri'):
        if name in definitions:
            wanted.add(name)
    pending = list(wanted)
    while pending:
        name = pending.pop()
        dependencies = {
            item.id
            for item in ast.walk(definitions[name])
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
        for dependency in dependencies.intersection(definitions):
            if dependency not in wanted:
                wanted.add(dependency)
                pending.append(dependency)
    source_nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in wanted:
            source_nodes.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            try:
                ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            source_nodes.append(node)

    async def get_resource_metadata(url):
        return SimpleNamespace(
            resource=TOOL_URL,
            scopes_supported=['tools:read'],
            get_discovery_urls=lambda server: [PROVIDER_ORIGIN + '/.well-known/oauth-authorization-server'],
        )

    async def verified_user(user_id):
        return SimpleNamespace(id=user_id)

    async def optional_user(request):
        return None

    async def runtime_config():
        return SimpleNamespace(OAUTH_REFRESH_TOKEN_INCLUDE_SCOPE=False)

    fake_aiohttp = SimpleNamespace(
        ClientSession=provider.client_session,
        ClientTimeout=lambda **kwargs: SimpleNamespace(**kwargs),
        ClientError=type('OfflineClientError', (Exception,), {}),
        ClientResponseError=type('OfflineResponseError', (Exception,), {}),
    )
    namespace = {
        '__name__': 'offline_tool_oauth_source',
        'asyncio': asyncio,
        'base64': base64,
        'hashlib': hashlib,
        'logging': logging,
        'os': os,
        're': re,
        'sys': sys,
        'urllib': urllib,
        'uuid': uuid,
        'zlib': zlib,
        'dataclass': dataclass,
        'field': field,
        'datetime': datetime,
        'timedelta': timedelta,
        'SimpleNamespace': SimpleNamespace,
        'Literal': Literal,
        'Optional': Optional,
        'OAuth': lambda: OfflineOAuth(provider),
        'OAuth2Error': OAuth2Error,
        'MCPOAuthClientMetadata': MCPOAuthClientMetadata,
        'OAuthMetadata': OAuthMetadata,
        'OAuthResourceParameterMode': Literal['auto', 'include', 'omit'],
        'Fernet': Fernet,
        'InvalidToken': InvalidToken,
        'FERNET': Fernet(Fernet.generate_key()),
        'HTTPException': HTTPException,
        'status': status,
        'Request': Request,
        'RedirectResponse': RedirectResponse,
        'Config': config,
        'OAuthSessions': sessions,
        'JSONCodec': json,
        'aiohttp': fake_aiohttp,
        'log': Mock(),
        'AIOHTTP_CLIENT_SESSION_SSL': False,
        'AIOHTTP_CLIENT_ALLOW_REDIRECTS': False,
        'OAUTH_RUNTIME_CONFIG': {},
        'OAUTH_CLIENT_TIMEOUT': 5,
    }
    isolated = ast.Module(body=source_nodes, type_ignores=[])
    exec(compile(isolated, str(OAUTH_SOURCE), 'exec'), namespace)
    namespace['FERNET'] = Fernet(Fernet.generate_key())
    namespace['get_protected_resource_metadata'] = get_resource_metadata
    namespace['get_verified_user_by_id'] = verified_user
    namespace['get_optional_verified_user_from_request'] = optional_user
    namespace['get_oauth_runtime_config'] = runtime_config
    return namespace


def make_request(origin, session=None, path='/oauth/clients/' + CLIENT_KEY + '/authorize', query=None, headers=None):
    parts = urllib.parse.urlsplit(origin)
    request_headers = [(b'host', parts.netloc.encode())]
    for name, value in (headers or {}).items():
        request_headers.append((name.lower().encode(), value.encode()))
    if session is None:
        session = {}
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': path,
        'raw_path': path.encode(),
        'root_path': '',
        'query_string': urllib.parse.urlencode(query or {}).encode(),
        'scheme': parts.scheme,
        'server': (parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)),
        'headers': request_headers,
        'session': session,
        'app': SimpleNamespace(state=SimpleNamespace(WEBUI_NAME='Buddy')),
    }
    return Request(scope)


class ToolOAuthRedirectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environ = patch.dict(
            os.environ,
            {'MCP_OAUTH_ALLOWED_REDIRECT_HOSTS': '100.122.80.32,sd-anvelez-03.tail83dea0.ts.net'},
            clear=True,
        )
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.config = FakeConfig()
        self.sessions = FakeSessions()
        self.provider = FakeProvider()
        self.source = load_oauth_source(self.config, self.sessions, self.provider)
        self.manager = self.source['OAuthClientManager'](SimpleNamespace())

    async def register(self, origin):
        client_info = await self.source['get_oauth_client_info_with_dynamic_client_registration'](
            make_request(origin), CLIENT_KEY, TOOL_URL
        )
        self.config.values['tool_server.connections'] = [
            {
                'type': 'mcp',
                'url': TOOL_URL,
                'auth_type': 'oauth_2.1',
                'info': {
                    'id': 'offline-slack',
                    'oauth_client_info': self.source['encrypt_data'](client_info.model_dump(mode='json')),
                },
            }
        ]
        return client_info

    async def authorize(self, origin, browser_session):
        response = await self.manager.handle_authorize(
            make_request(origin, browser_session), CLIENT_KEY, 'offline-user'
        )
        self.assertEqual(response.status_code, 302)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(response.headers['location']).query)
        self.assertIn('state', params)
        self.assertIn('code_challenge', params)
        self.assertEqual(params['code_challenge_method'], ['S256'])
        return params

    async def callback(self, origin, browser_session, params):
        request = make_request(
            origin,
            browser_session,
            path='/oauth/clients/' + CLIENT_KEY + '/callback',
            query={'state': params['state'][0], 'code': 'offline-code'},
        )
        return await self.manager.handle_callback(request, CLIENT_KEY, Response())

    async def assert_round_trip(self, origin):
        client_info = await self.register(origin)
        expected_uri = origin + '/oauth/clients/' + CLIENT_KEY + '/callback'
        self.assertEqual([str(uri) for uri in client_info.redirect_uris], [expected_uri])
        self.manager.add_client(CLIENT_KEY, client_info)
        browser_session = {}
        auth = await self.authorize(origin, browser_session)
        self.assertEqual(auth['redirect_uri'], [expected_uri])
        response = await self.callback(origin, browser_session, auth)
        self.assertEqual(response.headers['location'], origin)
        self.assertEqual(self.provider.token_forms[-1]['redirect_uri'], [expected_uri])
        self.assertTrue(self.provider.token_forms[-1].get('code_verifier'))
        self.assertEqual(len(self.sessions.created), 1)
        self.assertFalse(browser_session, 'Callback must consume both Authlib state and variant pointer')

    async def test_loopback_8081_registration_authorize_and_token_match(self):
        await self.assert_round_trip('http://127.0.0.1:8081')

    async def test_frontend_8082_registration_authorize_and_token_match(self):
        await self.assert_round_trip('http://127.0.0.1:8082')

    async def test_tailscale_host_8082_registration_authorize_and_token_match(self):
        await self.assert_round_trip('http://100.122.80.32:8082')

    async def test_tailscale_dns_https_preserves_browser_origin(self):
        await self.assert_round_trip('https://sd-anvelez-03.tail83dea0.ts.net')

    async def test_static_registration_preserves_request_origin(self):
        info = await self.source['get_oauth_client_info_with_static_credentials'](
            make_request('http://100.122.80.32:8081'),
            CLIENT_KEY,
            TOOL_URL,
            'offline-static-client',
            'offline-static-secret',
        )
        self.assertEqual(
            [str(uri) for uri in info.redirect_uris],
            ['http://100.122.80.32:8081/oauth/clients/' + CLIENT_KEY + '/callback'],
        )
        self.assertFalse(self.provider.registrations)

    async def test_untrusted_host_is_rejected_before_registration(self):
        with self.assertRaises(HTTPException):
            await self.register('https://untrusted.example.invalid')
        self.assertFalse(self.provider.registrations)

    async def test_origin_query_and_forwarded_headers_cannot_override_host(self):
        request = make_request(
            'http://127.0.0.1:8082',
            query={'frontend_origin': 'https://untrusted.example.invalid'},
            headers={
                'Origin': 'https://untrusted.example.invalid',
                'Referer': 'https://untrusted.example.invalid/',
                'Forwarded': 'host=untrusted.example.invalid;proto=https',
                'X-Forwarded-Host': 'untrusted.example.invalid',
                'X-Forwarded-Proto': 'https',
            },
        )
        resolved = await self.source['resolve_mcp_oauth_base_url'](request)
        self.assertEqual(resolved, 'http://127.0.0.1:8082')

    async def test_exact_origin_allowlist_restricts_scheme_and_port(self):
        os.environ['MCP_OAUTH_ALLOWED_REDIRECT_ORIGINS'] = 'https://exact.example.invalid:9443'
        self.assertEqual(
            await self.source['resolve_mcp_oauth_base_url'](make_request('https://exact.example.invalid:9443')),
            'https://exact.example.invalid:9443',
        )
        with self.assertRaises(HTTPException):
            await self.source['resolve_mcp_oauth_base_url'](make_request('https://exact.example.invalid:9444'))
        with self.assertRaises(HTTPException):
            await self.source['resolve_mcp_oauth_base_url'](make_request('http://exact.example.invalid:9443'))

    async def test_concurrent_origins_keep_their_registered_clients_and_states(self):
        base = await self.register('http://127.0.0.1:8081')
        self.manager.add_client(CLIENT_KEY, base)
        browser_session = {}
        frontend_auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        phone_auth = await self.authorize('http://100.122.80.32:8082', browser_session)
        self.assertNotEqual(frontend_auth['client_id'], phone_auth['client_id'])
        phone_response = await self.callback('http://100.122.80.32:8082', browser_session, phone_auth)
        frontend_response = await self.callback('http://127.0.0.1:8082', browser_session, frontend_auth)
        self.assertEqual(phone_response.headers['location'], 'http://100.122.80.32:8082')
        self.assertEqual(frontend_response.headers['location'], 'http://127.0.0.1:8082')
        self.assertEqual(self.provider.token_forms[0]['client_id'], phone_auth['client_id'])
        self.assertEqual(self.provider.token_forms[1]['client_id'], frontend_auth['client_id'])
        self.assertEqual((await self.manager.get_client_info(CLIENT_KEY)).client_id, base.client_id)

    async def test_changed_callback_origin_cannot_exchange_tokens(self):
        info = await self.register('http://127.0.0.1:8081')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8081', browser_session)
        response = await self.callback('http://127.0.0.1:8082', browser_session, auth)
        self.assertIn('error=', response.headers['location'])
        self.assertFalse(self.provider.token_forms)
        valid_response = await self.callback('http://127.0.0.1:8081', browser_session, auth)
        self.assertEqual(valid_response.headers['location'], 'http://127.0.0.1:8081')
        self.assertEqual(len(self.provider.token_forms), 1)

    async def test_callback_recovers_variant_after_manager_restart(self):
        info = await self.register('http://127.0.0.1:8081')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://100.122.80.32:8082', browser_session)
        self.manager = self.source['OAuthClientManager'](SimpleNamespace())
        response = await self.callback('http://100.122.80.32:8082', browser_session, auth)
        self.assertEqual(response.headers['location'], 'http://100.122.80.32:8082')
        self.assertEqual(self.provider.token_forms[-1]['client_id'], auth['client_id'])

    async def test_refresh_recovers_the_original_variant_credentials(self):
        info = await self.register('http://127.0.0.1:8081')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://100.122.80.32:8082', browser_session)
        await self.callback('http://100.122.80.32:8082', browser_session, auth)
        self.manager = self.source['OAuthClientManager'](SimpleNamespace())
        refreshed = await self.manager._perform_token_refresh(self.sessions.created[-1])
        self.assertIsNotNone(refreshed)
        self.assertEqual([self.provider.refresh_forms[-1]['client_id']], auth['client_id'])
        self.assertEqual(
            self.provider.refresh_forms[-1]['client_secret'], self.provider.token_forms[-1]['client_secret'][0]
        )

    async def test_replay_cannot_exchange_a_second_token(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        await self.callback('http://127.0.0.1:8082', browser_session, auth)
        replay = await self.callback('http://127.0.0.1:8082', browser_session, auth)
        self.assertIn('error=', replay.headers['location'])
        self.assertEqual(len(self.provider.token_forms), 1)
        self.assertEqual(len(self.sessions.created), 1)

    async def test_expired_state_is_consumed_without_token_exchange(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        state = auth['state'][0]
        pointer_key = self.source['oauth_client_state_key'](CLIENT_KEY, state)
        pointer = browser_session[pointer_key]
        framework = self.manager.oauth.framework_integration_cls(pointer['client_name'])
        pointer['expires_at'] = 0
        response = await self.callback('http://127.0.0.1:8082', browser_session, auth)
        self.assertIn('error=', response.headers['location'])
        self.assertFalse(self.provider.token_forms)
        self.assertNotIn(pointer_key, browser_session)
        self.assertIsNone(await framework.get_state_data(browser_session, state))

    async def test_callback_cannot_link_token_to_a_different_logged_in_user(self):
        async def different_user(request):
            return SimpleNamespace(id='another-offline-user')

        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        self.source['get_optional_verified_user_from_request'] = different_user
        response = await self.callback('http://127.0.0.1:8082', browser_session, auth)
        self.assertIn('error=', response.headers['location'])
        self.assertFalse(self.provider.token_forms)
        self.assertFalse(self.sessions.created)
        self.assertFalse(browser_session)

    async def test_new_same_origin_flow_invalidates_the_previous_state(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        first = await self.authorize('http://127.0.0.1:8082', browser_session)
        second = await self.authorize('http://127.0.0.1:8082', browser_session)
        self.assertNotEqual(first['state'], second['state'])
        second_response = await self.callback('http://127.0.0.1:8082', browser_session, second)
        first_response = await self.callback('http://127.0.0.1:8082', browser_session, first)
        self.assertEqual(second_response.headers['location'], 'http://127.0.0.1:8082')
        self.assertIn('error=', first_response.headers['location'])
        self.assertEqual(len(self.provider.registrations), 1)
        self.assertEqual(len(self.provider.token_forms), 1)
        self.assertFalse(browser_session)

    async def test_provider_cannot_substitute_a_different_callback(self):
        self.provider.returned_redirect_uris = ['https://other-app.example.invalid/callback']
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        with self.assertRaises(HTTPException):
            await self.authorize('http://127.0.0.1:8082', {})
        self.assertFalse(self.provider.token_forms)

    async def test_provider_multi_uri_response_selects_this_exact_app(self):
        origin = 'http://127.0.0.1:8082'
        expected = origin + '/oauth/clients/' + CLIENT_KEY + '/callback'
        self.provider.returned_redirect_uris = ['https://other-app.example.invalid/callback', expected]
        info = await self.register(origin)
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize(origin, browser_session)
        self.assertEqual(auth['redirect_uri'], [expected])
        response = await self.callback(origin, browser_session, auth)
        self.assertEqual(response.headers['location'], origin)
        self.assertEqual(self.provider.token_forms[-1]['redirect_uri'], [expected])

    async def test_browser_state_never_contains_plaintext_client_secret(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        self.assertNotIn(info.client_secret, json.dumps(browser_session))
        await self.callback('http://127.0.0.1:8082', browser_session, auth)
        snapshot_key = self.source['MCP_OAUTH_CLIENT_SNAPSHOT_KEY']
        snapshot = self.sessions.created[-1].token[snapshot_key]
        self.assertNotIn(info.client_secret, snapshot)
        decrypted = self.source['decode_oauth_client_state_snapshot'](snapshot)
        self.assertEqual(decrypted['client_secret'], info.client_secret)

    async def test_canonical_config_host_can_use_a_different_frontend_port(self):
        self.config.values['webui.url'] = 'https://canonical.example.invalid:8081'
        actual = 'https://canonical.example.invalid:8082'
        resolved = await self.source['resolve_mcp_oauth_base_url'](make_request(actual))
        self.assertEqual(resolved, actual)

    async def test_ipv6_loopback_and_default_https_port_are_normalized(self):
        resolved = await self.source['resolve_mcp_oauth_base_url'](make_request('http://[::1]:8082'))
        self.assertEqual(resolved, 'http://[::1]:8082')
        normalized = await self.source['resolve_mcp_oauth_base_url'](
            make_request('https://sd-anvelez-03.tail83dea0.ts.net:443')
        )
        self.assertEqual(normalized, 'https://sd-anvelez-03.tail83dea0.ts.net')

    async def assert_pending_cookie_bound(self, long_endpoints=False):
        if long_endpoints:
            self.provider.metadata['authorization_endpoint'] += '/' + 'tenant-path-' * 35
            self.provider.metadata['token_endpoint'] += '/' + 'token-tenant-path-' * 35
        info = await self.register('http://127.0.0.1:8081')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        await self.authorize('http://127.0.0.1:8081', browser_session)
        signer = TimestampSigner('offline-cookie-signing-key')
        single_cookie = signer.sign(base64.b64encode(json.dumps(browser_session).encode()))
        self.assertLessEqual(len(single_cookie), 3500)
        await self.authorize('http://127.0.0.1:8082', browser_session)
        dual_cookie = signer.sign(base64.b64encode(json.dumps(browser_session).encode()))
        self.assertLessEqual(len(dual_cookie), 3500)

    async def test_two_pending_ports_fit_a_browser_session_cookie(self):
        await self.assert_pending_cookie_bound()

    async def test_long_provider_endpoints_fit_a_browser_session_cookie(self):
        await self.assert_pending_cookie_bound(long_endpoints=True)

    async def test_legacy_snapshot_survives_callback_and_refresh(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        state_item = next(value for key, value in browser_session.items() if key.startswith('_state_'))
        snapshot_key = self.source['MCP_OAUTH_CLIENT_SNAPSHOT_KEY']
        state_item['data'][snapshot_key] = self.source['encrypt_data'](info.model_dump(mode='json'))
        self.manager = self.source['OAuthClientManager'](SimpleNamespace())
        response = await self.callback('http://127.0.0.1:8082', browser_session, auth)
        self.assertEqual(response.headers['location'], 'http://127.0.0.1:8082')
        self.manager = self.source['OAuthClientManager'](SimpleNamespace())
        refreshed = await self.manager._perform_token_refresh(self.sessions.created[-1])
        self.assertIsNotNone(refreshed)
        self.assertEqual(self.provider.refresh_forms[-1]['client_id'], info.client_id)

    async def test_decompressed_snapshot_size_is_bounded(self):
        oversized = json.dumps({'padding': 'x' * (16 * 1024 + 1)}).encode()
        encrypted = self.source['FERNET'].encrypt(zlib.compress(oversized)).decode()
        with self.assertRaises(ValueError):
            self.source['decode_oauth_client_state_snapshot']('z1:' + encrypted)

    async def test_compressed_snapshot_must_contain_a_json_object(self):
        encrypted = self.source['FERNET'].encrypt(zlib.compress(b'[]')).decode()
        with self.assertRaises(ValueError):
            self.source['decode_oauth_client_state_snapshot']('z1:' + encrypted)

    async def test_cancelling_8082_preserves_independent_8081_authorization(self):
        info = await self.register('http://127.0.0.1:8081')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        base_auth = await self.authorize('http://127.0.0.1:8081', browser_session)
        frontend_auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        config_before_cancel = copy.deepcopy(self.config.values)
        cancelled = await self.manager.cancel_authorization(
            make_request('http://127.0.0.1:8082', browser_session), CLIENT_KEY, 'offline-user'
        )
        self.assertEqual(cancelled, 1)
        self.assertFalse(self.provider.token_forms)
        self.assertFalse(self.sessions.created)
        self.assertEqual(self.config.values, config_before_cancel)
        frontend_response = await self.callback('http://127.0.0.1:8082', browser_session, frontend_auth)
        self.assertIn('error=', frontend_response.headers['location'])
        self.assertFalse(self.provider.token_forms)
        base_response = await self.callback('http://127.0.0.1:8081', browser_session, base_auth)
        self.assertEqual(base_response.headers['location'], 'http://127.0.0.1:8081')
        self.assertEqual(len(self.provider.token_forms), 1)
        self.assertEqual(self.provider.token_forms[0]['client_id'], base_auth['client_id'])

    async def test_another_user_cannot_cancel_pending_authorization(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        auth = await self.authorize('http://127.0.0.1:8082', browser_session)
        session_before_cancel = copy.deepcopy(browser_session)
        cancelled = await self.manager.cancel_authorization(
            make_request('http://127.0.0.1:8082', browser_session), CLIENT_KEY, 'another-offline-user'
        )
        self.assertEqual(cancelled, 0)
        self.assertEqual(browser_session, session_before_cancel)
        self.assertFalse(self.provider.token_forms)
        response = await self.callback('http://127.0.0.1:8082', browser_session, auth)
        self.assertEqual(response.headers['location'], 'http://127.0.0.1:8082')
        self.assertEqual(len(self.provider.token_forms), 1)

    async def test_cancel_clears_legacy_state_without_touching_another_app(self):
        info = await self.register('http://127.0.0.1:8082')
        self.manager.add_client(CLIENT_KEY, info)
        browser_session = {}
        legacy_client = await self.manager.get_client(CLIENT_KEY)
        redirect_uri = 'http://127.0.0.1:8082/oauth/clients/' + CLIENT_KEY + '/callback'
        auth_data = await legacy_client.create_authorization_url(redirect_uri)
        auth_data['user_id'] = 'offline-user'
        await legacy_client.save_authorize_data(
            make_request('http://127.0.0.1:8082', browser_session), redirect_uri=redirect_uri, **auth_data
        )
        other_auth = await self.authorize('http://127.0.0.1:8081', browser_session)
        cancelled = await self.manager.cancel_authorization(
            make_request('http://127.0.0.1:8082', browser_session), CLIENT_KEY, 'offline-user'
        )
        self.assertEqual(cancelled, 1)
        self.assertIsNone(await legacy_client.framework.get_state_data(browser_session, auth_data['state']))
        other_response = await self.callback('http://127.0.0.1:8081', browser_session, other_auth)
        self.assertEqual(other_response.headers['location'], 'http://127.0.0.1:8081')
        self.assertEqual(len(self.provider.token_forms), 1)


if __name__ == '__main__':
    unittest.main()
