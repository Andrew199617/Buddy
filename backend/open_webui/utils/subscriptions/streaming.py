"""Convert provider turn events into OpenAI chat completion responses.

Open WebUI's chat pipeline reads these the same way it reads any OpenAI
compatible connection: content and reasoning deltas, ``usage`` chunks,
``error`` chunks, and ``event`` chunks for status lines and Ultra workers.
"""

import dataclasses
import json
import logging
import time
import uuid

from fastapi import HTTPException, status

from open_webui.utils.subscriptions.events import (
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_RUNNING,
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
    WorkerUpdate,
)

log = logging.getLogger(__name__)

WORKERS_EVENT_TYPE = 'chat:message:workers'
# Seconds between snapshots that only change what workers are doing.
WORKER_SNAPSHOT_INTERVAL = 1.0


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


async def failure_events(message: str):
    """A turn that fails before it starts, for errors found while preparing it."""
    yield TurnFailed(message)


def _worker_status(worker: WorkerUpdate) -> str:
    """The status line for a worker that started or ended."""
    if worker.state == WORKER_RUNNING:
        return f'Worker started: {worker.label}'
    if worker.state == WORKER_DONE:
        return f'Worker finished: {worker.label}'
    if worker.state == WORKER_FAILED:
        return f'Worker failed: {worker.label}'
    return f'Worker stopped: {worker.label}'


class WorkerSnapshots:
    """The latest state of every Ultra worker in one reply, sent to the chat as one snapshot.

    Each snapshot lists every worker so far, in the order they started, so the
    chat replaces its copy rather than adding to it. A worker starting or ending
    is sent at once; changes to what a worker is doing are sent at most once a
    second, since a busy worker reports many of them.
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._workers = {}
        self._started_at = {}
        self._ended_at = {}
        self._state_changed = False
        self._activity_changed = False
        self._last_sent_at = 0.0

    def update(self, worker: WorkerUpdate) -> bool:
        """Record a worker's latest state, and return True when it started or its state changed."""
        previous = self._workers.get(worker.worker_id)
        # A copy, so a parser that changes and resends one update object still has its changes seen.
        self._workers[worker.worker_id] = dataclasses.replace(worker)
        if previous is not None and previous.state == worker.state:
            if previous != worker:
                self._activity_changed = True
            return False

        now = int(self._clock())
        self._started_at.setdefault(worker.worker_id, now)
        if worker.state == WORKER_RUNNING:
            self._ended_at.pop(worker.worker_id, None)
        else:
            self._ended_at[worker.worker_id] = now
        self._state_changed = True
        return True

    def ready(self) -> bool:
        """Whether a snapshot should be sent now."""
        if self._state_changed:
            return True
        if not self._activity_changed:
            return False
        return self._clock() - self._last_sent_at >= WORKER_SNAPSHOT_INTERVAL

    def unsent(self) -> bool:
        """Whether any change has not been sent yet."""
        return self._state_changed or self._activity_changed

    def snapshot(self) -> dict:
        """The snapshot event for every worker so far; its changes then count as sent."""
        self._state_changed = False
        self._activity_changed = False
        self._last_sent_at = self._clock()
        items = []
        running = 0
        for worker in self._workers.values():
            items.append(self._item(worker))
            if worker.state == WORKER_RUNNING:
                running += 1
        return {'event': {'type': WORKERS_EVENT_TYPE, 'data': {'items': items, 'running': running}}}

    def _item(self, worker: WorkerUpdate) -> dict:
        return {
            'id': worker.worker_id,
            'parent_id': worker.parent_id,
            'depth': worker.depth,
            'label': worker.label,
            'state': worker.state,
            'model': worker.model,
            'effort': worker.effort,
            'activity': worker.activity,
            'summary': worker.summary,
            'tokens': worker.tokens,
            'started_at': self._started_at[worker.worker_id],
            'ended_at': self._ended_at.get(worker.worker_id),
        }


async def stream_events(events, model_id: str):
    """Yield server-sent events for a streaming chat completion."""
    completion_id = f'chatcmpl-{uuid.uuid4()}'
    status_shown = False
    workers = WorkerSnapshots()
    try:
        async for event in events:
            if isinstance(event, TextDelta):
                yield _sse(_chunk(completion_id, model_id, {'content': event.text}))
            elif isinstance(event, ReasoningDelta):
                yield _sse(_chunk(completion_id, model_id, {'reasoning_content': event.text}))
            elif isinstance(event, StatusUpdate):
                status_shown = True
                yield _sse(_status_chunk(event.description, event.done))
            elif isinstance(event, WorkerUpdate):
                if workers.update(event):
                    # Shown before the chat has a workers card to show it in.
                    status_shown = True
                    yield _sse(_status_chunk(_worker_status(event), done=False))
            elif isinstance(event, TokenUsage):
                usage_chunk = _chunk(completion_id, model_id, {})
                usage_chunk['choices'] = []
                usage_chunk['usage'] = event.to_openai()
                yield _sse(usage_chunk)
            elif isinstance(event, TurnFailed):
                yield _sse({'error': {'message': event.message}})

            # Checked after every event, so a held activity change goes out once its second has passed.
            if workers.ready():
                yield _sse(workers.snapshot())
    except SubscriptionError as error:
        yield _sse({'error': {'message': str(error)}})
    except Exception as error:
        log.exception('Subscription model turn failed')
        yield _sse({'error': {'message': f'The subscription model failed: {error}'}})
    finally:
        # The chat closes this stream when Stop lands while it is handing on a
        # chunk. The turn is closed right here, so it ends before Stop returns,
        # and an Ultra turn stops its workers. Left for Python's garbage
        # collector to close, it would end later, in a task nobody waits for.
        await events.aclose()

    if workers.unsent():
        yield _sse(workers.snapshot())
    if status_shown:
        yield _sse(_status_chunk('Done', done=True, hidden=True))
    yield _sse(_chunk(completion_id, model_id, {}, finish_reason='stop'))
    yield 'data: [DONE]\n\n'


async def collect_events(events, model_id: str) -> dict:
    """Run a turn to the end and return a non-streaming chat completion.

    Status lines and Ultra worker updates only show while a reply streams, so
    they are left out.
    """
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
