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
import os
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_FULL,
    CHAT_INSTRUCTIONS,
    ProviderModel,
    ProviderSettings,
    TurnRequest,
    model_key,
)
from open_webui.utils.subscriptions.conversation import ChatTurn, conversation_key
from open_webui.utils.subscriptions.events import (
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
)
from open_webui.utils.subscriptions.process import ChildProcess, ProcessClosedError, subscription_env

log = logging.getLogger(__name__)

PROVIDER_ID = 'codex'
NOT_INSTALLED_MESSAGE = (
    'The Codex CLI was not found. Install it with "npm install -g @openai/codex" '
    'or set its path in the ChatGPT subscription settings.'
)
NOT_SIGNED_IN_MESSAGE = 'ChatGPT is not signed in. Sign in under Admin Settings → Connections → Subscriptions.'
# Desktop-app features that add computer-use and browser tools to every thread,
# and hooks, which run commands outside the read-only sandbox.
DISABLED_FEATURES = ('plugins', 'apps', 'computer_use', 'browser_use', 'hooks')
LIVE_THREAD_LIMIT = 24
REQUEST_TIMEOUT = 60


class CodexRpcError(SubscriptionError):
    pass


def _npm_vendor_binary(bin_dir: Path) -> str | None:
    """npm installs a codex.cmd shim; the native binary sits in node_modules."""
    executable_name = 'codex.exe' if sys.platform == 'win32' else 'codex'
    package_dir = bin_dir / 'node_modules' / '@openai' / 'codex'
    patterns = [
        f'node_modules/@openai/codex-*/vendor/*/bin/{executable_name}',
        f'vendor/*/codex/{executable_name}',
    ]
    for pattern in patterns:
        matches = sorted(package_dir.glob(pattern))
        if matches:
            return str(matches[0])
    return None


def find_codex_cli(configured_path: str = '') -> str | None:
    if configured_path:
        if os.path.isfile(configured_path):
            return configured_path
        return None

    on_path = shutil.which('codex')
    if not on_path:
        return None
    if Path(on_path).suffix.lower() == '.exe':
        return on_path
    vendor_binary = _npm_vendor_binary(Path(on_path).parent)
    if vendor_binary:
        return vendor_binary
    return on_path


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


def _window_label(minutes) -> str:
    if not minutes:
        return 'Usage'
    if minutes == 10080:
        return 'Weekly'
    if minutes % 1440 == 0:
        return f'{minutes // 1440}-day'
    if minutes % 60 == 0:
        return f'{minutes // 60}-hour'
    return f'{minutes}-minute'


def summarize_rate_limits(snapshot: dict | None) -> dict | None:
    """Reduce Codex's rate-limit snapshot to what the settings page shows."""
    if not isinstance(snapshot, dict):
        return None
    windows = []
    for window_name in ('primary', 'secondary'):
        window = snapshot.get(window_name)
        if not isinstance(window, dict):
            continue
        windows.append(
            {
                'label': _window_label(window.get('windowDurationMins')),
                'used_percent': window.get('usedPercent'),
                'resets_at': window.get('resetsAt'),
            }
        )
    return {
        'plan': snapshot.get('planType'),
        'windows': windows,
        'limit_reached': bool(snapshot.get('rateLimitReachedType')),
    }


def history_items(history: list[ChatTurn]) -> list[dict]:
    """Earlier chat messages as Responses API items for thread/inject_items."""
    items = []
    for turn in history:
        if turn.role == 'user':
            content = [{'type': 'input_text', 'text': turn.text}]
            for url in turn.images:
                if url.startswith(('data:', 'http://', 'https://')):
                    content.append({'type': 'input_image', 'image_url': url})
        else:
            content = [{'type': 'output_text', 'text': turn.text}]
        items.append({'type': 'message', 'role': turn.role, 'content': content})
    return items


def user_input(turn: ChatTurn) -> list[dict]:
    inputs = [{'type': 'text', 'text': turn.text, 'text_elements': []}]
    for url in turn.images:
        if url.startswith(('data:', 'http://', 'https://')):
            inputs.append({'type': 'image', 'url': url})
    return inputs


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

    def __init__(self, cli: str, cwd: str, on_notification):
        self.cli = cli
        self._cwd = cwd
        self._on_notification = on_notification
        self._process: ChildProcess | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._thread_queues: dict[str, asyncio.Queue] = {}
        self._next_id = 0
        self._start_lock = asyncio.Lock()
        self.generation = 0

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
            self._process = ChildProcess(args, self._cwd, subscription_env())
            self.generation += 1
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

    async def _read_messages(self, process: ChildProcess) -> None:
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


@dataclass
class LiveThread:
    thread_id: str
    generation: int
    access: str
    cwd: str
    usage_total: dict | None


class CodexLogin:
    def __init__(self, method: str, login_id: str, url: str, user_code: str | None):
        self.method = method
        self.login_id = login_id
        self.url = url
        self.user_code = user_code
        self.state = 'waiting'
        self.message: str | None = None

    def to_dict(self) -> dict:
        return {
            'state': self.state,
            'method': self.method,
            'url': self.url,
            'user_code': self.user_code,
            'needs_code': False,
            'message': self.message,
        }


class CodexProvider:
    id = PROVIDER_ID
    name = 'ChatGPT'

    def __init__(self, chat_dir: Path):
        self._chat_dir = chat_dir
        self._server: CodexAppServer | None = None
        self._live_threads: OrderedDict[str, LiveThread] = OrderedDict()
        self._login: CodexLogin | None = None
        self._model_efforts: dict[str, list[str]] = {}
        self.rate_limits: dict | None = None

    def _on_notification(self, message: dict) -> None:
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'account/rateLimits/updated':
            self.rate_limits = summarize_rate_limits(params.get('rateLimits'))
        elif method == 'account/login/completed':
            login = self._login
            if not login or (params.get('loginId') and params.get('loginId') != login.login_id):
                return
            if params.get('success'):
                login.state = 'success'
                login.message = 'Signed in to ChatGPT.'
            else:
                login.state = 'error'
                login.message = params.get('error') or 'Sign-in did not finish.'

    def _cli_cwd(self) -> str:
        """The app-server starts in Buddy's empty chat folder."""
        self._chat_dir.mkdir(parents=True, exist_ok=True)
        return str(self._chat_dir)

    async def _server_for(self, settings: ProviderSettings) -> CodexAppServer:
        cli = find_codex_cli(settings.cli_path)
        if not cli:
            raise SubscriptionError(NOT_INSTALLED_MESSAGE)
        if self._server and self._server.cli != cli:
            self._server.stop()
            self._server = None
            self._live_threads.clear()
        if not self._server:
            self._server = CodexAppServer(cli, self._cli_cwd(), self._on_notification)
        await self._server.ensure_started()
        return self._server

    async def status(self, settings: ProviderSettings) -> dict:
        cli = find_codex_cli(settings.cli_path)
        if not cli:
            return {'installed': False, 'signed_in': False, 'message': NOT_INSTALLED_MESSAGE}
        try:
            server = await self._server_for(settings)
            account_info = await server.request('account/read', {}, timeout=30)
        except SubscriptionError as error:
            return {'installed': True, 'cli_path': cli, 'signed_in': False, 'message': str(error)}

        account = account_info.get('account') or {}
        status = {
            'installed': True,
            'cli_path': cli,
            'signed_in': bool(account),
            'account': {
                'email': account.get('email'),
                'plan': account.get('planType'),
                'auth_method': account.get('type'),
            },
        }
        if account.get('type') == 'apiKey':
            status['message'] = (
                'Codex is signed in with an API key, which bills API usage instead of a ChatGPT plan. '
                'Sign out and sign in with ChatGPT.'
            )
        if account:
            try:
                rate_limits = await server.request('account/rateLimits/read', None, timeout=30)
                self.rate_limits = summarize_rate_limits(rate_limits.get('rateLimits'))
            except SubscriptionError as error:
                log.info('Could not read Codex usage limits: %s', error)
        status['usage'] = self.rate_limits
        return status

    async def list_models(self, settings: ProviderSettings) -> list[ProviderModel]:
        server = await self._server_for(settings)
        result = await server.request('model/list', {}, timeout=30)
        models = []
        for entry in result.get('data') or []:
            if entry.get('hidden'):
                continue
            value = entry.get('id') or entry.get('model')
            if not value:
                continue
            efforts = [option.get('reasoningEffort') for option in entry.get('supportedReasoningEfforts') or []]
            efforts = [effort for effort in efforts if effort]
            self._model_efforts[value] = efforts
            models.append(
                ProviderModel(
                    key=model_key(value),
                    value=value,
                    name=entry.get('displayName') or value,
                    description=entry.get('description') or '',
                    efforts=efforts,
                    vision='image' in (entry.get('inputModalities') or ['image']),
                )
            )
        return models

    async def start_login(self, settings: ProviderSettings, method: str) -> dict:
        await self.cancel_login()
        server = await self._server_for(settings)
        if method == 'device':
            result = await server.request('account/login/start', {'type': 'chatgptDeviceCode'})
            login = CodexLogin('device', result.get('loginId'), result.get('verificationUrl'), result.get('userCode'))
        else:
            result = await server.request('account/login/start', {'type': 'chatgpt'})
            login = CodexLogin('browser', result.get('loginId'), result.get('authUrl'), None)
        self._login = login
        return login.to_dict()

    async def submit_login_code(self, code: str) -> dict:
        raise SubscriptionError('ChatGPT sign-in does not take a pasted code.')

    def login_state(self) -> dict:
        if not self._login:
            return {'state': 'idle'}
        return self._login.to_dict()

    async def cancel_login(self) -> None:
        login = self._login
        self._login = None
        if not login or login.state != 'waiting' or not self._server or not self._server.running:
            return
        try:
            await self._server.request('account/login/cancel', {'loginId': login.login_id}, timeout=10)
        except SubscriptionError as error:
            log.info('Could not cancel the ChatGPT sign-in: %s', error)

    async def logout(self, settings: ProviderSettings) -> None:
        await self.cancel_login()
        server = await self._server_for(settings)
        await server.request('account/logout', None)
        self._live_threads.clear()
        self.rate_limits = None

    def _effort(self, turn: TurnRequest) -> str | None:
        supported = self._model_efforts.get(turn.model) or []
        if turn.is_task or turn.effort in ('none', 'minimal'):
            if supported:
                return supported[0]
            return 'low'
        if turn.effort in supported:
            return turn.effort
        return None

    def _developer_instructions(self, turn: TurnRequest) -> str | None:
        system = turn.conversation.system
        if turn.access == ACCESS_CHAT:
            return f'{CHAT_INSTRUCTIONS}\n\n{system}'.strip()
        return system or None

    async def _start_thread(self, server: CodexAppServer, turn: TurnRequest) -> str:
        sandbox = 'read-only'
        if turn.access == ACCESS_FULL:
            sandbox = 'danger-full-access'
        params = {
            'cwd': turn.cwd,
            'model': turn.model,
            'ephemeral': True,
            'sandbox': sandbox,
            'approvalPolicy': 'never',
            'developerInstructions': self._developer_instructions(turn),
        }
        result = await server.request('thread/start', params)
        thread_id = result['thread']['id']
        if turn.conversation.history:
            items = history_items(turn.conversation.history)
            await server.request('thread/inject_items', {'threadId': thread_id, 'items': items})
        return thread_id

    def _take_live_thread(self, key: str, turn: TurnRequest, generation: int) -> LiveThread | None:
        live = self._live_threads.pop(key, None)
        if not live:
            return None
        if live.generation != generation or live.access != turn.access or live.cwd != turn.cwd:
            return None
        return live

    def _keep_live_thread(self, key: str, live: LiveThread) -> None:
        self._live_threads[key] = live
        while len(self._live_threads) > LIVE_THREAD_LIMIT:
            _, oldest = self._live_threads.popitem(last=False)
            self._release_thread(oldest.thread_id)

    def _release_thread(self, thread_id: str) -> None:
        server = self._server
        if not server or not server.running:
            return

        async def unsubscribe():
            try:
                await server.request('thread/unsubscribe', {'threadId': thread_id}, timeout=10)
            except SubscriptionError as error:
                log.debug('Could not unload Codex thread %s: %s', thread_id, error)

        asyncio.create_task(unsubscribe())

    def _interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        server = self._server
        if not server or not server.running:
            return

        async def interrupt():
            try:
                await server.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=10)
            except SubscriptionError as error:
                log.debug('Could not interrupt Codex turn %s: %s', turn_id, error)
            self._release_thread(thread_id)

        asyncio.create_task(interrupt())

    async def run_turn(self, turn: TurnRequest):
        """Run one chat turn and yield its events."""
        server = await self._server_for(turn.settings)
        conversation = turn.conversation
        history_key = conversation_key(conversation.history, conversation.system)

        live = None
        if not turn.is_task:
            live = self._take_live_thread(history_key, turn, server.generation)
        if live:
            thread_id = live.thread_id
            previous_usage = live.usage_total
        else:
            thread_id = await self._start_thread(server, turn)
            previous_usage = None

        queue = server.subscribe(thread_id)
        parser = None
        completed = False
        try:
            params = {'threadId': thread_id, 'input': user_input(conversation.prompt), 'model': turn.model}
            effort = self._effort(turn)
            if effort:
                params['effort'] = effort
            result = await server.request('turn/start', params)
            parser = CodexTurnParser(result['turn']['id'])

            while not parser.finished:
                message = await queue.get()
                if message is None:
                    raise SubscriptionError(f'Codex stopped while answering. {server.stderr_text()}'.strip())
                for event in parser.handle(message):
                    yield event

            usage = _usage_difference(parser.usage_total, previous_usage)
            if usage:
                yield usage
            if parser.error:
                yield TurnFailed(parser.error)
            else:
                completed = True
        finally:
            server.unsubscribe(thread_id)
            if completed and not turn.is_task:
                answered = conversation.history + [conversation.prompt, ChatTurn('assistant', parser.reply_text)]
                next_key = conversation_key(answered, conversation.system)
                live = LiveThread(thread_id, server.generation, turn.access, turn.cwd, parser.usage_total)
                self._keep_live_thread(next_key, live)
            elif parser and not parser.finished:
                self._interrupt_turn(thread_id, parser.turn_id)
            else:
                self._release_thread(thread_id)

    def stop(self) -> None:
        if self._server:
            self._server.stop()
        self._server = None
        self._live_threads.clear()
