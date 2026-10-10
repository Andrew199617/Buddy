"""OpenAI models through the Codex CLI, billed to the user's ChatGPT plan.

Buddy runs one ``codex app-server`` process and talks JSON-RPC to it over
stdio, the same interface the Codex IDE extensions use. Codex signs in with
"Sign in with ChatGPT" and keeps its credentials in its own config folder.

Chats use in-memory (ephemeral) Codex threads. A thread stays loaded while the
chat continues; any other state is rebuilt by injecting the chat's earlier
messages into a new thread.
"""

import asyncio
import json
import logging

from open_webui.utils.subscriptions.events import (
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
)
from open_webui.utils.subscriptions.process import ProcessClosedError

log = logging.getLogger(__name__)

# Desktop-app features that add computer-use and browser tools to every thread,
# and hooks, which run commands outside the read-only sandbox.
DISABLED_FEATURES = ('plugins', 'apps', 'computer_use', 'browser_use', 'hooks')
REQUEST_TIMEOUT = 60


class CodexRpcError(SubscriptionError):
    pass


def readable_codex_error(message: str) -> str:
    """Codex forwards API errors as JSON text; keep only the human message."""
    text = str(message or '').strip()
    if text.startswith('{'):
        try:
            payload = json.loads(text)
        except ValueError:
            return text
        error = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get('message'):
            return str(error['message'])
        if isinstance(payload, dict) and payload.get('message'):
            return str(payload['message'])
    return text


def _shorten(text, limit: int = 160) -> str:
    text = ' '.join(str(text or '').split())
    if len(text) > limit:
        return text[: limit - 1] + '…'
    return text


def _usage_difference(total: dict | None, previous: dict | None) -> TokenUsage | None:
    if not isinstance(total, dict):
        return None
    previous = previous or {}

    def counted(name: str) -> int:
        return max(0, int(total.get(name) or 0) - int(previous.get(name) or 0))

    return TokenUsage(
        input_tokens=counted('inputTokens'),
        output_tokens=counted('outputTokens'),
        cached_input_tokens=counted('cachedInputTokens'),
        reasoning_tokens=counted('reasoningOutputTokens'),
    )


class CodexTurnParser:
    """Turns app-server notifications for one turn into turn events."""

    def __init__(self, turn_id: str):
        self.turn_id = turn_id
        self.reply_text = ''
        self.error: str | None = None
        self.finished = False
        self.usage_total: dict | None = None
        self._reasoning_text = ''
        self._streamed_message_ids = set()
        self._new_message = False
        self._new_reasoning = False

    def handle(self, message: dict) -> list:
        method = message.get('method')
        params = message.get('params') or {}
        turn_id = params.get('turnId') or (params.get('turn') or {}).get('id')
        if turn_id and turn_id != self.turn_id:
            return []

        if method == 'item/agentMessage/delta':
            self._streamed_message_ids.add(params.get('itemId'))
            return self._text(params.get('delta', ''))
        if method == 'item/reasoning/summaryTextDelta':
            return self._reasoning(params.get('delta', ''))
        if method == 'item/reasoning/textDelta':
            return self._reasoning(params.get('delta', ''))
        if method == 'item/reasoning/summaryPartAdded':
            self._new_reasoning = True
            return []
        if method == 'item/started':
            return self._item_started(params.get('item') or {})
        if method == 'item/completed':
            return self._item_completed(params.get('item') or {})
        if method == 'thread/tokenUsage/updated':
            self.usage_total = (params.get('tokenUsage') or {}).get('total')
            return []
        if method == 'error':
            return self._error(params)
        if method == 'turn/completed':
            return self._turn_completed(params.get('turn') or {})
        return []

    def _text(self, text: str) -> list:
        if not text:
            return []
        if self._new_message:
            self._new_message = False
            if self.reply_text and not self.reply_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self.reply_text += text
        return [TextDelta(text)]

    def _reasoning(self, text: str) -> list:
        if not text:
            return []
        if self._new_reasoning:
            self._new_reasoning = False
            if self._reasoning_text and not self._reasoning_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self._reasoning_text += text
        return [ReasoningDelta(text)]

    def _activity(self, description: str) -> list:
        self._new_reasoning = True
        return [StatusUpdate(description), *self._reasoning(f'*{description}*')]

    def _item_started(self, item: dict) -> list:
        item_type = item.get('type')
        if item_type == 'agentMessage':
            self._new_message = True
            return []
        if item_type == 'reasoning':
            self._new_reasoning = True
            return []
        if item_type == 'commandExecution':
            return self._activity(f'Running `{_shorten(item.get("command"))}`')
        if item_type == 'fileChange':
            return self._activity('Editing files')
        if item_type == 'webSearch':
            return self._activity(f'Searching the web for {_shorten(item.get("query"), 100)}')
        if item_type == 'mcpToolCall':
            return self._activity(f'Using {item.get("server")}.{item.get("tool")}')
        return []

    def _item_completed(self, item: dict) -> list:
        item_type = item.get('type')
        if item_type == 'agentMessage' and item.get('id') not in self._streamed_message_ids:
            # No deltas arrived for this message; show its full text.
            self._new_message = True
            return self._text(item.get('text', ''))
        if item_type == 'commandExecution' and item.get('exitCode') not in (None, 0):
            return self._reasoning(f' (exit code {item.get("exitCode")})')
        if item_type == 'fileChange':
            paths = [str(change.get('path')) for change in item.get('changes') or [] if change.get('path')]
            if paths:
                self._new_reasoning = True
                return self._reasoning('Edited ' + ', '.join(paths))
        return []

    def _error(self, params: dict) -> list:
        message = readable_codex_error((params.get('error') or {}).get('message'))
        if params.get('willRetry'):
            return [StatusUpdate(f'Retrying: {_shorten(message, 120)}')]
        self.error = message or 'Codex reported an error.'
        return []

    def _turn_completed(self, turn: dict) -> list:
        self.finished = True
        status = turn.get('status')
        if status == 'failed' and not self.error:
            error = turn.get('error') or {}
            self.error = readable_codex_error(error.get('message')) or 'The Codex turn failed.'
        elif status == 'interrupted' and not self.error:
            self.error = 'The reply was stopped.'
        return []


class CodexAppServer:
    """One ``codex app-server`` process and its JSON-RPC traffic."""

    def __init__(self, machine, cli: str, cwd: str, on_notification):
        self.machine = machine
        self.cli = cli
        self._cwd = cwd
        self._on_notification = on_notification
        self._process = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._thread_queues: dict[str, asyncio.Queue] = {}
        self._next_id = 0
        self._start_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self.running:
                return
            args = [self.cli, 'app-server']
            for feature in DISABLED_FEATURES:
                args.extend(['--disable', feature])
            self._process = await self.machine.start_process(args, self._cwd)
            self._reader = asyncio.create_task(self._read_messages(self._process))
            client_info = {'name': 'buddy', 'title': 'Buddy', 'version': '1.0'}
            try:
                await self._send_request('initialize', {'clientInfo': client_info}, REQUEST_TIMEOUT)
                await self._send({'method': 'initialized'})
            except SubscriptionError as error:
                self.stop()
                raise SubscriptionError(f'Codex did not start: {error}') from error

    async def request(self, method: str, params: dict | None = None, timeout: float = REQUEST_TIMEOUT):
        await self.ensure_started()
        return await self._send_request(method, params, timeout)

    async def _send(self, message: dict) -> None:
        if not self._process:
            raise SubscriptionError('Codex is not running.')
        await self._process.write(json.dumps(message) + '\n')

    async def _send_request(self, method: str, params: dict | None, timeout: float):
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message = {'id': request_id, 'method': method}
        if params is not None:
            message['params'] = params
        try:
            await self._send(message)
            return await asyncio.wait_for(future, timeout)
        except ProcessClosedError as error:
            raise SubscriptionError(f'Codex stopped unexpectedly. {self.stderr_text()}'.strip()) from error
        except TimeoutError as error:
            raise SubscriptionError(f'Codex did not answer {method} within {timeout:.0f} seconds.') from error
        finally:
            self._pending.pop(request_id, None)

    def stderr_text(self) -> str:
        if not self._process:
            return ''
        return _shorten(self._process.stderr_text(), 600)

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._thread_queues[thread_id] = queue
        return queue

    def unsubscribe(self, thread_id: str) -> None:
        self._thread_queues.pop(thread_id, None)

    async def _read_messages(self, process) -> None:
        while True:
            line = await process.read_line()
            if line is None:
                break
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'method' in message and 'id' in message:
                await self._decline_server_request(message)
            elif 'method' in message:
                self._dispatch_notification(message)
            elif 'id' in message:
                self._resolve_response(message)

        ended = SubscriptionError(f'Codex stopped unexpectedly. {self.stderr_text()}'.strip())
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ended)
        for queue in list(self._thread_queues.values()):
            queue.put_nowait(None)

    def _resolve_response(self, message: dict) -> None:
        future = self._pending.get(message.get('id'))
        if not future or future.done():
            return
        error = message.get('error')
        if error:
            future.set_exception(CodexRpcError(readable_codex_error(error.get('message'))))
        else:
            future.set_result(message.get('result') or {})

    def _dispatch_notification(self, message: dict) -> None:
        params = message.get('params') or {}
        queue = self._thread_queues.get(params.get('threadId'))
        if queue:
            queue.put_nowait(message)
        try:
            self._on_notification(message)
        except Exception:
            log.exception('Codex notification handler failed')

    async def _decline_server_request(self, message: dict) -> None:
        # Buddy starts threads with approvals turned off, so Codex should not
        # ask anything. Refuse unexpected requests so a turn cannot hang.
        log.info('Declining Codex request %s', message.get('method'))
        reply = {
            'id': message['id'],
            'error': {'code': -32601, 'message': 'Buddy does not answer this request.'},
        }
        try:
            await self._send(reply)
        except (ProcessClosedError, SubscriptionError):
            pass

    def stop(self) -> None:
        if self._process:
            self._process.close_stdin()
            self._process.kill()
        self._process = None
