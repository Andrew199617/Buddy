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
MAIN_PATH = ROOT / 'backend' / 'open_webui' / 'main.py'
MISC_PATH = ROOT / 'backend' / 'open_webui' / 'utils' / 'misc.py'


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
        admin = types.SimpleNamespace(role='admin')
        return await self.service._apply_preset(request, payload, {}, admin, self.models)

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


if __name__ == '__main__':
    unittest.main()
