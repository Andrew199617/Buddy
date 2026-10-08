"""Persist metadata for each new response run without changing native usage.

Call apply() after the Open WebUI app imports. Supported response handlers reset
usage for each run, including continuations, and sum all provider/tool calls.
Their final payload is a run snapshot even when saved native usage accumulates
across several runs on one message. Unknown lifecycle versions are skipped.
"""

import asyncio
import contextvars
import functools
import importlib
import inspect
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from importlib.metadata import version as package_version
from typing import Any

log = logging.getLogger('owui_local_patches')
SUPPORTED_VERSIONS = {'0.11.4'}
SNAPSHOT_VERSION = 1
REQUEST_STATE_KEY = '_owui_local_response_runs'
ACTIVE_RUN = contextvars.ContextVar('owui_local_response_run', default=None)


def token_count(value: Any) -> int | None:
    """Accept non-negative integral counts, including explicit zero."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def first_count(data: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in data:
            count = token_count(data[key])
            if count is not None:
                return count
    return None


def run_tokens(usage: Any) -> dict:
    """Read one cumulative run snapshot; never add successive snapshots."""
    result = {'input_tokens': None, 'output_tokens': None, 'total_tokens': None}
    if not isinstance(usage, dict):
        return result
    input_tokens = first_count(usage, ('input_tokens', 'prompt_tokens', 'prompt_eval_count'))
    if input_tokens is None and ('prompt_n' in usage or 'cache_n' in usage):
        prompt = token_count(usage.get('prompt_n', 0))
        cached = token_count(usage.get('cache_n', 0))
        if prompt is not None and cached is not None:
            input_tokens = prompt + cached
    output_tokens = first_count(usage, ('output_tokens', 'completion_tokens', 'eval_count', 'predicted_n'))
    total_tokens = first_count(usage, ('total_tokens',))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    result['input_tokens'] = input_tokens
    result['output_tokens'] = output_tokens
    result['total_tokens'] = total_tokens
    return result


def context_tokens(usage: Any) -> int | None:
    """Latest call occupancy is distinct from the run's cumulative consumption."""
    if not isinstance(usage, dict):
        return None
    prompt = first_count(usage, ('prompt_tokens', 'prompt_eval_count'))
    if prompt is None and ('prompt_n' in usage or 'cache_n' in usage):
        prompt_n = token_count(usage.get('prompt_n', 0))
        cached = token_count(usage.get('cache_n', 0))
        if prompt_n is not None and cached is not None:
            prompt = prompt_n + cached
    if prompt is None:
        prompt = first_count(usage, ('input_tokens',))
    completion = first_count(usage, ('completion_tokens', 'eval_count', 'predicted_n', 'output_tokens'))
    if prompt is None or completion is None:
        return None
    return prompt + completion


def explicit_context_capacity(params: Any, model: Any) -> dict | None:
    """Accept configuration or advertised metadata, never compaction thresholds."""
    params = params if isinstance(params, dict) else {}
    model = model if isinstance(model, dict) else {}
    info = model.get('info') if isinstance(model.get('info'), dict) else {}
    model_params = info.get('params') if isinstance(info.get('params'), dict) else {}
    configured = first_count(params, ('num_ctx',))
    if configured is None:
        configured = first_count(model_params, ('num_ctx',))
    if configured:
        return {'tokens': configured, 'source': 'configured'}
    meta = info.get('meta') if isinstance(info.get('meta'), dict) else {}
    advertised = first_count(model, ('context_window', 'context_length', 'max_model_len'))
    if advertised is None:
        advertised = first_count(meta, ('context_window', 'context_length', 'max_model_len'))
    if advertised:
        return {'tokens': advertised, 'source': 'advertised'}
    return None


def resolved_model_id(form_data: Any, metadata: Any, model: Any) -> str | None:
    form_data = form_data if isinstance(form_data, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    model = model if isinstance(model, dict) else {}
    selected = metadata.get('selected_model_id')
    if isinstance(selected, str) and selected:
        return selected
    info = model.get('info') if isinstance(model.get('info'), dict) else {}
    base_model_id = info.get('base_model_id')
    if isinstance(base_model_id, str) and base_model_id:
        return base_model_id
    identifier = form_data.get('model') or model.get('id') or metadata.get('model_id')
    return identifier if isinstance(identifier, str) and identifier else None


@dataclass
class Run:
    chat_id: str
    message_id: str
    model_id: str | None
    context_capacity: dict | None = None
    started_at: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    usage: dict | None = None
    completed_at: float | None = None
    duration_ms: int | None = None
    status: str = 'completed'
    provider_reports: int = 0
    reported_tokens: dict = field(default_factory=lambda: {
        'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0
    })
    complete_fields: dict = field(default_factory=lambda: {
        'input_tokens': True, 'output_tokens': True, 'total_tokens': True
    })
    latest_context_tokens: int | None = None
    nonstream_parsed: bool = False
    expected_provider_calls: int | None = None

    def observe_provider_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict) or not usage:
            return
        self.provider_reports += 1
        counts = run_tokens(usage)
        for key, value in counts.items():
            if value is None:
                self.complete_fields[key] = False
            else:
                self.reported_tokens[key] += value
        self.latest_context_tokens = context_tokens(usage)

    def observe_usage(self, usage: Any) -> None:
        if isinstance(usage, dict) and usage:
            self.usage = dict(usage)

    def finish(self) -> None:
        if self.completed_at is None:
            self.completed_at = time.time()
            elapsed = time.monotonic() - self.started_monotonic
            self.duration_ms = max(0, round(elapsed * 1000))

    def snapshot(self) -> dict:
        self.finish()
        tokens = {'input_tokens': None, 'output_tokens': None, 'total_tokens': None}
        reports_complete = self.expected_provider_calls is None or self.provider_reports == self.expected_provider_calls
        if self.provider_reports and reports_complete:
            for key in tokens:
                if self.complete_fields[key]:
                    tokens[key] = self.reported_tokens[key]
        has_split = tokens['input_tokens'] is not None and tokens['output_tokens'] is not None
        snapshot = {
            'version': SNAPSHOT_VERSION,
            'run_id': self.run_id,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'duration_ms': self.duration_ms,
            'model_id': self.model_id,
            'status': self.status,
            'input_tokens': tokens['input_tokens'],
            'output_tokens': tokens['output_tokens'],
            'total_tokens': tokens['total_tokens'],
            'context_tokens': self.latest_context_tokens,
            'usage_source': 'unavailable',
            'usage_complete': has_split and reports_complete and self.status == 'completed',
        }
        if any(value is not None for value in tokens.values()):
            snapshot['usage_source'] = 'provider'
        if self.context_capacity is not None:
            snapshot['context_capacity'] = dict(self.context_capacity)
        return snapshot


def request_runs(request) -> dict:
    runs = getattr(request.state, REQUEST_STATE_KEY, None)
    if not isinstance(runs, dict):
        runs = {}
        setattr(request.state, REQUEST_STATE_KEY, runs)
    return runs


def discard_run(request, key) -> None:
    try:
        request_runs(request).pop(key, None)
    except Exception:
        log.warning('Local run tracking could not discard lifecycle state', exc_info=True)


def run_key(metadata: Any) -> tuple[str, str] | None:
    if not isinstance(metadata, dict):
        return None
    chat_id = metadata.get('chat_id')
    message_id = metadata.get('message_id')
    if not isinstance(chat_id, str) or not chat_id or not isinstance(message_id, str) or not message_id:
        return None
    return chat_id, message_id


def refresh_run_model(run: Run, request, form_data: dict, metadata: dict, model: dict) -> None:
    selected = metadata.get('selected_model_id') or form_data.get('model')
    models = getattr(request.app.state, 'MODELS', {})
    get_model = getattr(models, 'get', None)
    selected_model = get_model(selected) if callable(get_model) else None
    if isinstance(selected_model, dict):
        model = selected_model
    run.model_id = resolved_model_id(form_data, metadata, model)
    params = metadata.get('params') or form_data.get('params') or {}
    capacity = explicit_context_capacity(params, model)
    if capacity is not None:
        run.context_capacity = capacity


async def read_saved_meta(chat_id: str, message_id: str) -> dict:
    """Read metadata alone, avoiding message content and whole-chat history."""
    from sqlalchemy import select
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chat_messages import ChatMessage
    from open_webui.models.chats import Chat

    async with get_async_db_context() as session:
        statement = select(ChatMessage.meta).where(ChatMessage.id == f'{chat_id}-{message_id}')
        result = await session.execute(statement)
        row = result.first()
        if row is not None:
            return dict(row[0]) if isinstance(row[0], dict) else {}
        legacy_meta = Chat.chat[('history', 'messages', message_id, 'meta')]
        result = await session.execute(select(legacy_meta).where(Chat.id == chat_id))
        value = result.scalar_one_or_none()
        return dict(value) if isinstance(value, dict) else {}


def decorate_completion_event(event: Any, run: Run) -> Any:
    if not isinstance(event, dict):
        return event
    event_type = event.get('type')
    data = event.get('data')
    if event_type == 'chat:tasks:cancel':
        run.status = 'cancelled'
        return event
    if event_type in ('chat:message:error', 'chat:completion') and isinstance(data, dict) and data.get('error'):
        run.status = 'error'
    if event_type != 'chat:completion' or not isinstance(data, dict):
        return event
    run.observe_usage(data.get('usage'))
    if data.get('done') is not True:
        return event
    meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
    enriched_data = dict(data)
    enriched_data['meta'] = {**meta, 'local_run': run.snapshot()}
    return {**event, 'data': enriched_data}


def wrap_payload(original, original_upsert=None):
    @functools.wraps(original)
    async def process_chat_payload(request, form_data, user, metadata, model):
        run = None
        key = run_key(metadata)
        try:
            if key is not None:
                run = Run(key[0], key[1], resolved_model_id(form_data, metadata, model))
                run.context_capacity = explicit_context_capacity(metadata.get('params') or form_data.get('params'), model)
                request_runs(request)[key] = run
        except Exception:
            log.warning('Local run tracking could not start; using normal chat processing', exc_info=True)
        try:
            result = await original(request, form_data, user, metadata, model)
        except asyncio.CancelledError:
            if run is not None:
                await finish_pending_attempt(request, run, 'cancelled', original_upsert)
            raise
        except Exception:
            if run is not None:
                await finish_pending_attempt(request, run, 'error', original_upsert)
            raise
        except BaseException:
            if run is not None:
                discard_run(request, key)
            raise
        if run is not None:
            try:
                updated_form, updated_metadata, _events = result
                updated_key = run_key(updated_metadata)
                if updated_key is not None:
                    if updated_key != key:
                        discard_run(request, key)
                        run.chat_id, run.message_id = updated_key
                        request_runs(request)[updated_key] = run
                    refresh_run_model(run, request, updated_form, updated_metadata, model)
            except Exception:
                log.warning('Local run tracking could not update model metadata', exc_info=True)
        return result
    process_chat_payload.__local_usage_tracking__ = True
    return process_chat_payload


def wrap_response(original):
    @functools.wraps(original)
    async def process_chat_response(response, ctx):
        run = None
        request = ctx.get('request')
        key = run_key(ctx.get('metadata'))
        try:
            if request is not None and key is not None:
                run = request_runs(request).get(key)
        except Exception:
            log.warning('Local run tracking could not find its lifecycle state', exc_info=True)
        if run is None:
            return await original(response, ctx)
        if run.expected_provider_calls is None:
            run.expected_provider_calls = 1
        token = ACTIVE_RUN.set(run)
        emitter = ctx.get('event_emitter')
        tracked_ctx = dict(ctx)
        if emitter is not None:
            async def emit_with_run_metadata(event):
                enriched_event = event
                try:
                    enriched_event = decorate_completion_event(event, run)
                except Exception:
                    log.warning('Local run tracking left a completion event unchanged', exc_info=True)
                return await emitter(enriched_event)
            tracked_ctx['event_emitter'] = emit_with_run_metadata
        try:
            return await original(response, tracked_ctx)
        finally:
            ACTIVE_RUN.reset(token)
            discard_run(request, key)
    process_chat_response.__local_usage_tracking__ = True
    return process_chat_response


def wrap_upsert(original):
    @functools.wraps(original)
    async def upsert_message(id: str, message_id: str, message: dict, *, touch: bool = True):
        enriched_message = message
        run = ACTIVE_RUN.get()
        if run is not None and (id, message_id) == (run.chat_id, run.message_id):
            try:
                selected_model = message.get('selectedModelId')
                if isinstance(selected_model, str) and selected_model:
                    run.model_id = selected_model
                run.observe_usage(message.get('usage'))
                if message.get('error'):
                    run.status = 'error'
                if message.get('done') is True:
                    saved_meta = await read_saved_meta(id, message_id)
                    incoming_meta = message.get('meta') if isinstance(message.get('meta'), dict) else {}
                    enriched_message = dict(message)
                    enriched_message['meta'] = {**saved_meta, **incoming_meta, 'local_run': run.snapshot()}
            except Exception:
                log.warning('Local run tracking left a message write unchanged', exc_info=True)
        return await original(id, message_id, enriched_message, touch=touch)
    upsert_message.__local_usage_tracking__ = True
    return upsert_message



def wrap_merge_usage(original):
    @functools.wraps(original)
    def merge_usage(current, incoming):
        result = original(current, incoming)
        run = ACTIVE_RUN.get()
        if run is not None and run.completed_at is None:
            try:
                run.observe_provider_usage(incoming)
            except Exception:
                log.warning('Local run tracking could not inspect provider counts', exc_info=True)
        return result
    merge_usage.__local_usage_tracking__ = True
    return merge_usage


def wrap_normalize_usage(original):
    @functools.wraps(original)
    def normalize_usage(usage):
        result = original(usage)
        run = ACTIVE_RUN.get()
        if run is not None and run.completed_at is None and not run.nonstream_parsed:
            try:
                run.observe_provider_usage(usage)
            except Exception:
                log.warning('Local run tracking could not inspect provider counts', exc_info=True)
        return result
    normalize_usage.__local_usage_tracking__ = True
    return normalize_usage



def wrap_response_data(original):
    @functools.wraps(original)
    def get_response_data(response):
        result = original(response)
        run = ACTIVE_RUN.get()
        if run is not None:
            try:
                response_data = result[1]
                if isinstance(response_data, dict):
                    run.observe_provider_usage(response_data.get('usage'))
                    run.nonstream_parsed = True
                    provider_model = response_data.get('model')
                    if isinstance(provider_model, str) and provider_model:
                        run.model_id = provider_model
            except Exception:
                log.warning('Local run tracking could not inspect a non-stream response', exc_info=True)
        return result
    get_response_data.__local_usage_tracking__ = True
    return get_response_data



async def persist_attempt_snapshot(run: Run, original_upsert) -> None:
    """Metadata-only write: native content, status, and usage remain upstream's."""
    try:
        meta = await read_saved_meta(run.chat_id, run.message_id)
        message = {'meta': {**meta, 'local_run': run.snapshot()}}
        await original_upsert(run.chat_id, run.message_id, message, touch=False)
    except Exception:
        log.warning('Local run tracking could not save attempt metadata', exc_info=True)



async def finish_pending_attempt(request, run: Run, status: str, original_upsert) -> None:
    run.status = status
    try:
        if original_upsert is not None:
            if status == 'cancelled':
                await asyncio.shield(persist_attempt_snapshot(run, original_upsert))
            else:
                await persist_attempt_snapshot(run, original_upsert)
    finally:
        discard_run(request, (run.chat_id, run.message_id))


def pending_run(request, form_data=None, metadata=None) -> Run | None:
    if metadata is None and isinstance(form_data, dict):
        metadata = form_data.get('metadata')
    key = run_key(metadata)
    if key is None:
        return None
    return request_runs(request).get(key)


def wrap_initial_provider(original, original_upsert):
    @functools.wraps(original)
    async def generate_chat_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        run = None
        try:
            run = pending_run(request, form_data)
            if run is not None:
                run.expected_provider_calls = 1
        except Exception:
            log.warning('Local run tracking could not inspect an initial model call', exc_info=True)
        try:
            result = await original(request, form_data, user, bypass_filter, bypass_system_prompt)
        except asyncio.CancelledError:
            if run is not None:
                await finish_pending_attempt(request, run, 'cancelled', original_upsert)
            raise
        except Exception:
            if run is not None:
                await finish_pending_attempt(request, run, 'error', original_upsert)
            raise
        status_code = token_count(getattr(result, 'status_code', None))
        if run is not None and status_code is not None and status_code >= 400:
            await finish_pending_attempt(request, run, 'error', original_upsert)
        return result
    generate_chat_completion.__local_usage_tracking__ = True
    return generate_chat_completion


def wrap_tool_drain(original, original_upsert):
    @functools.wraps(original)
    async def drain_approved_tool_calls(request, form_data, user, model, metadata):
        run = None
        try:
            run = pending_run(request, metadata=metadata)
        except Exception:
            log.warning('Local run tracking could not inspect a tool approval attempt', exc_info=True)
        try:
            paused = await original(request, form_data, user, model, metadata)
        except asyncio.CancelledError:
            if run is not None:
                await finish_pending_attempt(request, run, 'cancelled', original_upsert)
            raise
        except Exception:
            if run is not None:
                await finish_pending_attempt(request, run, 'error', original_upsert)
            raise
        if paused and run is not None:
            await finish_pending_attempt(request, run, 'paused', original_upsert)
        return paused
    drain_approved_tool_calls.__local_usage_tracking__ = True
    return drain_approved_tool_calls


def wrap_tool_provider(original):
    @functools.wraps(original)
    async def generate_chat_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        run = ACTIVE_RUN.get()
        if run is not None and run.completed_at is None:
            if run.expected_provider_calls is None:
                run.expected_provider_calls = 1
            run.expected_provider_calls += 1
        return await original(request, form_data, user, bypass_filter, bypass_system_prompt)
    generate_chat_completion.__local_usage_tracking__ = True
    return generate_chat_completion


def signature_matches(function, expected: list[str]) -> bool:
    try:
        return list(inspect.signature(function).parameters) == expected
    except (TypeError, ValueError):
        return False


def lifecycle_is_supported(main, middleware, chats) -> bool:
    if not signature_matches(main.process_chat_payload, ['request', 'form_data', 'user', 'metadata', 'model']):
        return False
    if not signature_matches(main.process_chat_response, ['response', 'ctx']):
        return False
    if not signature_matches(chats.upsert_message_to_chat_by_id_and_message_id, ['id', 'message_id', 'message', 'touch']):
        return False
    if not signature_matches(middleware.merge_usage, ['current', 'incoming']):
        return False
    if not signature_matches(middleware.normalize_usage, ['usage']):
        return False
    if not signature_matches(middleware.get_response_data, ['response']):
        return False
    provider_parameters = ['request', 'form_data', 'user', 'bypass_filter', 'bypass_system_prompt']
    if not signature_matches(main.chat_completion_handler, provider_parameters):
        return False
    if not signature_matches(middleware.generate_chat_completion, provider_parameters):
        return False
    if not signature_matches(main.drain_approved_tool_calls, ['request', 'form_data', 'user', 'model', 'metadata']):
        return False
    try:
        stream_source = inspect.getsource(middleware.streaming_chat_response_handler)
        nonstream_source = inspect.getsource(middleware.non_streaming_chat_response_handler)
    except (OSError, TypeError):
        return False
    required_stream_markers = ('usage = None', 'usage = merge_usage(usage,', "'usage': usage")
    for marker in required_stream_markers:
        if marker not in stream_source:
            return False
    return "normalize_usage(response_data.get('usage'" in nonstream_source


def apply() -> bool:
    """Install once; skip upgrades until the response lifecycle is verified."""
    try:
        main = importlib.import_module('open_webui.main')
        middleware = importlib.import_module('open_webui.utils.middleware')
        chats = importlib.import_module('open_webui.models.chats').Chats
        if getattr(main.process_chat_response, '__local_usage_tracking__', False):
            return True
        installed_version = package_version('open-webui')
        if installed_version not in SUPPORTED_VERSIONS or not lifecycle_is_supported(main, middleware, chats):
            log.warning('Skipping local run tracking: Open WebUI %s lifecycle has not been verified', installed_version)
            return False
        original_upsert = chats.upsert_message_to_chat_by_id_and_message_id
        initial_provider_wrapper = wrap_initial_provider(main.chat_completion_handler, original_upsert)
        tool_drain_wrapper = wrap_tool_drain(main.drain_approved_tool_calls, original_upsert)
        tool_provider_wrapper = wrap_tool_provider(middleware.generate_chat_completion)
        payload_wrapper = wrap_payload(main.process_chat_payload, original_upsert)
        response_wrapper = wrap_response(main.process_chat_response)
        upsert_wrapper = wrap_upsert(chats.upsert_message_to_chat_by_id_and_message_id)
        merge_wrapper = wrap_merge_usage(middleware.merge_usage)
        normalize_wrapper = wrap_normalize_usage(middleware.normalize_usage)
        response_data_wrapper = wrap_response_data(middleware.get_response_data)
        main.chat_completion_handler = initial_provider_wrapper
        main.drain_approved_tool_calls = tool_drain_wrapper
        middleware.drain_approved_tool_calls = tool_drain_wrapper
        middleware.generate_chat_completion = tool_provider_wrapper
        main.process_chat_payload = payload_wrapper
        middleware.process_chat_payload = payload_wrapper
        main.process_chat_response = response_wrapper
        middleware.process_chat_response = response_wrapper
        chats.upsert_message_to_chat_by_id_and_message_id = upsert_wrapper
        middleware.merge_usage = merge_wrapper
        middleware.normalize_usage = normalize_wrapper
        middleware.get_response_data = response_data_wrapper
        log.info('Local patch active: per-run provider tokens, timing, and configured context metadata')
        return True
    except Exception:
        log.warning('Local run tracking could not be installed; using normal chat processing', exc_info=True)
        return False
