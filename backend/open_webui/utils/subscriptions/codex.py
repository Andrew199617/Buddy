"""OpenAI models through the Codex CLI, billed to the user's ChatGPT plan.

Buddy runs one ``codex app-server`` process per machine (this server or a
Buddy Runner) and talks JSON-RPC to it over its stdio, which a Buddy Runner
relays over its connection. This is the same interface the Codex IDE
extensions use. Codex signs in with "Sign in with ChatGPT" and keeps its
credentials in its own config folder on that machine.

Chats use in-memory (ephemeral) Codex threads. A thread stays loaded while the
chat continues; any other state is rebuilt by injecting the chat's earlier
messages into a new thread. Ultra turns use persistent threads, which Codex also
saves in its own sessions folder, because Codex can't start workers from an
ephemeral thread.
"""

import asyncio
import dataclasses
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlsplit

from open_webui.utils.subscriptions import ultra
from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_FULL,
    CHAT_INSTRUCTIONS,
    CREDENTIAL_API_KEY,
    CREDENTIAL_MACHINE,
    ProviderModel,
    ProviderSettings,
    TurnRequest,
    UltraOptions,
    model_key,
)
from open_webui.utils.subscriptions.conversation import ChatTurn, conversation_key
from open_webui.utils.subscriptions.events import (
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_RUNNING,
    WORKER_STOPPED,
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
    WorkerUpdate,
)
from buddy_runner.discovery import TOOL_CODEX
from buddy_runner.process import OutputLimitError, ProcessClosedError

log = logging.getLogger(__name__)

PROVIDER_ID = 'codex'
NOT_INSTALLED_MESSAGE = (
    'The Codex CLI was not found. Install it with "npm install -g @openai/codex" '
    'or set its path in the ChatGPT subscription settings.'
)
# Over-long output lines are skipped, so this happens only when Codex's output
# piles up on a Runner while Buddy's reader is held up.
OUTPUT_BACKLOG_MESSAGE = (
    "Buddy fell behind reading Codex's output from its machine, so Buddy stopped Codex. Try again."
)
# Desktop-app features that add computer-use and browser tools to every thread,
# and hooks, which run commands outside the read-only sandbox.
DISABLED_FEATURES = ('plugins', 'apps', 'computer_use', 'browser_use', 'hooks')
LIVE_THREAD_LIMIT = 24
REQUEST_TIMEOUT = 60
# ChatGPT sign-in links go to these hosts or their subdomains.
SIGN_IN_HOSTS = ('openai.com', 'chatgpt.com')
UNEXPECTED_SIGN_IN_URL_MESSAGE = (
    'Codex gave a sign-in link that does not go to OpenAI, so Buddy did not show it. '
    'Check the Codex CLI on the selected computer.'
)
# The first segment of every agent path. An Ultra turn's orchestrator is /root,
# its workers add a segment each, such as /root/research, and their
# sub-workers one more.
ROOT_AGENT = 'root'
# How long Buddy follows an Ultra turn. Workers may outlive the orchestrator's
# reply and take hours; this only ends a turn that never would.
ULTRA_TIME_LIMIT_HOURS = 6
ULTRA_TIME_LIMIT = ULTRA_TIME_LIMIT_HOURS * 60 * 60
ULTRA_TIME_LIMIT_MESSAGE = f'Buddy stopped this Ultra reply after {ULTRA_TIME_LIMIT_HOURS} hours.'
# A worker's report is kept to this many characters, since every snapshot of the
# turn's workers carries it.
WORKER_SUMMARY_LIMIT = 4000
# The summary of a worker that was still running when its app-server ended.
WORKER_ENDED_WITH_CODEX = 'Stopped when Codex stopped.'


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


def is_chatgpt_sign_in_url(url) -> bool:
    """Whether ``url`` is an https link to an OpenAI or ChatGPT host.

    The app-server may run on a Buddy Runner, which Buddy trusts less than
    itself, and the admin's browser opens this link.
    """
    if not isinstance(url, str):
        return False
    # Browsers read backslashes, spaces, and control characters in a URL
    # differently than urlsplit does, so a link with any of them is refused.
    if '\\' in url or ' ' in url or not url.isprintable():
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
    except ValueError:
        return False
    if parts.scheme != 'https' or '@' in parts.netloc:
        return False
    for allowed_host in SIGN_IN_HOSTS:
        if host == allowed_host or host.endswith('.' + allowed_host):
            return True
    return False


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


def turn_input(turn: TurnRequest) -> list[dict]:
    """The input of a turn's turn/start: the person's message, and ULTRA_TURN_NOTE on Ultra turns.

    The note is a text item of its own, added here so the chat never holds it.
    """
    inputs = user_input(turn.conversation.prompt)
    if turn.ultra is not None:
        inputs.append({'type': 'text', 'text': ultra.ULTRA_TURN_NOTE, 'text_elements': []})
    return inputs


def _sends_effort(turn: TurnRequest) -> bool:
    """Whether a turn sends Codex a thinking level, which must be one its model offers."""
    if turn.effort or turn.is_task:
        return True
    # The person may choose a level for an Ultra turn's workers alone.
    return turn.ultra is not None and bool(turn.ultra.worker_effort)


def _shorten(text, limit: int = 160) -> str:
    text = ' '.join(str(text or '').split())
    if len(text) > limit:
        return text[: limit - 1] + '…'
    return text


def _clip(text, limit: int) -> str:
    """Text cut to at most ``limit`` characters, keeping its lines."""
    text = str(text or '').strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + '…'
    return text


def describe_item(item: dict) -> str | None:
    """One line describing a command, file edit, web search or tool call that a thread starts; None for others."""
    item_type = item.get('type')
    if item_type == 'commandExecution':
        return f'Running `{_shorten(item.get("command"))}`'
    if item_type == 'fileChange':
        return 'Editing files'
    if item_type == 'webSearch':
        return f'Searching the web for {_shorten(item.get("query"), 100)}'
    if item_type == 'mcpToolCall':
        return f'Using {item.get("server")}.{item.get("tool")}'
    return None


def started_worker_thread(params: dict) -> str | None:
    """The thread of the worker that a subAgentActivity item reports started, or None for any other notification."""
    item = params.get('item') or {}
    if item.get('type') != 'subAgentActivity' or item.get('kind') != 'started':
        return None
    return item.get('agentThreadId') or None


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


def _usage_sum(first: TokenUsage | None, second: TokenUsage | None) -> TokenUsage | None:
    """Two usages added up, where None is no usage reported."""
    if first is None:
        return second
    if second is None:
        return first
    return TokenUsage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cached_input_tokens=first.cached_input_tokens + second.cached_input_tokens,
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
    )


def _agent_path_segments(path) -> tuple[str, ...]:
    """The segments of an agent path below /root, such as ('research', 'tests') for /root/research/tests."""
    segments = tuple(segment for segment in str(path or '').split('/') if segment)
    if segments and segments[0] == ROOT_AGENT:
        return segments[1:]
    return segments


def _worker_label(segments: tuple[str, ...]) -> str:
    """A worker's name in the list: the last segment of its path, made readable, such as 'Rivers essay'."""
    if not segments:
        return 'Worker'
    words = _shorten(segments[-1].replace('_', ' ').replace('-', ' '), 100)
    if not words:
        return 'Worker'
    return words[0].upper() + words[1:]


def _worker_state(status) -> str | None:
    """The state of a worker whose turn ended with this Codex status, or None for a turn that has not ended."""
    if status == 'completed':
        return WORKER_DONE
    if status == 'interrupted':
        return WORKER_STOPPED
    if status == 'failed':
        return WORKER_FAILED
    return None


def _worker_activity(item: dict) -> str | None:
    """What a worker is doing once its thread starts this item, in one short line, or None to keep the last one."""
    item_type = item.get('type')
    if item_type == 'reasoning':
        return 'Thinking'
    if item_type == 'agentMessage':
        return 'Writing'
    if item_type == 'collabAgentToolCall' and item.get('tool') == 'wait':
        return 'Waiting for its workers'
    return describe_item(item)


class CodexWorkers:
    """The workers of one Ultra turn, followed through Codex's notifications.

    The thread that starts a worker reports it with subAgentActivity items:
    kind 'started', with the worker's own thread and its agent path, such as
    /root/rivers_essay, and kind 'completed' when a turn of the worker ends.
    The orchestrator's thread reports its workers, and each worker's thread
    the sub-workers it starts. Each worker's thread reports its own turns
    under its own thread id: turn/started, its items, its token usage, and
    turn/completed with how the turn ended. A worker's id is its thread id.

    A worker's depth is the number of segments of its path below /root, and
    its parent is the worker whose path is the rest of it.
    """

    def __init__(self, options: UltraOptions):
        self._options = options
        self._workers: dict[str, WorkerUpdate] = {}
        # The thread of each worker, by the segments of its agent path.
        self._threads_by_path: dict[tuple[str, ...], str] = {}
        # Each worker's latest turn, from its own thread's turn/started.
        self._turns: dict[str, str] = {}
        # Each worker thread's latest token usage total.
        self._usage_totals: dict[str, dict] = {}

    def running(self) -> bool:
        """Whether any worker has not ended yet."""
        for worker in self._workers.values():
            if worker.state == WORKER_RUNNING:
                return True
        return False

    def usage_totals(self) -> list[dict]:
        """Each worker thread's latest token usage total.

        A worker's thread starts with the worker, so its total counts
        everything the worker used.
        """
        return list(self._usage_totals.values())

    def sub_agent_activity(self, item: dict) -> list:
        """WorkerUpdate events for a subAgentActivity item from the orchestrator's thread or a worker's."""
        kind = item.get('kind')
        if kind == 'started':
            return self._started(item)
        if kind == 'completed':
            return self._completed(item)
        return []

    def handle(self, message: dict) -> list:
        """WorkerUpdate events for one notification from a worker's own thread."""
        method = message.get('method')
        params = message.get('params') or {}
        worker_id = params.get('threadId')
        if worker_id not in self._workers:
            return []
        if method == 'turn/started':
            return self._turn_started(worker_id, params.get('turn') or {})
        if method == 'item/started':
            return self._item_started(worker_id, params.get('item') or {})
        if method == 'item/completed':
            return self._item_completed(worker_id, params.get('item') or {})
        if method == 'thread/tokenUsage/updated':
            return self._usage_updated(worker_id, params.get('tokenUsage') or {})
        if method == 'turn/completed':
            return self._turn_completed(worker_id, params.get('turn') or {})
        return []

    def stop_running(self) -> list:
        """Every worker still running, as stopped: they end with their app-server."""
        events = []
        for worker_id, worker in list(self._workers.items()):
            if worker.state == WORKER_RUNNING:
                events.extend(
                    self._change(worker_id, state=WORKER_STOPPED, activity=None, summary=WORKER_ENDED_WITH_CODEX)
                )
        return events

    def _change(self, worker_id: str, **changes) -> list:
        """Apply changes to a worker, and report it when anything changed.

        Each change makes a new WorkerUpdate, so an update already reported
        keeps what it said.
        """
        worker = self._workers[worker_id]
        changed = dataclasses.replace(worker, **changes)
        if changed == worker:
            return []
        self._workers[worker_id] = changed
        return [changed]

    def _parent(self, segments: tuple[str, ...]) -> str | None:
        """The worker whose path is the rest of this one's, or None for the orchestrator's own workers."""
        if len(segments) < 2:
            return None
        return self._threads_by_path.get(segments[:-1])

    def _started(self, item: dict) -> list:
        worker_id = item.get('agentThreadId')
        if not worker_id or worker_id in self._workers:
            return []
        segments = _agent_path_segments(item.get('agentPath'))
        worker = WorkerUpdate(
            worker_id=worker_id,
            parent_id=self._parent(segments),
            depth=max(len(segments), 1),
            label=_worker_label(segments),
            state=WORKER_RUNNING,
            # Every worker runs on the person's choices, or the chat's own when both are None.
            model=self._options.worker_label or self._options.worker_model,
            effort=self._options.worker_effort,
        )
        if segments:
            self._threads_by_path[segments] = worker_id
        self._workers[worker_id] = worker
        return [worker]

    def _completed(self, item: dict) -> list:
        """End a worker that the thread which started it reports done.

        That report doesn't say how the worker's turn ended, and its own
        thread's turn/completed does. So it ends only a worker whose own
        thread has reported no turn here.
        """
        worker_id = item.get('agentThreadId')
        worker = self._workers.get(worker_id)
        if worker is None or worker.state != WORKER_RUNNING or worker_id in self._turns:
            return []
        return self._change(worker_id, state=WORKER_DONE, activity=None)

    def _turn_started(self, worker_id: str, turn: dict) -> list:
        if turn.get('id'):
            self._turns[worker_id] = turn['id']
        # A worker that ended runs again when it is given more to do.
        return self._change(worker_id, state=WORKER_RUNNING, activity='Working')

    def _item_started(self, worker_id: str, item: dict) -> list:
        if item.get('type') == 'subAgentActivity':
            # A worker reports the sub-workers it starts, as the orchestrator reports its workers.
            return self.sub_agent_activity(item)
        if self._workers[worker_id].state != WORKER_RUNNING:
            return []
        activity = _worker_activity(item)
        if activity is None:
            return []
        return self._change(worker_id, activity=activity)

    def _item_completed(self, worker_id: str, item: dict) -> list:
        if item.get('type') == 'subAgentActivity':
            return self.sub_agent_activity(item)
        if item.get('type') != 'agentMessage':
            return []
        # The worker's latest message. Its last one is its report to the thread that started it.
        summary = _clip(item.get('text'), WORKER_SUMMARY_LIMIT)
        if not summary:
            return []
        return self._change(worker_id, summary=summary)

    def _usage_updated(self, worker_id: str, token_usage: dict) -> list:
        total = token_usage.get('total')
        if not isinstance(total, dict):
            return []
        self._usage_totals[worker_id] = total
        tokens = total.get('totalTokens')
        if not isinstance(tokens, int):
            return []
        return self._change(worker_id, tokens=tokens)

    def _turn_completed(self, worker_id: str, turn: dict) -> list:
        state = _worker_state(turn.get('status'))
        if state is None:
            return []
        changes = {'state': state, 'activity': None}
        if state == WORKER_FAILED:
            message = readable_codex_error((turn.get('error') or {}).get('message'))
            if message:
                changes['summary'] = _clip(message, WORKER_SUMMARY_LIMIT)
        return self._change(worker_id, **changes)


class CodexTurnParser:
    """Turns app-server notifications for one turn into turn events.

    On Ultra turns, once track_workers() is called, it also reports the
    turn's workers. Their own threads' notifications reach the same queue,
    and only the orchestrator's thread writes the reply. Such a turn is over
    once the orchestrator's turn has completed and no worker still runs.
    """

    def __init__(self, turn_id: str, thread_id: str | None = None):
        self.turn_id = turn_id
        # The orchestrator's thread. An Ultra turn's other threads are its workers'.
        self.thread_id = thread_id
        self.reply_text = ''
        self.error: str | None = None
        # Whether the orchestrator's own turn has completed.
        self.finished = False
        self.usage_total: dict | None = None
        # An Ultra turn's workers; None on other turns.
        self.workers: CodexWorkers | None = None
        self._reasoning_text = ''
        self._streamed_message_ids = set()
        self._new_message = False
        self._new_reasoning = False

    def track_workers(self, options: UltraOptions) -> None:
        """Follow an Ultra turn's workers."""
        self.workers = CodexWorkers(options)

    def turn_over(self) -> bool:
        """Whether the turn is over: the orchestrator's turn has completed, and none of its workers still runs.

        Workers may outlive the orchestrator's reply. They stay listed until
        each one ends, since Codex doesn't stop them with it.
        """
        if not self.finished:
            return False
        return self.workers is None or not self.workers.running()

    def stop_workers(self) -> list:
        """Workers still running when the app-server ends, as stopped with it."""
        if self.workers is None:
            return []
        return self.workers.stop_running()

    def reply_usage(self, previous_total: dict | None) -> TokenUsage | None:
        """The reply's token usage, or None when Codex reported none.

        That is the orchestrator's thread's usage since ``previous_total``,
        its total when the thread's turn before this one ended, plus all that
        each worker's thread used.
        """
        usage = _usage_difference(self.usage_total, previous_total)
        if self.workers is None:
            return usage
        for total in self.workers.usage_totals():
            usage = _usage_sum(usage, _usage_difference(total, None))
        return usage

    def handle(self, message: dict) -> list:
        method = message.get('method')
        params = message.get('params') or {}
        if self.workers is not None and params.get('threadId') != self.thread_id:
            # A worker's own thread: it updates the worker list, never the reply.
            return self.workers.handle(message)
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
        if item_type == 'subAgentActivity' and self.workers is not None:
            # A worker gets its own status line when it starts.
            return self.workers.sub_agent_activity(item)
        description = describe_item(item)
        if description:
            return self._activity(description)
        return []

    def _item_completed(self, item: dict) -> list:
        item_type = item.get('type')
        if item_type == 'subAgentActivity' and self.workers is not None:
            return self.workers.sub_agent_activity(item)
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

    def __init__(self, machine, cli: str, cwd: str, on_notification, api_key: str | None = None):
        self.machine = machine
        self.cli = cli
        # Signs the app-server in with the user's own API key, held only in its memory.
        self.api_key = api_key
        self._cwd = cwd
        self._on_notification = on_notification
        self._process = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._thread_queues: dict[str, asyncio.Queue] = {}
        # The threads whose workers' notifications go to a subscribed thread's
        # queue too, each with the subscribed thread whose queue that is.
        self._worker_routes: dict[str, str] = {}
        self._next_id = 0
        self._start_lock = asyncio.Lock()
        self.generation = 0

    @property
    def running(self) -> bool:
        if self._process is None or self._process.returncode is not None:
            return False
        # Replies cannot arrive once the reader has ended.
        return self._reader is not None and not self._reader.done()

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self.running:
                return
            args = [self.cli, 'app-server']
            for feature in DISABLED_FEATURES:
                args.extend(['--disable', feature])
            if self.api_key:
                # Keep the key out of auth.json, so the computer's own sign-in is untouched.
                args.extend(['-c', 'cli_auth_credentials_store="ephemeral"'])
            self._process = await self.machine.start_process(args, self._cwd)
            self.generation += 1
            self._reader = asyncio.create_task(self._read_messages(self._process))
            client_info = {'name': 'buddy', 'title': 'Buddy', 'version': '1.0'}
            try:
                await self._send_request('initialize', {'clientInfo': client_info}, REQUEST_TIMEOUT)
                await self._send({'method': 'initialized'})
                if self.api_key:
                    await self._send_request(
                        'account/login/start', {'type': 'apiKey', 'apiKey': self.api_key}, REQUEST_TIMEOUT
                    )
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

    def follow_workers(self, thread_id: str) -> None:
        """Send the notifications of the workers that a subscribed thread starts to its queue too.

        Codex reports each worker's turn under the worker's own thread id. A
        worker's thread joins the queue as soon as the thread that starts it
        reports it, before any turn of its own, and so do the sub-workers it
        starts. Unsubscribing the thread ends all of that.
        """
        self._worker_routes[thread_id] = thread_id

    def unsubscribe(self, thread_id: str) -> None:
        queue = self._thread_queues.pop(thread_id, None)
        for followed, subscribed in list(self._worker_routes.items()):
            if subscribed != thread_id:
                continue
            del self._worker_routes[followed]
            if self._thread_queues.get(followed) is queue:
                del self._thread_queues[followed]

    async def _read_messages(self, process) -> None:
        """Handle the app-server's messages until they end, for any reason."""
        reason = None
        try:
            await self._handle_messages(process)
        except OutputLimitError:
            log.warning('Buddy fell behind reading Codex output; stopping its app-server')
            reason = OUTPUT_BACKLOG_MESSAGE
        except ProcessClosedError as error:
            log.info('Codex app-server output ended: %s', error)
        except Exception:
            log.exception('Codex app-server reader failed')
        finally:
            if reason is None:
                reason = f'Codex stopped unexpectedly. {_shorten(process.stderr_text(), 600)}'.strip()
            self._end_process(process, reason)

    async def _handle_messages(self, process) -> None:
        while True:
            line = await process.read_line()
            if line is None:
                return
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

    def _end_process(self, process, reason: str) -> None:
        """Stop a process whose output can no longer be read, and fail everything waiting on it."""
        if self._process is process:
            # It may still be running unread. The next request starts a new one.
            process.kill()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(SubscriptionError(reason))
        for queue in list(self._thread_queues.values()):
            queue.put_nowait(SubscriptionError(reason))

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
        thread_id = params.get('threadId')
        queue = self._thread_queues.get(thread_id)
        if queue:
            self._route_new_worker(thread_id, params, queue)
            queue.put_nowait(message)
        try:
            self._on_notification(message)
        except Exception:
            log.exception('Codex notification handler failed')

    def _route_new_worker(self, thread_id: str, params: dict, queue: asyncio.Queue) -> None:
        """Send a worker's notifications to ``queue`` from now on, when the thread that started it is followed.

        This runs as the notification is read, so none of the worker's own
        notifications that follow are missed.
        """
        subscribed = self._worker_routes.get(thread_id)
        if subscribed is None:
            return
        worker_thread = started_worker_thread(params)
        if worker_thread is None or worker_thread in self._thread_queues:
            return
        self._thread_queues[worker_thread] = queue
        # The sub-workers it starts follow it there.
        self._worker_routes[worker_thread] = subscribed

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
    machine_id: str
    generation: int
    access: str
    cwd: str
    usage_total: dict | None
    # The Ultra choice the thread started with, or None when it started without Ultra.
    ultra: UltraOptions | None = None

    def can_run(self, turn: TurnRequest, generation: int) -> bool:
        """Whether this kept thread can run the turn.

        Its app-server must not have restarted since. Its sandbox, folder and
        workers were set when it started, so it must have started for the
        turn's access, folder and Ultra choice. Workers are on only in Ultra
        threads, so a thread never runs a turn of the other kind.
        """
        if self.generation != generation:
            return False
        if self.access != turn.access or self.cwd != turn.cwd:
            return False
        return self.ultra == turn.ultra


class CodexLogin:
    def __init__(self, machine_id: str, method: str, login_id: str, url: str, user_code: str | None):
        self.machine_id = machine_id
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

    def __init__(self):
        # One app-server per machine, keyed by machine id.
        self._servers: dict[str, CodexAppServer] = {}
        self._live_threads: OrderedDict[str, LiveThread] = OrderedDict()
        self._login: CodexLogin | None = None
        self._model_efforts: dict[str, list[str]] = {}
        self._model_efforts_server: CodexAppServer | None = None
        self._model_efforts_generation: int | None = None
        self.rate_limits: dict | None = None
        # Set by the service before each use: which sign-in Codex should be using.
        self.credential_mode = CREDENTIAL_MACHINE
        self._api_key: str | None = None

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

    def _forget_machine_threads(self, machine_id: str) -> None:
        for key, live in list(self._live_threads.items()):
            if live.machine_id == machine_id:
                del self._live_threads[key]

    def _release_machine_threads(self, machine_id: str) -> None:
        """Unload and forget a machine's kept threads while its app-server keeps running."""
        for key, live in list(self._live_threads.items()):
            if live.machine_id == machine_id:
                del self._live_threads[key]
                self._release_live_thread(live)

    def use_credential(self, credential_mode: str, api_key: str | None) -> None:
        """Sign in with the machine's own sign-in, or with the user's API key; a change restarts Codex."""
        if credential_mode == self.credential_mode and api_key == self._api_key:
            return
        self.credential_mode = credential_mode
        self._api_key = api_key
        self.stop()

    async def _server_for(self, settings: ProviderSettings, machine) -> CodexAppServer:
        """The machine's app-server; it starts in the machine's empty chat folder."""
        cli = await machine.find_tool(TOOL_CODEX, settings.cli_path)
        if not cli:
            raise SubscriptionError(f'{machine.name}: {NOT_INSTALLED_MESSAGE}')
        server = self._servers.get(machine.id)
        if server and (server.cli != cli or server.machine is not machine):
            server.stop()
            server = None
            self._forget_machine_threads(machine.id)
        if not server:
            server = CodexAppServer(
                machine, cli, await machine.chat_dir(), self._on_notification, api_key=self._api_key
            )
            self._servers[machine.id] = server
        await server.ensure_started()
        return server

    async def status(self, settings: ProviderSettings, machine) -> dict:
        cli = await machine.find_tool(TOOL_CODEX, settings.cli_path)
        if not cli:
            return {'installed': False, 'signed_in': False, 'message': NOT_INSTALLED_MESSAGE}
        try:
            server = await self._server_for(settings, machine)
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
        uses_api_key = account.get('type') == 'apiKey'
        if account and self.credential_mode == CREDENTIAL_API_KEY:
            if uses_api_key:
                status['billing'] = 'api_key'
            else:
                status['api_billing'] = True
                status['message'] = 'Codex is not using your OpenAI API key. Add it again in Settings → Machines.'
        elif uses_api_key:
            status['api_billing'] = True
            status['message'] = (
                'Codex is signed in with an API key, which bills API usage instead of a ChatGPT plan. '
                'Sign out and sign in with ChatGPT.'
            )
        elif account:
            status['billing'] = 'plan'
        if account:
            try:
                rate_limits = await server.request('account/rateLimits/read', None, timeout=30)
                self.rate_limits = summarize_rate_limits(rate_limits.get('rateLimits'))
            except SubscriptionError as error:
                log.info('Could not read Codex usage limits: %s', error)
        status['usage'] = self.rate_limits
        return status

    async def list_models(self, settings: ProviderSettings, machine) -> list[ProviderModel]:
        server = await self._server_for(settings, machine)
        return await self._load_models(server)

    async def _load_models(self, server: CodexAppServer) -> list[ProviderModel]:
        generation = server.generation
        result = await server.request('model/list', {}, timeout=30)
        if server.generation != generation:
            raise SubscriptionError('Codex restarted while loading model metadata. Try again.')
        models = []
        model_efforts = {}
        for entry in result.get('data') or []:
            if entry.get('hidden'):
                continue
            value = entry.get('id') or entry.get('model')
            if not value:
                continue
            efforts = [option.get('reasoningEffort') for option in entry.get('supportedReasoningEfforts') or []]
            efforts = [effort for effort in efforts if effort]
            model_efforts[value] = efforts
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
        self._model_efforts = model_efforts
        self._model_efforts_server = server
        self._model_efforts_generation = generation
        return models

    async def start_login(self, settings: ProviderSettings, machine, method: str) -> dict:
        if self.credential_mode != CREDENTIAL_MACHINE:
            raise SubscriptionError('ChatGPT uses your OpenAI API key here. Remove the key to sign in on the computer.')
        await self.cancel_login()
        server = await self._server_for(settings, machine)
        if method == 'device':
            result = await server.request('account/login/start', {'type': 'chatgptDeviceCode'})
            login = CodexLogin(
                machine.id, 'device', result.get('loginId'), result.get('verificationUrl'), result.get('userCode')
            )
        else:
            result = await server.request('account/login/start', {'type': 'chatgpt'})
            login = CodexLogin(machine.id, 'browser', result.get('loginId'), result.get('authUrl'), None)
        self._login = login
        if not is_chatgpt_sign_in_url(login.url):
            log.warning('Refused a ChatGPT sign-in link from %s that is not an https OpenAI address', machine.name)
            # Stop the sign-in the app-server started, since its link is not shown.
            await self.cancel_login()
            raise SubscriptionError(UNEXPECTED_SIGN_IN_URL_MESSAGE)
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
        if not login or login.state != 'waiting':
            return
        server = self._servers.get(login.machine_id)
        if not server or not server.running:
            return
        try:
            await server.request('account/login/cancel', {'loginId': login.login_id}, timeout=10)
        except SubscriptionError as error:
            log.info('Could not cancel the ChatGPT sign-in: %s', error)

    async def logout(self, settings: ProviderSettings, machine) -> None:
        await self.cancel_login()
        server = await self._server_for(settings, machine)
        await server.request('account/logout', None)
        self._release_machine_threads(machine.id)
        self.rate_limits = None

    def _effort(self, turn: TurnRequest) -> str | None:
        supported = turn.efforts
        if turn.is_task or turn.effort in ('none', 'minimal'):
            if supported:
                return supported[0]
            return 'low'
        if turn.effort in supported:
            return turn.effort
        return None

    async def _ensure_effort_models(self, server: CodexAppServer, turn: TurnRequest) -> None:
        if not _sends_effort(turn):
            return
        if not (
            self._model_efforts_server is server
            and self._model_efforts_generation == server.generation
        ):
            # Carried registry metadata may describe an earlier host or server.
            await self._load_models(server)
        turn.efforts = list(self._model_efforts.get(turn.model, []))

    def _developer_instructions(self, turn: TurnRequest) -> str | None:
        system = turn.conversation.system
        if turn.access == ACCESS_CHAT:
            return f'{CHAT_INSTRUCTIONS}\n\n{system}'.strip()
        return system or None

    def _worker_effort(self, turn: TurnRequest) -> str | None:
        """The thinking level chosen for an Ultra turn's workers, when their model offers it here.

        Workers run on the chat's own model unless the person chose another.
        """
        options = turn.ultra
        if not options.worker_effort:
            return None
        worker_model = options.worker_model or turn.model
        if options.worker_effort in self._model_efforts.get(worker_model, []):
            return options.worker_effort
        return None

    def _worker_config(self, turn: TurnRequest) -> dict:
        """Config keys that turn Codex's workers on for an Ultra thread, with the person's model and level.

        Codex may not honour the model and level keys, so the orchestrator
        note also asks for them in every spawn.
        """
        config = {'features.multi_agent': True}
        if turn.ultra.worker_model:
            config['agents.default_subagent_model'] = turn.ultra.worker_model
        effort = self._worker_effort(turn)
        if effort:
            config['agents.default_subagent_reasoning_effort'] = effort
        return config

    async def _thread_config(self, server: CodexAppServer, turn: TurnRequest) -> dict:
        """Per-thread config overrides that keep threads in bounds.

        Only Ultra threads may start Codex's own sub-agents, the workers Buddy
        lists under the chat. Other threads have them off: Buddy does not list
        their sub-agents, and nothing may run unseen.

        The read-only sandbox blocks writes but not reads anywhere on disk, and
        MCP servers from the user's Codex config (such as a JavaScript REPL) run
        outside it. Chat threads therefore get no command, file, or MCP tools;
        read threads keep read-only commands but get no MCP servers. The working
        folder may be someone else's repository, so neither loads its AGENTS.md
        files or skills, which could steer those reads.
        """
        # Dotted keys leave the app-server's other feature switches alone. A
        # whole features table would turn back on what it disabled at start.
        config = {'features.multi_agent': False}
        if turn.ultra is not None:
            config.update(self._worker_config(turn))
        if turn.access == ACCESS_FULL:
            return config

        effective = await server.request('config/read', {'includeLayers': False, 'cwd': turn.cwd})
        server_names = ((effective.get('config') or {}).get('mcp_servers') or {}).keys()
        config['mcp_servers'] = {name: {'enabled': False} for name in server_names}
        # The user's own ~/.codex/AGENTS.md still applies.
        config['project_doc_max_bytes'] = 0
        config['skills.include_instructions'] = False
        if turn.access == ACCESS_CHAT:
            config['features.shell_tool'] = False
            config['features.unified_exec'] = False
            config['features.view_image'] = False
        return config

    async def _start_thread(self, server: CodexAppServer, turn: TurnRequest) -> str:
        sandbox = 'read-only'
        if turn.access == ACCESS_FULL:
            sandbox = 'danger-full-access'
        ephemeral = True
        if turn.ultra is not None:
            # Workers can't start from an ephemeral thread: Codex fails their spawns.
            ephemeral = False
        params = {
            'cwd': turn.cwd,
            'model': turn.model,
            'ephemeral': ephemeral,
            'sandbox': sandbox,
            'approvalPolicy': 'never',
            'developerInstructions': self._developer_instructions(turn),
        }
        params['config'] = await self._thread_config(server, turn)
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
        if not live.can_run(turn, generation):
            self._release_live_thread(live)
            return None
        return live

    def _keep_live_thread(self, key: str, live: LiveThread) -> None:
        replaced = self._live_threads.pop(key, None)
        if replaced:
            self._release_live_thread(replaced)
        self._live_threads[key] = live
        while len(self._live_threads) > LIVE_THREAD_LIMIT:
            _, oldest = self._live_threads.popitem(last=False)
            self._release_live_thread(oldest)

    def _release_live_thread(self, live: LiveThread) -> None:
        """Unload a thread Buddy no longer keeps, unless its app-server restarted since."""
        server = self._servers.get(live.machine_id)
        if not server or server.generation != live.generation:
            return
        self._release_thread(server, live.thread_id)

    def _release_thread(self, server: CodexAppServer, thread_id: str) -> None:
        if not server.running:
            return

        async def unsubscribe():
            try:
                await server.request('thread/unsubscribe', {'threadId': thread_id}, timeout=10)
            except SubscriptionError as error:
                log.debug('Could not unload Codex thread %s: %s', thread_id, error)

        asyncio.create_task(unsubscribe())

    def _interrupt_turn(self, server: CodexAppServer, thread_id: str, turn_id: str) -> None:
        if not server.running:
            return

        async def interrupt():
            try:
                await server.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=10)
            except SubscriptionError as error:
                log.debug('Could not interrupt Codex turn %s: %s', turn_id, error)
            self._release_thread(server, thread_id)

        asyncio.create_task(interrupt())

    async def run_turn(self, turn: TurnRequest):
        """Run one chat turn and yield its events."""
        machine = turn.machine
        server = await self._server_for(turn.settings, machine)
        await self._ensure_effort_models(server, turn)
        conversation = turn.conversation
        history_key = f'{machine.id}|{conversation_key(conversation.history, conversation.system)}'

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
        if turn.ultra is not None:
            # Each worker reports its turn under its own thread, which joins this queue.
            server.follow_workers(thread_id)
        parser = None
        completed = False
        timed_out = False
        try:
            # Thread setup also sends RPCs and may restart the app-server.
            await self._ensure_effort_models(server, turn)
            params = {'threadId': thread_id, 'input': turn_input(turn), 'model': turn.model}
            effort = self._effort(turn)
            if effort:
                params['effort'] = effort
            result = await server.request('turn/start', params)
            parser = CodexTurnParser(result['turn']['id'], thread_id)
            if turn.ultra is not None:
                parser.track_workers(turn.ultra)

            deadline = _reply_deadline(turn)
            while not parser.turn_over():
                try:
                    message = await _next_message(queue, deadline)
                except TimeoutError:
                    log.warning('Stopping an Ultra turn on %s that ran past its time limit', machine.name)
                    timed_out = True
                    break
                if isinstance(message, SubscriptionError):
                    # The app-server ended, and its workers with it; its reader says why.
                    for event in parser.stop_workers():
                        yield event
                    raise message
                for event in parser.handle(message):
                    yield event

            usage = parser.reply_usage(previous_usage)
            if usage:
                yield usage
            if parser.error:
                yield TurnFailed(parser.error)
            elif timed_out:
                yield TurnFailed(ULTRA_TIME_LIMIT_MESSAGE)
            else:
                completed = True
        finally:
            server.unsubscribe(thread_id)
            if completed and not turn.is_task:
                answered = conversation.history + [conversation.prompt, ChatTurn('assistant', parser.reply_text)]
                next_key = f'{machine.id}|{conversation_key(answered, conversation.system)}'
                live = LiveThread(
                    thread_id,
                    machine.id,
                    server.generation,
                    turn.access,
                    turn.cwd,
                    parser.usage_total,
                    ultra=turn.ultra,
                )
                self._keep_live_thread(next_key, live)
            elif parser and not parser.finished:
                self._interrupt_turn(server, thread_id, parser.turn_id)
            else:
                self._release_thread(server, thread_id)

    def stop_machine(self, machine_id: str) -> None:
        """Stop one computer's app-server and forget its threads; other computers keep theirs."""
        server = self._servers.pop(machine_id, None)
        if server is not None:
            server.stop()
            if self._model_efforts_server is server:
                self._model_efforts_server = None
                self._model_efforts_generation = None
        self._forget_machine_threads(machine_id)

    def stop(self) -> None:
        for server in self._servers.values():
            server.stop()
        self._servers.clear()
        self._live_threads.clear()
        self._model_efforts.clear()
        self._model_efforts_server = None
        self._model_efforts_generation = None


def _reply_deadline(turn: TurnRequest) -> float | None:
    """When Buddy stops following an Ultra turn that started now, on the event loop's clock; None for other turns."""
    if turn.ultra is None:
        return None
    return asyncio.get_running_loop().time() + ULTRA_TIME_LIMIT


async def _next_message(queue: asyncio.Queue, deadline: float | None):
    """The next message of a turn's queue. Raises TimeoutError when the deadline passes first."""
    if deadline is None:
        return await queue.get()
    async with asyncio.timeout_at(deadline):
        return await queue.get()
