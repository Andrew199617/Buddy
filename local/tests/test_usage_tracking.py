"""Offline run tracking tests; no live app, database, or provider requests."""

import ast
import asyncio
import copy
import importlib.util
import sys
import types
import unittest
from numbers import Number
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

LOCAL_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_usage_tracking_under_test', LOCAL_DIR / 'usage_tracking.py')
TRACKING = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = TRACKING
spec.loader.exec_module(TRACKING)


def load_native_usage_helpers():
    """Exercise installed arithmetic without importing app configuration or DB."""
    source_path = LOCAL_DIR.parent / '.venv/Lib/site-packages/open_webui/utils/response.py'
    source = ast.parse(source_path.read_text(encoding='utf-8'))
    function_names = {
        'normalize_usage', 'merge_usage', '_is_numeric_usage_value', '_merge_numeric_usage_map'
    }
    assignment_names = {
        'USAGE_TOKEN_KEYS', 'USAGE_COST_KEYS', 'USAGE_SUMMABLE_KEYS', 'USAGE_DETAIL_KEYS'
    }
    selected = []
    for node in source.body:
        if isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected.append(node)
        elif isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id in assignment_names for target in node.targets):
                selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {'Number': Number}
    exec(compile(module, str(source_path), 'exec'), namespace)
    return namespace['normalize_usage'], namespace['merge_usage']


NATIVE_NORMALIZE, NATIVE_MERGE = load_native_usage_helpers()


async def guard_streaming_handler(response, ctx):
    usage = None
    usage = merge_usage(usage, response)
    return {'usage': usage}


async def guard_nonstreaming_handler(response, ctx):
    response_data = response
    return normalize_usage(response_data.get('usage', {}) or {})



def load_installed_function(path, name, class_name=None):
    """Keep installed source locations for inspect, without running module code."""
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    candidates = parsed.body
    if class_name is not None:
        owner = next(node for node in candidates if isinstance(node, ast.ClassDef) and node.name == class_name)
        candidates = owner.body
    function = next(node for node in candidates if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    future_annotations = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    future_annotations.lineno = 1
    future_annotations.col_offset = 0
    module = ast.fix_missing_locations(ast.Module(body=[future_annotations, function], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[name]


class TokenContractTests(unittest.TestCase):
    def test_preserves_explicit_zero_in_preferred_field(self):
        counts = TRACKING.run_tokens({'input_tokens': 0, 'prompt_tokens': 70, 'output_tokens': 2})
        self.assertEqual(counts, {'input_tokens': 0, 'output_tokens': 2, 'total_tokens': 2})

    def test_missing_total_is_unknown_when_a_side_is_missing(self):
        self.assertEqual(TRACKING.run_tokens({'input_tokens': 80}), {
            'input_tokens': 80, 'output_tokens': None, 'total_tokens': None
        })

    def test_total_only_does_not_invent_zero_split(self):
        run = TRACKING.Run('chat', 'message', 'model')
        run.observe_provider_usage({'total_tokens': 100})
        run.observe_usage(NATIVE_NORMALIZE({'total_tokens': 100}))
        snapshot = run.snapshot()
        self.assertIsNone(snapshot['input_tokens'])
        self.assertIsNone(snapshot['output_tokens'])
        self.assertEqual(snapshot['total_tokens'], 100)
        self.assertFalse(snapshot['usage_complete'])
        self.assertIsNone(snapshot['context_tokens'])

    def test_timing_only_data_has_unavailable_counts(self):
        run = TRACKING.Run('chat', 'message', 'model')
        run.observe_provider_usage({'total_duration': 1000000000})
        run.observe_usage(NATIVE_NORMALIZE({'total_duration': 1000000000}))
        snapshot = run.snapshot()
        self.assertEqual(snapshot['usage_source'], 'unavailable')
        self.assertIsNone(snapshot['total_tokens'])
        self.assertIsNone(snapshot['input_tokens'])
        self.assertIsNone(snapshot['output_tokens'])

    def test_llama_counts_include_cached_prompt_tokens(self):
        run = TRACKING.Run('chat', 'message', 'model')
        run.observe_provider_usage({'prompt_n': 120, 'cache_n': 400, 'predicted_n': 25})
        snapshot = run.snapshot()
        self.assertEqual(snapshot['input_tokens'], 520)
        self.assertEqual(snapshot['output_tokens'], 25)
        self.assertEqual(snapshot['context_tokens'], 545)

    def test_rejects_invalid_counts(self):
        for invalid in (True, -1, float('nan'), float('inf'), 1.5, 'invalid'):
            with self.subTest(value=invalid):
                self.assertIsNone(TRACKING.token_count(invalid))

    def test_only_explicit_capacity_is_used(self):
        capacity = TRACKING.explicit_context_capacity({'num_ctx': 32768, 'compact_token_threshold': 80000}, {})
        self.assertEqual(capacity, {'tokens': 32768, 'source': 'configured'})
        self.assertIsNone(TRACKING.explicit_context_capacity({'compact_token_threshold': 80000}, {}))
        self.assertEqual(TRACKING.explicit_context_capacity({}, {
            'info': {'meta': {'context_length': 200000}}
        }), {'tokens': 200000, 'source': 'advertised'})

    def test_snapshot_time_uses_monotonic_clock_and_finishes_once(self):
        run = TRACKING.Run('chat', 'message', 'model', started_at=100.0, started_monotonic=20.0)
        with patch.object(TRACKING.time, 'time', return_value=105.0):
            with patch.object(TRACKING.time, 'monotonic', return_value=25.25):
                first = run.snapshot()
        with patch.object(TRACKING.time, 'monotonic', return_value=99.0):
            second = run.snapshot()
        self.assertEqual(first['duration_ms'], 5250)
        self.assertEqual(first['completed_at'], 105.0)
        self.assertEqual(second, first)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.messages = {}
        self.writes = []
        self.events = []
        self.request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(state=SimpleNamespace(MODELS={
                'model': {'id': 'model'},
                'selected-model': {'id': 'selected-model', 'info': {'params': {'num_ctx': 64000}}}
            }))
        )

        async def payload(request, form_data, user, metadata, model):
            return form_data, metadata, []

        async def upsert(id, message_id, message, *, touch=True):
            key = (id, message_id)
            saved = copy.deepcopy(self.messages.get(key, {}))
            native_usage = saved.get('usage')
            saved.update(copy.deepcopy(message))
            if message.get('usage'):
                saved['usage'] = NATIVE_MERGE(native_usage, message['usage'])
            self.messages[key] = saved
            self.writes.append((id, message_id, copy.deepcopy(message), touch))
            return saved

        async def read_saved_meta(chat_id, message_id):
            return copy.deepcopy(self.messages.get((chat_id, message_id), {}).get('meta') or {})

        self.original_upsert = upsert
        self.chats = SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=TRACKING.wrap_upsert(upsert))
        async def provider(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
            return None

        self.middleware = SimpleNamespace(
            generate_chat_completion=TRACKING.wrap_tool_provider(provider),
            merge_usage=TRACKING.wrap_merge_usage(NATIVE_MERGE),
            normalize_usage=TRACKING.wrap_normalize_usage(NATIVE_NORMALIZE),
            get_response_data=TRACKING.wrap_response_data(lambda response: (response, response))
        )
        self.payload = TRACKING.wrap_payload(payload)
        self.meta_patch = patch.object(TRACKING, 'read_saved_meta', read_saved_meta)
        self.meta_patch.start()
        self.addCleanup(self.meta_patch.stop)

    async def emit(self, event):
        self.events.append(copy.deepcopy(event))

    async def run_stream(self, raw_reports, *, message_id='message', continuing=False, status='completed'):
        metadata = {'chat_id': 'chat', 'message_id': message_id, 'params': {'num_ctx': 32768}}
        if continuing:
            metadata['assistant_message_id'] = message_id
        form_data = {'model': 'model'}
        model = {'id': 'model'}
        await self.payload(self.request, form_data, None, metadata, model)

        async def response_handler(response, ctx):
            usage = None
            for report_index, raw_usage in enumerate(raw_reports):
                if report_index > 0:
                    await self.middleware.generate_chat_completion(self.request, {}, None)
                await asyncio.sleep(0)
                usage = self.middleware.merge_usage(usage, raw_usage)
                await ctx['event_emitter']({'type': 'chat:completion', 'data': {'usage': usage}})
            if status == 'cancelled':
                await ctx['event_emitter']({'type': 'chat:tasks:cancel', 'data': {}})
                final_message = {'done': True, 'output': []}
            else:
                final_message = {'done': True, 'output': []}
                if usage:
                    final_message['usage'] = usage
                if status == 'error':
                    final_message['error'] = {'content': 'Synthetic test failure'}
            await self.chats.upsert_message_to_chat_by_id_and_message_id('chat', message_id, final_message)
            await ctx['event_emitter']({'type': 'chat:completion', 'data': final_message})
            return response

        response = TRACKING.wrap_response(response_handler)
        await response('native-response', {
            'request': self.request,
            'metadata': metadata,
            'event_emitter': self.emit
        })
        return self.messages[('chat', message_id)].get('meta', {}).get('local_run')

    async def test_two_tool_calls_are_counted_once_and_context_uses_latest_call(self):
        first = {'prompt_tokens': 100, 'completion_tokens': 10}
        second = {'prompt_tokens': 160, 'completion_tokens': 20}
        snapshot = await self.run_stream([first, second])
        self.assertEqual(snapshot['input_tokens'], 260)
        self.assertEqual(snapshot['output_tokens'], 30)
        self.assertEqual(snapshot['total_tokens'], 290)
        self.assertEqual(snapshot['context_tokens'], 180)
        self.assertTrue(snapshot['usage_complete'])
        self.assertEqual(snapshot['context_capacity'], {'tokens': 32768, 'source': 'configured'})
        self.assertEqual(self.messages[('chat', 'message')]['usage'], NATIVE_MERGE(first, second))
        final_event = self.events[-1]['data']['meta']['local_run']
        self.assertEqual(final_event, snapshot)
        self.assertIsNone(TRACKING.ACTIVE_RUN.get())
        self.assertEqual(TRACKING.request_runs(self.request), {})

    async def test_continuation_run_excludes_saved_historical_usage(self):
        prior_usage = NATIVE_NORMALIZE({'prompt_tokens': 900, 'completion_tokens': 90})
        self.messages[('chat', 'message')] = {
            'usage': prior_usage,
            'meta': {'approval': 'preserved', 'local_run': {'run_id': 'previous-run'}}
        }
        snapshot = await self.run_stream([
            {'prompt_tokens': 100, 'completion_tokens': 10},
            {'prompt_tokens': 160, 'completion_tokens': 20}
        ], continuing=True)
        self.assertEqual(snapshot['total_tokens'], 290)
        self.assertEqual(self.messages[('chat', 'message')]['usage']['total_tokens'], 1280)
        self.assertEqual(self.messages[('chat', 'message')]['meta']['approval'], 'preserved')
        self.assertNotEqual(snapshot['run_id'], 'previous-run')

    async def test_missing_first_call_side_stays_unknown_after_last_complete_call(self):
        snapshot = await self.run_stream([
            {'prompt_tokens': 100},
            {'prompt_tokens': 160, 'completion_tokens': 20}
        ])
        self.assertEqual(snapshot['input_tokens'], 260)
        self.assertIsNone(snapshot['output_tokens'])
        self.assertIsNone(snapshot['total_tokens'])
        self.assertEqual(snapshot['context_tokens'], 180)
        self.assertFalse(snapshot['usage_complete'])

    async def test_provider_total_only_keeps_reported_total_and_null_split(self):
        snapshot = await self.run_stream([{'total_tokens': 100}])
        self.assertEqual(snapshot['total_tokens'], 100)
        self.assertIsNone(snapshot['input_tokens'])
        self.assertIsNone(snapshot['output_tokens'])
        self.assertFalse(snapshot['usage_complete'])

    async def test_cancelled_run_preserves_partial_counts_without_claiming_complete_usage(self):
        self.messages[('chat', 'message')] = {'usage': NATIVE_NORMALIZE({'input_tokens': 800, 'output_tokens': 80})}
        snapshot = await self.run_stream([{'input_tokens': 100, 'output_tokens': 10}], continuing=True, status='cancelled')
        self.assertEqual(snapshot['status'], 'cancelled')
        self.assertEqual(snapshot['total_tokens'], 110)
        self.assertFalse(snapshot['usage_complete'])
        self.assertEqual(self.messages[('chat', 'message')]['usage']['total_tokens'], 880)

    async def test_cancelled_run_without_provider_report_does_not_reuse_historical_usage(self):
        self.messages[('chat', 'message')] = {'usage': NATIVE_NORMALIZE({'input_tokens': 800, 'output_tokens': 80})}
        snapshot = await self.run_stream([], continuing=True, status='cancelled')
        self.assertEqual(snapshot['status'], 'cancelled')
        self.assertIsNone(snapshot['total_tokens'])
        self.assertEqual(snapshot['usage_source'], 'unavailable')
        self.assertEqual(self.messages[('chat', 'message')]['usage']['total_tokens'], 880)

    async def test_error_marks_reported_counts_as_incomplete(self):
        snapshot = await self.run_stream([{'input_tokens': 20, 'output_tokens': 0}], status='error')
        self.assertEqual(snapshot['status'], 'error')
        self.assertEqual(snapshot['output_tokens'], 0)
        self.assertFalse(snapshot['usage_complete'])

    async def test_parallel_runs_have_isolated_counters(self):
        first, second = await asyncio.gather(
            self.run_stream([{'input_tokens': 100, 'output_tokens': 10}], message_id='first'),
            self.run_stream([{'input_tokens': 200, 'output_tokens': 20}], message_id='second')
        )
        self.assertEqual(first['total_tokens'], 110)
        self.assertEqual(second['total_tokens'], 220)
        self.assertNotEqual(first['run_id'], second['run_id'])

    async def test_nonstream_normalization_observer_does_not_double_count_native_merge(self):
        metadata = {'chat_id': 'chat', 'message_id': 'nonstream'}
        await self.payload(self.request, {'model': 'model'}, None, metadata, {'id': 'model'})

        async def response_handler(response, ctx):
            usage = self.middleware.normalize_usage({'input_tokens': 70, 'output_tokens': 8})
            await self.chats.upsert_message_to_chat_by_id_and_message_id(
                'chat', 'nonstream', {'done': True, 'usage': usage}
            )
            return response

        await TRACKING.wrap_response(response_handler)(None, {
            'request': self.request, 'metadata': metadata, 'event_emitter': self.emit
        })
        snapshot = self.messages[('chat', 'nonstream')]['meta']['local_run']
        self.assertEqual(snapshot['total_tokens'], 78)

    async def test_nonstream_final_event_before_normalization_has_the_same_counts_as_saved_meta(self):
        metadata = {'chat_id': 'chat', 'message_id': 'nonstream-order'}
        await self.payload(self.request, {'model': 'model'}, None, metadata, {'id': 'model'})

        async def response_handler(response, ctx):
            _response, response_data = self.middleware.get_response_data(response)
            await ctx['event_emitter']({'type': 'chat:completion', 'data': response_data})
            await ctx['event_emitter']({'type': 'chat:completion', 'data': {'done': True, 'output': []}})
            usage = self.middleware.normalize_usage(response_data['usage'])
            await self.chats.upsert_message_to_chat_by_id_and_message_id(
                'chat', 'nonstream-order', {'done': True, 'usage': usage}
            )
            return response

        response = {'model': 'provider-model', 'usage': {'input_tokens': 70, 'output_tokens': 8}}
        await TRACKING.wrap_response(response_handler)(response, {
            'request': self.request, 'metadata': metadata, 'event_emitter': self.emit
        })
        snapshot = self.messages[('chat', 'nonstream-order')]['meta']['local_run']
        self.assertEqual(snapshot['total_tokens'], 78)
        self.assertEqual(snapshot['model_id'], 'provider-model')
        self.assertEqual(self.events[-1]['data']['meta']['local_run'], snapshot)

    async def test_missing_tool_call_report_prevents_complete_run_totals(self):
        metadata = {'chat_id': 'chat', 'message_id': 'missing-report'}
        await self.payload(self.request, {'model': 'model'}, None, metadata, {'id': 'model'})

        async def response_handler(response, ctx):
            # First provider stream returned no usage; its tool follow-up does.
            await self.middleware.generate_chat_completion(self.request, {}, None)
            usage = self.middleware.merge_usage(None, {'input_tokens': 100, 'output_tokens': 10})
            await self.chats.upsert_message_to_chat_by_id_and_message_id(
                'chat', 'missing-report', {'done': True, 'usage': usage}
            )
            return response

        await TRACKING.wrap_response(response_handler)(None, {
            'request': self.request, 'metadata': metadata, 'event_emitter': self.emit
        })
        snapshot = self.messages[('chat', 'missing-report')]['meta']['local_run']
        self.assertIsNone(snapshot['total_tokens'])
        self.assertIsNone(snapshot['input_tokens'])
        self.assertIsNone(snapshot['output_tokens'])
        self.assertFalse(snapshot['usage_complete'])
        self.assertEqual(snapshot['context_tokens'], 110)
        self.assertEqual(self.messages[('chat', 'missing-report')]['usage']['total_tokens'], 110)

    async def test_early_provider_error_replaces_previous_run_meta_without_changing_native_status(self):
        metadata = {'chat_id': 'chat', 'message_id': 'early-error'}
        self.messages[('chat', 'early-error')] = {
            'done': False, 'usage': {'input_tokens': 500, 'output_tokens': 50},
            'meta': {'approval': 'preserved', 'local_run': {'run_id': 'old', 'total_tokens': 550}}
        }
        form_data = {'model': 'model', 'metadata': metadata}
        await self.payload(self.request, form_data, None, metadata, {'id': 'model'})
        expected = RuntimeError('Synthetic initial provider failure')

        async def provider(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
            raise expected

        with self.assertRaises(RuntimeError) as caught:
            await TRACKING.wrap_initial_provider(provider, self.original_upsert)(self.request, form_data, None)
        self.assertIs(caught.exception, expected)
        saved = self.messages[('chat', 'early-error')]
        self.assertFalse(saved['done'])
        self.assertEqual(saved['usage'], {'input_tokens': 500, 'output_tokens': 50})
        self.assertEqual(saved['meta']['approval'], 'preserved')
        self.assertEqual(saved['meta']['local_run']['status'], 'error')
        self.assertIsNone(saved['meta']['local_run']['total_tokens'])
        self.assertNotEqual(saved['meta']['local_run']['run_id'], 'old')
        self.assertEqual(TRACKING.request_runs(self.request), {})

    async def test_early_provider_cancellation_saves_unavailable_attempt_and_preserves_cancellation(self):
        metadata = {'chat_id': 'chat', 'message_id': 'early-cancel'}
        form_data = {'model': 'model', 'metadata': metadata}
        await self.payload(self.request, form_data, None, metadata, {'id': 'model'})

        async def provider(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await TRACKING.wrap_initial_provider(provider, self.original_upsert)(self.request, form_data, None)
        saved = self.messages[('chat', 'early-cancel')]
        self.assertNotIn('done', saved)
        self.assertEqual(saved['meta']['local_run']['status'], 'cancelled')
        self.assertIsNone(saved['meta']['local_run']['total_tokens'])
        self.assertEqual(TRACKING.request_runs(self.request), {})

    async def test_provider_http_error_result_is_returned_unchanged_with_attempt_snapshot(self):
        metadata = {'chat_id': 'chat', 'message_id': 'http-error'}
        form_data = {'model': 'model', 'metadata': metadata}
        await self.payload(self.request, form_data, None, metadata, {'id': 'model'})
        expected = SimpleNamespace(status_code=400)

        async def provider(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
            return expected

        result = await TRACKING.wrap_initial_provider(provider, self.original_upsert)(self.request, form_data, None)
        self.assertIs(result, expected)
        self.assertEqual(self.messages[('chat', 'http-error')]['meta']['local_run']['status'], 'error')
        self.assertEqual(TRACKING.request_runs(self.request), {})

    async def test_approval_pause_only_writes_namespaced_metadata_and_cleans_pending_state(self):
        metadata = {'chat_id': 'chat', 'message_id': 'paused'}
        self.messages[('chat', 'paused')] = {'done': False, 'meta': {'approval': 'preserved'}}
        await self.payload(self.request, {'model': 'model'}, None, metadata, {'id': 'model'})

        async def drain(request, form_data, user, model, metadata):
            return True

        result = await TRACKING.wrap_tool_drain(drain, self.original_upsert)(self.request, {}, None, {}, metadata)
        self.assertTrue(result)
        saved = self.messages[('chat', 'paused')]
        self.assertFalse(saved['done'])
        self.assertEqual(saved['meta']['approval'], 'preserved')
        self.assertEqual(saved['meta']['local_run']['status'], 'paused')
        self.assertIsNone(saved['meta']['local_run']['total_tokens'])
        self.assertEqual(TRACKING.request_runs(self.request), {})

    async def test_redis_model_registry_is_used_for_selected_capacity(self):
        class ModelRegistry:
            def get(self, identifier):
                if identifier == 'selected-model':
                    return {'id': 'selected-model', 'info': {'params': {'num_ctx': 64000}}}
                return None

        self.request.app.state.MODELS = ModelRegistry()
        metadata = {'chat_id': 'chat', 'message_id': 'registry', 'selected_model_id': 'selected-model'}
        await self.payload(self.request, {'model': 'selected-model'}, None, metadata, {'id': 'arena'})
        run = TRACKING.request_runs(self.request)[('chat', 'registry')]
        self.assertEqual(run.model_id, 'selected-model')
        self.assertEqual(run.context_capacity, {'tokens': 64000, 'source': 'configured'})

    async def test_metadata_read_failure_preserves_the_original_write(self):
        async def broken_meta(chat_id, message_id):
            raise RuntimeError('Synthetic metadata lookup failure')

        with patch.object(TRACKING, 'read_saved_meta', broken_meta):
            with self.assertLogs('owui_local_patches', 'WARNING'):
                snapshot = await self.run_stream([{'input_tokens': 10, 'output_tokens': 1}])
        self.assertIsNone(snapshot)
        self.assertNotIn('meta', self.writes[-1][2])
        self.assertEqual(self.messages[('chat', 'message')]['usage']['total_tokens'], 11)
        self.assertTrue(self.messages[('chat', 'message')]['done'])

    async def test_tracking_failure_does_not_change_native_provider_response(self):
        run = TRACKING.Run('chat', 'message', 'model')
        token = TRACKING.ACTIVE_RUN.set(run)
        try:
            with patch.object(run, 'observe_provider_usage', side_effect=RuntimeError('Synthetic observer failure')):
                with self.assertLogs('owui_local_patches', 'WARNING'):
                    result = self.middleware.merge_usage(None, {'input_tokens': 10, 'output_tokens': 1})
            self.assertEqual(result, NATIVE_MERGE(None, {'input_tokens': 10, 'output_tokens': 1}))
        finally:
            TRACKING.ACTIVE_RUN.reset(token)

    async def test_payload_failure_is_preserved_and_state_is_disposed(self):
        expected = RuntimeError('Original payload failure')

        async def broken_payload(request, form_data, user, metadata, model):
            raise expected

        with self.assertRaises(RuntimeError) as caught:
            await TRACKING.wrap_payload(broken_payload)(self.request, {}, None, {
                'chat_id': 'chat', 'message_id': 'message'
            }, {})
        self.assertIs(caught.exception, expected)
        self.assertEqual(TRACKING.request_runs(self.request), {})

    async def test_unrelated_message_writes_are_unchanged(self):
        run = TRACKING.Run('chat', 'message', 'model')
        token = TRACKING.ACTIVE_RUN.set(run)
        message = {'done': True, 'usage': {'input_tokens': 1, 'output_tokens': 2}}
        try:
            await self.chats.upsert_message_to_chat_by_id_and_message_id('chat', 'unrelated', message, touch=False)
        finally:
            TRACKING.ACTIVE_RUN.reset(token)
        self.assertEqual(self.writes[-1][2], message)
        self.assertFalse(self.writes[-1][3])


class InstallationTests(unittest.TestCase):
    def setUp(self):
        async def payload(request, form_data, user, metadata, model):
            return form_data, metadata, []

        async def response(response, ctx):
            return response

        async def upsert(id, message_id, message, *, touch=True):
            return message

        async def provider(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
            return None

        async def drain(request, form_data, user, model, metadata):
            return False

        self.main = SimpleNamespace(
            process_chat_payload=payload, process_chat_response=response,
            chat_completion_handler=provider, drain_approved_tool_calls=drain
        )
        self.middleware = SimpleNamespace(
            process_chat_payload=payload,
            process_chat_response=response,
            generate_chat_completion=provider,
            drain_approved_tool_calls=drain,
            streaming_chat_response_handler=guard_streaming_handler,
            non_streaming_chat_response_handler=guard_nonstreaming_handler,
            merge_usage=NATIVE_MERGE,
            normalize_usage=NATIVE_NORMALIZE,
            get_response_data=lambda response: (response, response)
        )
        self.chats = SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=upsert)
        modules = {
            'open_webui.main': self.main,
            'open_webui.utils.middleware': self.middleware,
            'open_webui.models.chats': SimpleNamespace(Chats=self.chats)
        }
        self.import_patch = patch.object(TRACKING.importlib, 'import_module', side_effect=modules.__getitem__)
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)

    def test_installs_once_and_updates_actual_bound_aliases(self):
        with patch.object(TRACKING, 'package_version', return_value='0.11.4'):
            self.assertTrue(TRACKING.apply())
            first = self.main.process_chat_response
            self.assertTrue(TRACKING.apply())
        self.assertIs(self.main.process_chat_response, first)
        self.assertIs(self.main.process_chat_payload, self.middleware.process_chat_payload)
        self.assertIs(self.main.process_chat_response, self.middleware.process_chat_response)
        self.assertTrue(self.chats.upsert_message_to_chat_by_id_and_message_id.__local_usage_tracking__)

    def test_installed_source_lifecycle_is_supported_without_importing_the_app(self):
        package_dir = LOCAL_DIR.parent / '.venv/Lib/site-packages/open_webui'
        middleware_path = package_dir / 'utils/middleware.py'
        chats_path = package_dir / 'models/chats.py'
        payload = load_installed_function(middleware_path, 'process_chat_payload')
        response = load_installed_function(middleware_path, 'process_chat_response')
        upsert = load_installed_function(chats_path, 'upsert_message_to_chat_by_id_and_message_id', 'ChatTable')
        provider = load_installed_function(package_dir / 'utils/chat.py', 'generate_chat_completion')
        drain = load_installed_function(middleware_path, 'drain_approved_tool_calls')
        main = SimpleNamespace(
            process_chat_payload=payload, process_chat_response=response,
            chat_completion_handler=provider, drain_approved_tool_calls=drain
        )
        middleware = SimpleNamespace(
            streaming_chat_response_handler=load_installed_function(middleware_path, 'streaming_chat_response_handler'),
            non_streaming_chat_response_handler=load_installed_function(middleware_path, 'non_streaming_chat_response_handler'),
            get_response_data=load_installed_function(middleware_path, 'get_response_data'),
            generate_chat_completion=provider,
            merge_usage=NATIVE_MERGE,
            normalize_usage=NATIVE_NORMALIZE
        )
        chats = SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=types.MethodType(upsert, object()))
        self.assertTrue(TRACKING.lifecycle_is_supported(main, middleware, chats))

    def test_unknown_version_is_skipped_without_modifying_hooks(self):
        original = self.main.process_chat_response
        with patch.object(TRACKING, 'package_version', return_value='0.99.0'):
            with self.assertLogs('owui_local_patches', 'WARNING'):
                self.assertFalse(TRACKING.apply())
        self.assertIs(self.main.process_chat_response, original)

    def test_changed_signature_is_skipped_without_modifying_hooks(self):
        async def changed_payload(request, data):
            return data

        self.main.process_chat_payload = changed_payload
        original = self.main.process_chat_response
        with patch.object(TRACKING, 'package_version', return_value='0.11.4'):
            with self.assertLogs('owui_local_patches', 'WARNING'):
                self.assertFalse(TRACKING.apply())
        self.assertIs(self.main.process_chat_response, original)

    def test_missing_lifecycle_reset_semantics_are_skipped(self):
        async def changed_streaming_handler(response, ctx):
            return response

        self.middleware.streaming_chat_response_handler = changed_streaming_handler
        with patch.object(TRACKING, 'package_version', return_value='0.11.4'):
            with self.assertLogs('owui_local_patches', 'WARNING'):
                self.assertFalse(TRACKING.apply())


if __name__ == '__main__':
    unittest.main()
