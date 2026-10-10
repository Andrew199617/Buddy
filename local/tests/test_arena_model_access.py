"""Hermetic arena authorization regressions using the actual production functions.

Definitions are loaded through AST without importing app configuration or starting
a database. The catalog, access-grant queries and provider responses are fixtures.
"""

import ast
import asyncio
import copy
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
UTILS = ROOT / 'backend' / 'open_webui' / 'utils'


def execute_definitions(path, names, namespace):
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    selected = [node for node in parsed.body if getattr(node, 'name', None) in names]
    if len(selected) != len(names):
        raise AssertionError(f'Missing production definitions in {path}: {names}')
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)


def model_entry(model_id, owned_by='openai', owner='another-user'):
    return {
        'id': model_id,
        'owned_by': owned_by,
        'info': {'id': model_id, 'user_id': owner},
    }


def arena_entry(model_ids=None, filter_mode=None):
    meta = {'model_ids': model_ids, 'filter_mode': filter_mode}
    return {'id': 'arena', 'owned_by': 'arena', 'arena': True, 'info': {'meta': meta}}


class PayloadSelected(Exception):
    def __init__(self, form_data, model):
        self.form_data = form_data
        self.model = model


class ArenaModelAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = SimpleNamespace(id='alice', role='user')
        self.allowed_ids = {'public', 'public-other'}
        self.access_queries = []
        self.group_queries = []
        self.provider_calls = []
        self.choices = []

        async def get_groups(user_id, db=None):
            self.group_queries.append(user_id)
            return [SimpleNamespace(id='readers')]

        async def get_accessible_ids(**kwargs):
            self.access_queries.append(kwargs)
            return self.allowed_ids.intersection(kwargs['resource_ids'])

        self.models_namespace = {
            'BYPASS_MODEL_ACCESS_CONTROL': False,
            'BYPASS_ADMIN_ACCESS_CONTROL': True,
            'SUBSCRIPTION_OWNED_BY': 'subscription',
            'HTTPException': HTTPException,
            'Groups': SimpleNamespace(get_groups_by_member_id=get_groups),
            'AccessGrants': SimpleNamespace(get_accessible_resource_ids=get_accessible_ids),
        }
        definitions = {'get_filtered_models'}
        parsed = ast.parse((UTILS / 'models.py').read_text(encoding='utf-8'))
        if any(getattr(node, 'name', None) == 'get_arena_model_ids' for node in parsed.body):
            definitions.add('get_arena_model_ids')
        execute_definitions(UTILS / 'models.py', definitions, self.models_namespace)

        def choose(candidates):
            self.choices.append(list(candidates))
            # Force the original defect instead of relying on a random draw.
            if 'subscription' in candidates:
                return 'subscription'
            return candidates[0]

        async def check_model_access(user, model):
            if model.get('owned_by') == 'arena':
                return
            if model['id'] not in self.allowed_ids:
                raise Exception('Model not found')

        async def subscription_provider(request, form_data, user, models):
            self.provider_calls.append(('subscription', form_data['model']))
            if user.role != 'admin':
                raise HTTPException(status_code=403, detail='Subscriptions are admin-only')
            return {'provider': 'subscription'}

        async def openai_provider(request, form_data, user):
            self.provider_calls.append(('openai', form_data['model']))
            return {'provider': 'openai'}

        shared = {
            'log': logging.getLogger(__name__),
            'random': SimpleNamespace(choice=choose),
            'BYPASS_MODEL_ACCESS_CONTROL': False,
            'HTTPException': HTTPException,
            'check_model_access': check_model_access,
            'SUBSCRIPTION_OWNED_BY': 'subscription',
            'generate_subscription_chat_completion': subscription_provider,
            'generate_openai_chat_completion': openai_provider,
        }
        if 'get_arena_model_ids' in self.models_namespace:
            shared['get_arena_model_ids'] = self.models_namespace['get_arena_model_ids']
        self.chat_namespace = dict(shared)
        execute_definitions(UTILS / 'chat.py', {'generate_chat_completion'}, self.chat_namespace)

        def capture_payload(form_data, selected_model):
            raise PayloadSelected(form_data, selected_model)

        self.middleware_namespace = dict(shared, apply_params_to_form_data=capture_payload)
        execute_definitions(UTILS / 'middleware.py', {'process_chat_payload'}, self.middleware_namespace)

    def request(self, arena, candidates):
        models = {model['id']: model for model in [arena, *candidates]}
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)),
                               state=SimpleNamespace())

    async def payload_selection(self, arena, candidates):
        request = self.request(arena, candidates)
        form_data = {'model': 'arena', 'messages': []}
        metadata = {}
        with self.assertRaises(PayloadSelected) as captured:
            await self.middleware_namespace['process_chat_payload'](
                request, form_data, self.user, metadata, arena)
        return captured.exception.model, form_data, metadata

    async def fallback_selection(self, arena, candidates, bypass_filter=False):
        request = self.request(arena, candidates)
        form_data = {'model': 'arena', 'metadata': {}, 'messages': []}
        response = await self.chat_namespace['generate_chat_completion'](
            request, form_data, self.user, bypass_filter=bypass_filter)
        return response, form_data

    async def test_chat_payload_does_not_draw_admin_subscription_from_default_pool(self):
        selected, form_data, metadata = await self.payload_selection(arena_entry(), [
            model_entry('public'), model_entry('subscription', 'subscription'),
        ])
        self.assertEqual(selected['id'], 'public')
        self.assertEqual(form_data['model'], 'public')
        self.assertEqual(metadata['selected_model_id'], 'public')

    async def test_background_fallback_does_not_call_admin_subscription(self):
        response, form_data = await self.fallback_selection(arena_entry(), [
            model_entry('public'), model_entry('subscription', 'subscription'),
        ])
        self.assertEqual(response, {'provider': 'openai', 'selected_model_id': 'public'})
        self.assertEqual(form_data['model'], 'public')
        self.assertEqual(self.provider_calls, [('openai', 'public')])

    async def test_exclusion_keeps_subscription_out_of_both_selection_paths(self):
        arena = arena_entry(['public-other'], 'exclude')
        candidates = [model_entry('public'), model_entry('public-other'),
                      model_entry('subscription', 'subscription')]
        selected, _, _ = await self.payload_selection(arena, candidates)
        self.assertEqual(selected['id'], 'public')
        response, _ = await self.fallback_selection(arena, candidates)
        self.assertEqual(response['selected_model_id'], 'public')
        self.assertEqual(self.choices, [['public'], ['public']])

    async def test_explicit_include_cannot_authorize_subscription_or_private_model(self):
        arena = arena_entry(['subscription', 'private', 'public-other'], 'include')
        candidates = [model_entry('public'), model_entry('public-other'),
                      model_entry('private'), model_entry('subscription', 'subscription')]
        selected, _, _ = await self.payload_selection(arena, candidates)
        self.assertEqual(selected['id'], 'public-other')
        response, _ = await self.fallback_selection(arena, candidates)
        self.assertEqual(response['selected_model_id'], 'public-other')
        self.assertEqual(self.choices, [['public-other'], ['public-other']])

    async def test_admin_subscription_is_retained_in_default_and_explicit_pools(self):
        self.user.role = 'admin'
        for arena in [arena_entry(), arena_entry(['subscription'], 'include')]:
            candidates = [model_entry('public'), model_entry('subscription', 'subscription')]
            selected, _, _ = await self.payload_selection(arena, candidates)
            self.assertEqual(selected['id'], 'subscription')
            response, _ = await self.fallback_selection(arena, candidates)
            self.assertEqual(response, {'provider': 'subscription', 'selected_model_id': 'subscription'})
        self.assertEqual(self.access_queries, [])
        self.assertEqual(self.group_queries, [])

    async def test_excluding_every_model_does_not_fall_back_to_the_excluded_pool(self):
        arena = arena_entry(['public', 'subscription'], 'exclude')
        candidates = [model_entry('public'), model_entry('subscription', 'subscription')]
        for select in [self.payload_selection, self.fallback_selection]:
            with self.assertRaises(HTTPException) as captured:
                await select(arena, candidates)
            self.assertEqual(captured.exception.status_code, 403)
            self.assertEqual(captured.exception.detail, 'No accessible models available for arena')
        self.assertEqual(self.choices, [])
        self.assertEqual(self.provider_calls, [])

    async def test_inaccessible_explicit_pool_does_not_fall_back_to_other_public_model(self):
        arena = arena_entry(['private', 'subscription'], 'include')
        candidates = [model_entry('public'), model_entry('private'),
                      model_entry('subscription', 'subscription')]
        for select in [self.payload_selection, self.fallback_selection]:
            with self.assertRaises(HTTPException) as captured:
                await select(arena, candidates)
            self.assertEqual(captured.exception.status_code, 403)
        self.assertEqual(self.choices, [])
        self.assertEqual(self.provider_calls, [])

    async def test_empty_catalog_reports_access_error_without_random_choice(self):
        for select in [self.payload_selection, self.fallback_selection]:
            with self.assertRaises(HTTPException) as captured:
                await select(arena_entry(), [])
            self.assertEqual(captured.exception.status_code, 403)
        self.assertEqual(self.choices, [])

    async def test_only_admin_subscription_catalog_fails_closed_for_regular_user(self):
        for select in [self.payload_selection, self.fallback_selection]:
            with self.assertRaises(HTTPException) as captured:
                await select(arena_entry(), [model_entry('subscription', 'subscription')])
            self.assertEqual(captured.exception.status_code, 403)
        self.assertEqual(self.provider_calls, [])

    async def test_include_ignores_missing_ids_and_nested_arenas(self):
        arena = arena_entry(['missing', 'nested-arena', None, 12, 'public'], 'include')
        candidates = [model_entry('nested-arena', 'arena'), model_entry('public')]
        selected, _, _ = await self.payload_selection(arena, candidates)
        self.assertEqual(selected['id'], 'public')
        response, _ = await self.fallback_selection(arena, candidates)
        self.assertEqual(response['selected_model_id'], 'public')
        self.assertEqual(self.choices, [['public'], ['public']])

    async def test_missing_explicit_ids_do_not_expand_to_unlisted_models(self):
        with self.assertRaises(HTTPException):
            await self.fallback_selection(arena_entry(['missing'], 'include'), [model_entry('public')])
        self.assertEqual(self.choices, [])

    async def test_empty_include_and_exclude_use_the_default_authorized_pool(self):
        for filter_mode in ['include', 'exclude']:
            response, _ = await self.fallback_selection(arena_entry([], filter_mode), [
                model_entry('public'), model_entry('subscription', 'subscription'),
            ])
            self.assertEqual(response['selected_model_id'], 'public')

    async def test_batch_visibility_preserves_owned_and_granted_models(self):
        candidates = [model_entry('owned', owner='alice'), model_entry('public'),
                      model_entry('private'), model_entry('subscription', 'subscription')]
        before = copy.deepcopy(candidates)
        ids = await self.models_namespace['get_arena_model_ids'](candidates, arena_entry(), self.user)
        self.assertEqual(ids, ['owned', 'public'])
        self.assertEqual(candidates, before)
        self.assertEqual(self.group_queries, ['alice'])
        self.assertEqual(len(self.access_queries), 1)
        query = self.access_queries[0]
        self.assertEqual(query['user_id'], 'alice')
        self.assertEqual(query['permission'], 'read')
        self.assertEqual(query['resource_type'], 'model')
        self.assertEqual(query['user_group_ids'], {'readers'})
        self.assertEqual(query['resource_ids'], ['owned', 'public', 'private'])

    async def test_global_acl_bypass_does_not_bypass_subscription_admin_restriction(self):
        self.models_namespace['BYPASS_MODEL_ACCESS_CONTROL'] = True
        self.chat_namespace['BYPASS_MODEL_ACCESS_CONTROL'] = True
        candidates = [model_entry('public'), model_entry('private'),
                      model_entry('subscription', 'subscription')]
        selected, _, _ = await self.payload_selection(arena_entry(), candidates)
        self.assertEqual(selected['id'], 'public')
        response, _ = await self.fallback_selection(arena_entry(), candidates)
        self.assertEqual(response['selected_model_id'], 'public')
        self.assertEqual(self.choices, [['public', 'private'], ['public', 'private']])
        self.assertEqual(self.access_queries, [])

    async def test_explicit_fallback_acl_bypass_still_excludes_subscription(self):
        response, _ = await self.fallback_selection(arena_entry(), [
            model_entry('public'), model_entry('private'), model_entry('subscription', 'subscription'),
        ], bypass_filter=True)
        self.assertEqual(response['selected_model_id'], 'public')
        self.assertEqual(self.choices, [['public', 'private']])
        self.assertEqual(self.access_queries, [])

    async def test_access_query_failure_does_not_select_or_call_a_provider(self):
        async def unavailable_access(**kwargs):
            raise RuntimeError('fixture access query failed')
        self.models_namespace['AccessGrants'].get_accessible_resource_ids = unavailable_access
        for select in [self.payload_selection, self.fallback_selection]:
            with self.assertRaisesRegex(RuntimeError, 'fixture access query failed'):
                await select(arena_entry(), [model_entry('public')])
        self.assertEqual(self.choices, [])
        self.assertEqual(self.provider_calls, [])

    async def test_access_query_cancellation_is_not_swallowed(self):
        async def cancelled_access(**kwargs):
            raise asyncio.CancelledError()
        self.models_namespace['AccessGrants'].get_accessible_resource_ids = cancelled_access
        with self.assertRaises(asyncio.CancelledError):
            await self.fallback_selection(arena_entry(), [model_entry('public')])
        self.assertEqual(self.choices, [])
        self.assertEqual(self.provider_calls, [])


if __name__ == '__main__':
    unittest.main()
