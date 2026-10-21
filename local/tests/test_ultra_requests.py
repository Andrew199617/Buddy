"""Offline tests: the chat route takes the composer's Ultra choice out of the request body.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_requests.py -v

The route keeps the choice in metadata['ultra'] only, so it never reaches
params, features or the provider payload, and API keys can't turn Ultra on.
"""

import asyncio
import copy
import logging
import types
import unittest
from unittest.mock import AsyncMock

from fastapi import Depends, HTTPException, status
from test_subscription_preset_rows import MAIN_PATH, MISC_PATH, load_functions, subscription_model

MODEL_ID = 'claude-code.fixture-opus'
CHOICE = {'worker_model': 'claude-code.fixture-sonnet', 'worker_effort': 'high'}


class ReachedChatPayload(BaseException):
    """Stops the chat route where it hands the request to the payload pipeline."""


def fixture_request(auth_type=None):
    state = types.SimpleNamespace()
    if auth_type is not None:
        state.auth_type = auth_type
    return types.SimpleNamespace(state=state, headers={})


class PopUltraTests(unittest.TestCase):
    def setUp(self):
        self.route = {'HTTPException': HTTPException, 'status': status}
        load_functions(MAIN_PATH, {'_pop_ultra'}, self.route)

    def pop(self, form_data, auth_type=None):
        return self.route['_pop_ultra'](form_data, fixture_request(auth_type))

    def test_a_choice_is_taken_out_of_the_body(self):
        form_data = {'model': MODEL_ID, 'ultra': dict(CHOICE)}
        self.assertEqual(self.pop(form_data), CHOICE)
        self.assertEqual(form_data, {'model': MODEL_ID})

    def test_workers_may_use_the_chats_own_model_and_thinking(self):
        choice = {'worker_model': None, 'worker_effort': None}
        self.assertEqual(self.pop({'ultra': dict(choice)}), choice)

    def test_a_body_without_ultra_has_no_choice(self):
        form_data = {'model': MODEL_ID}
        self.assertIsNone(self.pop(form_data))
        self.assertEqual(form_data, {'model': MODEL_ID})

    def test_anything_else_is_dropped_and_still_removed(self):
        not_choices = [
            None,
            True,
            'on',
            ['claude-code.fixture-sonnet', 'high'],
            {},
            {'worker_model': 'claude-code.fixture-sonnet'},
            {'worker_effort': 'high'},
            {**CHOICE, 'on': False},
            {'worker_model': 5, 'worker_effort': 'high'},
            {'worker_model': 'claude-code.fixture-sonnet', 'worker_effort': {'level': 'high'}},
        ]
        for value in not_choices:
            with self.subTest(ultra=value):
                form_data = {'model': MODEL_ID, 'ultra': value}
                self.assertIsNone(self.pop(form_data))
                self.assertEqual(form_data, {'model': MODEL_ID})

    def test_an_api_key_cannot_turn_ultra_on(self):
        form_data = {'model': MODEL_ID, 'ultra': dict(CHOICE)}
        with self.assertRaises(HTTPException) as refused:
            self.pop(form_data, auth_type='api_key')
        self.assertEqual(refused.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('Ultra', refused.exception.detail)
        self.assertEqual(form_data, {'model': MODEL_ID})

    def test_an_api_key_request_without_a_choice_is_not_refused(self):
        self.assertIsNone(self.pop({'model': MODEL_ID}, auth_type='api_key'))
        self.assertIsNone(self.pop({'model': MODEL_ID, 'ultra': 'on'}, auth_type='api_key'))

    def test_a_signed_in_session_may_turn_ultra_on(self):
        self.assertEqual(self.pop({'ultra': dict(CHOICE)}, auth_type='jwt'), CHOICE)


class ChatRouteUltraTests(unittest.IsolatedAsyncioTestCase):
    """/api/chat/completions keeps the choice in metadata and out of everything forwarded."""

    def setUp(self):
        self.reached = None

        async def get_config(key, default=None):
            return default

        async def reach_chat_payload(request, form_data, user, metadata, model):
            self.reached = {'form_data': copy.deepcopy(form_data), 'metadata': metadata}
            raise ReachedChatPayload()

        # Default access settings: an administrator's chat skips the model access check.
        self.route = {
            'Models': types.SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
            'Config': types.SimpleNamespace(get=get_config),
            'is_conflicting_preset_row': lambda model, model_info: False,
            'ENABLE_CUSTOM_MODEL_FALLBACK': False,
            'BYPASS_MODEL_ACCESS_CONTROL': False,
            'BYPASS_ADMIN_ACCESS_CONTROL': True,
            'get_all_models': AsyncMock(),
            'normalize_chat_variables': lambda chat_variables: chat_variables,
            'SUBSCRIPTION_OWNED_BY': 'subscription',
            'process_chat_payload': reach_chat_payload,
            'asyncio': asyncio,
            'copy': copy,
            'Depends': Depends,
            'get_verified_user': None,
            'HTTPException': HTTPException,
            'status': status,
            'log': logging.getLogger(__name__),
        }
        load_functions(MISC_PATH, {'merge_model_params'}, self.route)
        load_functions(MAIN_PATH, {'_pop_ultra', 'chat_completion'}, self.route)

    async def call_route(self, extra_fields, auth_type=None):
        request = fixture_request(auth_type)
        models = {MODEL_ID: subscription_model(MODEL_ID, 'fixture-opus')}
        request.app = types.SimpleNamespace(state=types.SimpleNamespace(MODELS=models))
        admin = types.SimpleNamespace(id='fixture-admin', role='admin')
        body = {
            'model': MODEL_ID,
            'messages': [{'role': 'user', 'content': 'Fixture question'}],
            'chat_variables': {},
            'params': {'temperature': 0.2},
            'features': {'web_search': False},
            **extra_fields,
        }
        await self.route['chat_completion'](request, body, admin)

    async def send(self, extra_fields):
        """Run the route until it hands the request on, and return what it handed on."""
        with self.assertRaises(ReachedChatPayload):
            await self.call_route(extra_fields)
        return self.reached

    async def test_the_choice_reaches_metadata_and_nothing_forwarded(self):
        reached = await self.send({'ultra': dict(CHOICE)})
        form_data = reached['form_data']
        metadata = reached['metadata']
        self.assertEqual(metadata['ultra'], CHOICE)
        self.assertNotIn('ultra', form_data)
        self.assertNotIn('ultra', form_data['params'])
        self.assertNotIn('ultra', form_data['features'])
        self.assertNotIn('ultra', metadata['params'])
        self.assertEqual(metadata['features'], {'web_search': False})

    async def test_a_turn_without_ultra_has_none_in_metadata(self):
        reached = await self.send({})
        self.assertNotIn('ultra', reached['metadata'])

    async def test_a_malformed_choice_is_dropped_before_the_payload(self):
        reached = await self.send({'ultra': {'on': True}})
        self.assertNotIn('ultra', reached['form_data'])
        self.assertNotIn('ultra', reached['metadata'])

    async def test_an_api_key_request_with_a_choice_is_refused(self):
        with self.assertRaises(HTTPException) as refused:
            await self.call_route({'ultra': dict(CHOICE)}, auth_type='api_key')
        self.assertEqual(refused.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIsNone(self.reached)

    async def test_an_api_key_request_without_ultra_still_runs(self):
        with self.assertRaises(ReachedChatPayload):
            await self.call_route({}, auth_type='api_key')
        self.assertNotIn('ultra', self.reached['metadata'])


if __name__ == '__main__':
    unittest.main()
