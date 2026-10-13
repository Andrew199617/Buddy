"""Offline tests: Workspace preset rows cannot take over subscription models.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_subscription_preset_rows.py -v
"""

import __future__
import ast
import copy
import logging
import types
import unittest
from unittest.mock import AsyncMock

from fastapi import Depends, HTTPException, status
from test_subscription_machines import ROOT, MemoryConfig, load_service

BASE_ID = 'claude-code.fixture-opus'
OTHER_ID = 'claude-code.fixture-sonnet'
PRESET_ID = 'fixture-preset'
DEFAULT_ID = 'fixture-default-model'
API_MODEL_ID = 'fixture-anthropic-api-model'
API_URL = 'https://fixture-anthropic.invalid/v1'
MAIN_PATH = ROOT / 'backend' / 'open_webui' / 'main.py'
MISC_PATH = ROOT / 'backend' / 'open_webui' / 'utils' / 'misc.py'
OPENAI_ROUTER_PATH = ROOT / 'backend' / 'open_webui' / 'routers' / 'openai.py'


class ReachedChatSetup(BaseException):
    """Stops the chat route once it has settled the model and params, before any chat work."""


def load_functions(source_path, names, namespace):
    """Define only the named top-level functions of a source file, without decorators.

    The production function bodies run as written, but the module's imports
    do not, so the test never opens the application database.
    """
    syntax_tree = ast.parse(source_path.read_text(encoding='utf-8'))
    functions = []
    for node in syntax_tree.body:
        is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_function and node.name in names:
            node.decorator_list = []
            functions.append(node)
    module = ast.Module(body=functions, type_ignores=[])
    flags = __future__.annotations.compiler_flag
    exec(compile(module, str(source_path), 'exec', flags=flags, dont_inherit=True), namespace)


def preset_row(model_id, base_model_id, system):
    params = {'system': system, 'temperature': 0.2}
    return types.SimpleNamespace(
        id=model_id,
        base_model_id=base_model_id,
        user_id='fixture-member',
        params=types.SimpleNamespace(model_dump=lambda: dict(params)),
    )


def subscription_model(model_id, model):
    return {'id': model_id, 'owned_by': 'subscription', 'subscription': {'provider': 'claude', 'model': model}}


class SubscriptionPresetRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = load_service(MemoryConfig(), ROOT / 'unused-offline-preset-data')
        self.rows = {}
        self.system_prompts = []

        async def get_model_by_id(model_id):
            return self.rows.get(model_id)

        async def apply_system_prompt(system, payload, metadata, user):
            self.system_prompts.append(system)
            return payload

        def apply_params(params, payload):
            return {**payload, **params}

        self.service.Models = types.SimpleNamespace(get_model_by_id=get_model_by_id)
        self.service.apply_system_prompt_to_body = apply_system_prompt
        self.service.apply_model_params_to_body_openai = apply_params
        # The registry get_all_models builds: a row whose ID matches a base
        # model is skipped there, so the base subscription model is listed.
        self.models = {
            BASE_ID: subscription_model(BASE_ID, 'fixture-opus'),
            OTHER_ID: subscription_model(OTHER_ID, 'fixture-sonnet'),
            PRESET_ID: {'id': PRESET_ID, 'owned_by': 'subscription', 'preset': True},
        }

    async def apply(self, model_id):
        request = types.SimpleNamespace(state=types.SimpleNamespace())
        payload = {'model': model_id, 'messages': []}
        admin = types.SimpleNamespace(id='fixture-admin', role='admin')
        model_id, payload, _ = await self.service._apply_preset(request, payload, {}, admin, self.models)
        return model_id, payload

    async def test_a_row_reusing_a_subscription_model_id_cannot_redirect_or_instruct_it(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, OTHER_ID, 'Fixture injected instructions')
        model_id, payload = await self.apply(BASE_ID)
        self.assertEqual(model_id, BASE_ID)
        self.assertEqual(payload, {'model': BASE_ID, 'messages': []})
        self.assertEqual(self.system_prompts, [])

    async def test_a_row_reusing_the_id_with_a_missing_base_is_ignored_too(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, 'missing-fixture-model', 'Fixture injected instructions')
        model_id, payload = await self.apply(BASE_ID)
        self.assertEqual(model_id, BASE_ID)
        self.assertNotIn('temperature', payload)
        self.assertEqual(self.system_prompts, [])

    async def test_saved_settings_for_the_subscription_model_itself_still_apply(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, None, 'Fixture administrator instructions')
        model_id, payload = await self.apply(BASE_ID)
        self.assertEqual(model_id, BASE_ID)
        self.assertEqual(payload['temperature'], 0.2)
        self.assertEqual(self.system_prompts, ['Fixture administrator instructions'])

    async def test_a_listed_preset_still_runs_on_its_base_model(self):
        self.rows[PRESET_ID] = preset_row(PRESET_ID, BASE_ID, 'Fixture preset instructions')
        model_id, payload = await self.apply(PRESET_ID)
        self.assertEqual(model_id, BASE_ID)
        self.assertEqual(payload['temperature'], 0.2)
        self.assertEqual(self.system_prompts, ['Fixture preset instructions'])

    async def test_a_preset_written_by_someone_else_is_marked_to_run_chat_only(self):
        # A turn runs on the requesting user's own machine, so another user's
        # preset must not be able to steer tools there.
        self.rows[PRESET_ID] = preset_row(PRESET_ID, BASE_ID, 'Fixture preset instructions')
        request = types.SimpleNamespace(state=types.SimpleNamespace())
        author = types.SimpleNamespace(id='fixture-member', role='user')
        someone_else = types.SimpleNamespace(id='fixture-other', role='user')
        payload = {'model': PRESET_ID, 'messages': []}
        _, _, foreign = await self.service._apply_preset(request, dict(payload), {}, author, self.models)
        self.assertFalse(foreign)
        _, _, foreign = await self.service._apply_preset(request, dict(payload), {}, someone_else, self.models)
        self.assertTrue(foreign)

    def test_only_rows_with_a_base_model_conflict_with_a_listed_subscription_model(self):
        conflicting = self.service.is_conflicting_preset_row
        row_with_base = preset_row(BASE_ID, OTHER_ID, '')
        row_without_base = preset_row(BASE_ID, None, '')
        self.assertTrue(conflicting(self.models[BASE_ID], row_with_base))
        self.assertFalse(conflicting(self.models[BASE_ID], row_without_base))
        self.assertFalse(conflicting(self.models[PRESET_ID], row_with_base))
        self.assertFalse(conflicting({'id': 'fixture-openai', 'owned_by': 'openai'}, row_with_base))
        self.assertFalse(conflicting(None, row_with_base))
        self.assertFalse(conflicting(self.models[BASE_ID], None))


class ChatRoutePresetRowTests(unittest.IsolatedAsyncioTestCase):
    """The /api/chat/completions route settles the model and params before the subscription service runs."""

    def setUp(self):
        service = load_service(MemoryConfig(), ROOT / 'unused-offline-preset-data')
        self.rows = {}
        self.config = {}
        self.models = {
            BASE_ID: subscription_model(BASE_ID, 'fixture-opus'),
            DEFAULT_ID: {'id': DEFAULT_ID, 'owned_by': 'openai'},
        }

        async def get_model_by_id(model_id):
            return self.rows.get(model_id)

        async def get_config(key, default=None):
            return self.config.get(key, default)

        def stop_at_chat_setup(chat_variables):
            raise ReachedChatSetup()

        # Default access settings: an administrator's chat skips the model access check.
        self.route = {
            'Models': types.SimpleNamespace(get_model_by_id=get_model_by_id),
            'Config': types.SimpleNamespace(get=get_config),
            'is_conflicting_preset_row': service.is_conflicting_preset_row,
            'ENABLE_CUSTOM_MODEL_FALLBACK': False,
            'BYPASS_MODEL_ACCESS_CONTROL': False,
            'BYPASS_ADMIN_ACCESS_CONTROL': True,
            'get_all_models': AsyncMock(),
            'normalize_chat_variables': stop_at_chat_setup,
            'copy': copy,
            'Depends': Depends,
            'get_verified_user': None,
            'HTTPException': HTTPException,
            'status': status,
            'log': logging.getLogger(__name__),
        }
        load_functions(MISC_PATH, {'merge_model_params'}, self.route)
        load_functions(MAIN_PATH, {'chat_completion'}, self.route)

    async def settle_chat(self):
        """Run the route until the model and params are settled, and return the form it settled."""
        app = types.SimpleNamespace(state=types.SimpleNamespace(MODELS=self.models))
        request = types.SimpleNamespace(app=app, state=types.SimpleNamespace(), headers={})
        admin = types.SimpleNamespace(id='fixture-admin', role='admin')
        form_data = {'model': BASE_ID, 'messages': [], 'chat_variables': {}}
        with self.assertRaises(ReachedChatSetup):
            await self.route['chat_completion'](request, form_data, admin)
        return form_data

    async def test_a_row_reusing_the_id_with_a_missing_base_does_not_break_the_model(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, 'missing-fixture-model', 'Fixture injected instructions')
        form_data = await self.settle_chat()
        self.assertEqual(form_data['model'], BASE_ID)
        self.assertNotIn('system', form_data.get('params', {}))
        self.assertNotIn('temperature', form_data.get('params', {}))

    async def test_a_row_reusing_the_id_does_not_switch_to_the_fallback_model(self):
        self.route['ENABLE_CUSTOM_MODEL_FALLBACK'] = True
        self.config['ui.default_models'] = DEFAULT_ID
        self.rows[BASE_ID] = preset_row(BASE_ID, 'missing-fixture-model', 'Fixture injected instructions')
        form_data = await self.settle_chat()
        self.assertEqual(form_data['model'], BASE_ID)
        self.assertNotIn('system', form_data.get('params', {}))

    async def test_the_subscription_models_own_saved_settings_still_apply(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, None, 'Fixture administrator instructions')
        form_data = await self.settle_chat()
        self.assertEqual(form_data['model'], BASE_ID)
        self.assertEqual(form_data['params']['system'], 'Fixture administrator instructions')
        self.assertEqual(form_data['params']['temperature'], 0.2)


class MessagesRoutePresetRowTests(unittest.IsolatedAsyncioTestCase):
    """The Anthropic /api/v1/messages route cannot pass a subscription model's turn to an API connection."""

    def setUp(self):
        service = load_service(MemoryConfig(), ROOT / 'unused-offline-preset-data')
        self.rows = {}
        self.calls = []
        self.listed_models = {
            BASE_ID: subscription_model(BASE_ID, 'fixture-opus'),
            PRESET_ID: {'id': PRESET_ID, 'owned_by': 'openai', 'preset': True},
            API_MODEL_ID: {'id': API_MODEL_ID, 'owned_by': 'openai'},
        }
        self.models = dict(self.listed_models)
        self.openai_models = {API_MODEL_ID: {'id': API_MODEL_ID, 'urlIdx': 0}}

        async def get_model_by_id(model_id):
            return self.rows.get(model_id)

        async def load_all_models(request, user=None):
            self.calls.append(('load_models',))
            self.models.update(self.listed_models)

        async def check_model_access(user, model_info, bypass_filter=False):
            return None

        async def get_openai_connection(url_index):
            return API_URL, 'fixture-key', {}

        async def get_headers_and_cookies(request, url, key, api_config, user=None):
            return {}, {}

        self.router = {
            'Models': types.SimpleNamespace(get_model_by_id=get_model_by_id),
            'check_model_access': check_model_access,
            'BYPASS_MODEL_ACCESS_CONTROL': False,
            'is_conflicting_preset_row': service.is_conflicting_preset_row,
            'get_all_models': AsyncMock(),
            'get_openai_connection': get_openai_connection,
            'strip_provider_model_prefix': lambda model_id, prefix_id: model_id,
            'get_headers_and_cookies': get_headers_and_cookies,
            'is_anthropic_url': lambda url: False,
            'ERROR_MESSAGES': types.SimpleNamespace(MODEL_NOT_FOUND=lambda: 'Fixture model not found'),
            'HTTPException': HTTPException,
        }
        load_functions(OPENAI_ROUTER_PATH, {'get_anthropic_request_target'}, self.router)

        async def count_anthropic_tokens(request, form_data, user):
            target = await self.router['get_anthropic_request_target'](request, form_data, user)
            self.calls.append(('count_tokens', target[1]['model']))
            return 7

        async def passthrough_anthropic_messages(request, form_data, user):
            self.calls.append(('passthrough_to_api_key', form_data['model']))
            return {'passthrough': True}

        async def chat_completion(request, payload, user):
            self.calls.append(('chat_completion', payload['model']))
            return {'model': payload['model']}

        def convert_to_anthropic_response(response, model, input_tokens):
            return {'model': model, 'input_tokens': input_tokens}

        self.route = {
            'openai': types.SimpleNamespace(
                count_anthropic_tokens=count_anthropic_tokens,
                get_all_models=AsyncMock(),
                get_openai_connection=get_openai_connection,
            ),
            'Models': types.SimpleNamespace(get_model_by_id=get_model_by_id),
            'is_conflicting_preset_row': service.is_conflicting_preset_row,
            'get_all_models': load_all_models,
            # The fixture connection is an Anthropic one, so a request routed to it passes through.
            'is_anthropic_messages_passthrough': lambda url, api_config: True,
            'passthrough_anthropic_messages': passthrough_anthropic_messages,
            'convert_anthropic_to_openai_payload': lambda form_data, params: {'model': form_data['model']},
            'chat_completion': chat_completion,
            'StreamingResponse': type('FixtureStreamingResponse', (), {}),
            'convert_openai_to_anthropic_response': convert_to_anthropic_response,
            'Depends': Depends,
            'get_verified_user': None,
            'log': logging.getLogger(__name__),
        }
        load_functions(MAIN_PATH, {'generate_messages', 'load_models_for_messages'}, self.route)

    async def send(self, model_id):
        state = types.SimpleNamespace(MODELS=self.models, OPENAI_MODELS=self.openai_models)
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=state))
        admin = types.SimpleNamespace(id='fixture-admin', role='admin')
        form_data = {'model': model_id, 'messages': [], 'max_tokens': 16}
        return await self.route['generate_messages'](request, form_data, admin)

    async def test_a_row_reusing_a_subscription_model_id_does_not_pass_its_turn_to_the_api(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, API_MODEL_ID, 'Fixture injected instructions')
        with self.assertLogs(__name__, logging.WARNING):
            response = await self.send(BASE_ID)
        self.assertEqual(response, {'model': BASE_ID, 'input_tokens': None})
        # Counting found no API model for the subscription model ID, so it did not use the API key.
        self.assertEqual(self.calls, [('chat_completion', BASE_ID)])

    async def test_the_route_loads_the_model_list_before_deciding(self):
        self.rows[BASE_ID] = preset_row(BASE_ID, API_MODEL_ID, 'Fixture injected instructions')
        self.models.clear()
        with self.assertLogs(__name__, logging.WARNING):
            await self.send(BASE_ID)
        self.assertEqual(self.calls, [('load_models',), ('chat_completion', BASE_ID)])

    async def test_a_listed_preset_still_passes_through_to_its_api_model(self):
        self.rows[PRESET_ID] = preset_row(PRESET_ID, API_MODEL_ID, 'Fixture preset instructions')
        response = await self.send(PRESET_ID)
        self.assertEqual(response, {'passthrough': True})
        self.assertEqual(self.calls, [
            ('count_tokens', API_MODEL_ID), ('passthrough_to_api_key', PRESET_ID),
        ])


if __name__ == '__main__':
    unittest.main()
