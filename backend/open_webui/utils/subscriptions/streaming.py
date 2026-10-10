"""Convert provider turn events into OpenAI chat completion responses.

Open WebUI's chat pipeline reads these the same way it reads any OpenAI
compatible connection: content and reasoning deltas, ``usage`` chunks,
``error`` chunks, and ``event`` chunks for status lines.
"""

import json
import logging
import time
import uuid

from fastapi import HTTPException, status

from open_webui.utils.subscriptions.events import (
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
)

log = logging.getLogger(__name__)


def _sse(data: dict) -> str:
    return f'data: {json.dumps(data, ensure_ascii=False)}\n\n'


def _chunk(completion_id: str, model_id: str, delta: dict, finish_reason: str | None = None) -> dict:
    return {
        'id': completion_id,
        'object': 'chat.completion.chunk',
        'created': int(time.time()),
        'model': model_id,
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
    }


def _status_chunk(description: str, done: bool, hidden: bool = False) -> dict:
    data = {'description': description, 'done': done}
    if hidden:
        data['hidden'] = True
    return {'event': {'type': 'status', 'data': data}}


async def stream_events(events, model_id: str):
    """Yield server-sent events for a streaming chat completion."""
    completion_id = f'chatcmpl-{uuid.uuid4()}'
    status_shown = False
    try:
        async for event in events:
            if isinstance(event, TextDelta):
                yield _sse(_chunk(completion_id, model_id, {'content': event.text}))
            elif isinstance(event, ReasoningDelta):
                yield _sse(_chunk(completion_id, model_id, {'reasoning_content': event.text}))
            elif isinstance(event, StatusUpdate):
                status_shown = True
                yield _sse(_status_chunk(event.description, event.done))
            elif isinstance(event, TokenUsage):
                usage_chunk = _chunk(completion_id, model_id, {})
                usage_chunk['choices'] = []
                usage_chunk['usage'] = event.to_openai()
                yield _sse(usage_chunk)
            elif isinstance(event, TurnFailed):
                yield _sse({'error': {'message': event.message}})
    except SubscriptionError as error:
        yield _sse({'error': {'message': str(error)}})
    except Exception as error:
        log.exception('Subscription model turn failed')
        yield _sse({'error': {'message': f'The subscription model failed: {error}'}})

    if status_shown:
        yield _sse(_status_chunk('Done', done=True, hidden=True))
    yield _sse(_chunk(completion_id, model_id, {}, finish_reason='stop'))
    yield 'data: [DONE]\n\n'


async def collect_events(events, model_id: str) -> dict:
    """Run a turn to the end and return a non-streaming chat completion."""
    text_parts = []
    reasoning_parts = []
    usage = None
    try:
        async for event in events:
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
            elif isinstance(event, ReasoningDelta):
                reasoning_parts.append(event.text)
            elif isinstance(event, TokenUsage):
                usage = event.to_openai()
            elif isinstance(event, TurnFailed):
                raise SubscriptionError(event.message)
    except SubscriptionError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error))

    message = {'role': 'assistant', 'content': ''.join(text_parts)}
    if reasoning_parts:
        message['reasoning_content'] = ''.join(reasoning_parts)
    completion = {
        'id': f'chatcmpl-{uuid.uuid4()}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model_id,
        'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
    }
    if usage:
        completion['usage'] = usage
    return completion
