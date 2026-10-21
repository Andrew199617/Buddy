"""Claude models through the Claude Code CLI, billed to the user's Claude plan.

Each turn runs ``claude -p`` with stream-json input and output. Claude Code
signs in with its own ``claude auth login`` flow and stores its credentials in
its own config folder. Anthropic counts ``claude -p`` usage against the Claude
subscription's limits.
"""

import asyncio
import dataclasses
import json
import logging
import re
from pathlib import Path

from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_READ,
    CHAT_INSTRUCTIONS,
    CREDENTIAL_API_KEY,
    CREDENTIAL_MACHINE,
    CREDENTIAL_PLAN_TOKEN,
    ProviderModel,
    ProviderSettings,
    TurnRequest,
    UltraOptions,
    model_key,
)
from open_webui.utils.subscriptions import ultra
from open_webui.utils.subscriptions.conversation import (
    ChatTurn,
    SessionStore,
    conversation_key,
)
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
from buddy_runner.discovery import TOOL_CLAUDE
from buddy_runner.local_process import temp_file_arg
from buddy_runner.process import ProcessClosedError

log = logging.getLogger(__name__)

PROVIDER_ID = 'claude'
NOT_INSTALLED_MESSAGE = (
    'Claude Code was not found. Install it from https://claude.com/claude-code '
    'or set its path in the Claude subscription settings.'
)
NOT_SIGNED_IN_MESSAGE = 'Claude is not signed in. Sign in under Settings → Machines.'
EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
# No WebFetch: a prompt in the folder could make it send file contents to any URL.
READ_ONLY_TOOLS = 'Read,Grep,Glob,WebSearch'
# Ultra turns add the Agent tool, which starts workers, to the turn's own tools.
ULTRA_CHAT_TOOLS = 'Agent'
ULTRA_READ_TOOLS = 'Read,Grep,Glob,WebSearch,Agent'
# Ultra turns define Buddy's worker types in an initialize request, before the prompt.
WORKERS_REQUEST_ID = 'buddy-ultra'
# Seconds to wait for Claude Code's answer, as when listing models.
WORKERS_ANSWER_TIMEOUT = 30
WORKER_DESCRIPTION = (
    'A Buddy worker with the same access as this turn. Use it for most tasks, '
    'including any task that may need sub-workers.'
)
READER_DESCRIPTION = (
    'A Buddy worker that can only read and search, in the project and on the web, and never '
    'changes anything. Use it for tasks that only need to read.'
)
# A reader has the read-only turn's tools. They leave out Agent, so a reader
# can't start sub-workers.
READER_TOOLS = READ_ONLY_TOOLS.split(',')
ULTRA_TOO_OLD_REASON = 'Claude Code on {computer} is too old for Ultra.'
# The tool that starts a worker. Claude Code called it Task before it was renamed.
AGENT_TOOLS = ('Agent', 'Task')
# How long Buddy reads an Ultra turn before it stops the CLI and every worker
# still running. Workers may take hours; this only ends a turn that never would.
ULTRA_TIME_LIMIT_HOURS = 6
ULTRA_TIME_LIMIT = ULTRA_TIME_LIMIT_HOURS * 60 * 60
ULTRA_TIME_LIMIT_MESSAGE = f'Buddy stopped this Ultra reply after {ULTRA_TIME_LIMIT_HOURS} hours.'
# The summary of a worker that was still running when its turn ended.
WORKER_ENDED_WITH_TURN = 'Stopped when the reply ended.'
# A worker's report is kept to this many characters, since every snapshot of the
# turn's workers carries it.
WORKER_SUMMARY_LIMIT = 4000
# The task_updated statuses of a task that has not ended.
TASK_LIVE_STATUSES = ('pending', 'running')
# Buddy stops a worker with a stop_task control request; each one's id starts with this.
STOP_REQUEST_ID = 'buddy-stop'
# Seconds Buddy waits for Claude Code to report its workers stopped, before it
# ends the CLI of an Ultra turn that is stopped while workers still run.
STOP_WORKERS_GRACE = 5
TRANSCRIPT_INTRO = 'Here is our conversation so far. Continue it by replying to my latest message.'
INSTRUCTIONS_FILE = 'instructions'
LOGIN_URL = re.compile(r'https://\S+/oauth/authorize\?\S+')
# The authMethod `claude auth status --json` reports for a Claude plan sign-in.
# The others bill elsewhere: 'api_key' and 'api_key_helper' bill the API,
# 'third_party' bills Bedrock, Vertex, or Foundry, and 'oauth_token' is any
# other token, such as a gateway's.
PLAN_AUTH_METHOD = 'claude.ai'
# What ``claude auth status`` reports for CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY.
PLAN_TOKEN_AUTH_METHOD = 'oauth_token'
API_KEY_AUTH_METHOD = 'api_key'
FALLBACK_MODELS = [
    ProviderModel(key='opus', value='opus', name='Claude Opus'),
    ProviderModel(key='sonnet', value='sonnet', name='Claude Sonnet'),
    ProviderModel(key='haiku', value='haiku', name='Claude Haiku'),
]


def claude_effort(effort: str | None) -> str | None:
    if effort in ('none', 'minimal'):
        return 'low'
    if effort in EFFORT_LEVELS:
        return effort
    return None


def ultra_env(options: UltraOptions) -> dict[str, str]:
    """Environment variables that set how an Ultra turn's workers run."""
    # The orchestrator's workers may start sub-workers, and those may not start any.
    env = {'CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH': str(ultra.MAX_DEPTH)}
    if options.worker_model:
        # Sub-workers may start as Claude Code's own general-purpose type
        # instead of Buddy's, so the person's model is forced on every worker.
        env['CLAUDE_CODE_SUBAGENT_MODEL'] = options.worker_model
        env['CLAUDE_CODE_SUBAGENT_MODEL_FORCE'] = '1'
    if options.max_workers is not None:
        env['CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS'] = str(options.max_workers)
    # CLAUDE_CODE_EFFORT_LEVEL stays unset: it would set the orchestrator's
    # thinking level too, not only the workers'.
    return env


def without_ultra(turn: TurnRequest) -> TurnRequest:
    """The same turn without Ultra: no workers, and no note that asks for them."""
    system = ultra.without_orchestrator_note(turn.conversation.system, PROVIDER_ID, turn.access, turn.ultra)
    conversation = dataclasses.replace(turn.conversation, system=system)
    return dataclasses.replace(turn, conversation=conversation, ultra_options=None)


def _worker_prompt(turn: TurnRequest) -> str:
    """The system prompt of an Ultra turn's workers.

    Workers don't see the orchestrator's instructions, so the chat's own, such
    as a chosen project agent's and the person's, follow Buddy's worker
    prompt. The orchestrator note stays out: workers do their task themselves.
    """
    chat_system = ultra.without_orchestrator_note(turn.conversation.system, PROVIDER_ID, turn.access, turn.ultra)
    if not chat_system:
        return ultra.WORKER_PROMPT
    return f'{ultra.WORKER_PROMPT}\n\n{chat_system}'


def _worker_type(turn: TurnRequest, description: str) -> dict:
    """One Claude Code agent definition for an Ultra turn's workers."""
    options = turn.ultra
    definition = {
        'description': description,
        'prompt': _worker_prompt(turn),
        # 'inherit' runs the workers on the chat's own model.
        'model': options.worker_model or 'inherit',
    }
    # The chat's own thinking level, unless the person chose one for the workers.
    effort = claude_effort(options.worker_effort) or claude_effort(turn.effort)
    if effort:
        definition['effort'] = effort
    return definition


def worker_types(turn: TurnRequest) -> dict:
    """Buddy's worker types for an Ultra turn, as the agents of Claude Code's initialize request.

    A buddy-worker names no tools, so it has the turn's own: the same access
    as the orchestrator. A buddy-reader can only read and search. Chat turns
    have no file tools for a reader to keep, so they get only buddy-worker.
    """
    agents = {ultra.WORKER_AGENT: _worker_type(turn, WORKER_DESCRIPTION)}
    if turn.access != ACCESS_CHAT:
        reader = _worker_type(turn, READER_DESCRIPTION)
        reader['tools'] = list(READER_TOOLS)
        agents[ultra.READER_AGENT] = reader
    return agents


def _control_answer(message: dict, request_id: str) -> dict | None:
    """Claude Code's answer to the control request with this id, or None for any other message."""
    if message.get('type') != 'control_response':
        return None
    response = message.get('response') or {}
    if response.get('request_id') != request_id:
        return None
    # An error answer has none, as if nothing were listed.
    return response.get('response') or {}


def _agent_names(answer: dict) -> list[str]:
    """The agent types an initialize answer lists."""
    names = []
    for agent in answer.get('agents') or []:
        if isinstance(agent, dict) and agent.get('name'):
            names.append(agent['name'])
        elif isinstance(agent, str):
            names.append(agent)
    return names


def _image_block(url: str) -> dict | None:
    if url.startswith(('http://', 'https://')):
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    match = re.match(r'data:(image/[\w.+-]+);base64,(.*)', url, re.DOTALL)
    if not match:
        return None
    return {
        'type': 'image',
        'source': {'type': 'base64', 'media_type': match.group(1), 'data': match.group(2)},
    }


def _text_block(text: str) -> dict:
    return {'type': 'text', 'text': text}


def _image_blocks(urls: list[str]) -> list[dict]:
    blocks = []
    for url in urls:
        block = _image_block(url)
        if block:
            blocks.append(block)
    return blocks


def _stream_user_message(content: list[dict]) -> dict:
    return {
        'type': 'user',
        'message': {'role': 'user', 'content': content},
        'parent_tool_use_id': None,
        'session_id': '',
    }


def user_message(text: str, images: list[str]) -> dict:
    return _stream_user_message([_text_block(text), *_image_blocks(images)])


def transcript_message(history: list[ChatTurn], prompt: ChatTurn) -> dict:
    """First message of a fresh session: the chat so far, its images, and the new prompt."""
    if not history:
        return user_message(prompt.text, prompt.images)

    content = [_text_block(f'{TRANSCRIPT_INTRO}\n\n<conversation>')]
    for turn in history:
        content.append(_text_block(f'<{turn.role}>\n{turn.text.strip()}'))
        content.extend(_image_blocks(turn.images))
        content.append(_text_block(f'</{turn.role}>'))
    content.append(_text_block(f'</conversation>\n\nMy latest message:\n{prompt.text}'))
    content.extend(_image_blocks(prompt.images))
    return _stream_user_message(content)


def turn_message(turn: TurnRequest, resume_id: str | None) -> dict:
    """The message that asks for this turn's reply.

    A resumed session already holds the chat, so it gets only the new prompt;
    a fresh one gets the whole chat. An Ultra turn's message ends with
    ULTRA_TURN_NOTE, added here so the chat itself never holds it.
    """
    prompt = turn.conversation.prompt
    if resume_id:
        message = user_message(prompt.text, prompt.images)
    else:
        message = transcript_message(turn.conversation.history, prompt)
    if turn.ultra is not None:
        message['message']['content'].append(_text_block(ultra.ULTRA_TURN_NOTE))
    return message


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


def describe_tool(name: str, tool_input: dict) -> str:
    """One line describing a Claude Code tool call."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if name in ('Bash', 'PowerShell'):
        return f'Running `{_shorten(tool_input.get("command"))}`'
    if name == 'Read':
        return f'Reading {_shorten(tool_input.get("file_path"))}'
    if name in ('Edit', 'MultiEdit', 'Write', 'NotebookEdit'):
        path = tool_input.get('file_path') or tool_input.get('notebook_path')
        return f'Editing {_shorten(path)}'
    if name == 'Grep':
        return f'Searching files for `{_shorten(tool_input.get("pattern"), 80)}`'
    if name == 'Glob':
        return f'Finding files matching `{_shorten(tool_input.get("pattern"), 80)}`'
    if name == 'WebSearch':
        return f'Searching the web for {_shorten(tool_input.get("query"), 100)}'
    if name == 'WebFetch':
        return f'Reading {_shorten(tool_input.get("url"), 120)}'
    if name in ('Task', 'Agent'):
        return f'Starting a subagent: {_shorten(tool_input.get("description"), 100)}'
    if name == 'TodoWrite':
        return 'Updating the task list'
    return f'Using {name}'


def _usage_from_result(usage: dict) -> TokenUsage:
    cache_read = int(usage.get('cache_read_input_tokens') or 0)
    cache_write = int(usage.get('cache_creation_input_tokens') or 0)
    uncached = int(usage.get('input_tokens') or 0)
    return TokenUsage(
        input_tokens=uncached + cache_read + cache_write,
        output_tokens=int(usage.get('output_tokens') or 0),
        cached_input_tokens=cache_read,
    )


def _usage_sum(first: TokenUsage, second: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cached_input_tokens=first.cached_input_tokens + second.cached_input_tokens,
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
    )


RATE_LIMIT_LABELS = {
    'five_hour': '5-hour',
    'seven_day': 'Weekly',
    'seven_day_opus': 'Weekly Opus',
    'seven_day_sonnet': 'Weekly Sonnet',
}


def rate_limit_window(info: dict) -> dict | None:
    """One usage window from a Claude Code rate_limit_event, when it has numbers."""
    utilization = info.get('utilization')
    if not isinstance(utilization, (int, float)):
        return None
    used_percent = utilization * 100 if utilization <= 1 else utilization
    resets_at = info.get('resetsAt')
    if isinstance(resets_at, (int, float)) and resets_at > 10_000_000_000:
        resets_at = resets_at / 1000
    return {
        'label': RATE_LIMIT_LABELS.get(info.get('rateLimitType'), 'Usage'),
        'used_percent': round(used_percent),
        'resets_at': resets_at,
    }


def _readable_error(text: str) -> str:
    if 'not logged in' in text.lower() or '/login' in text:
        return NOT_SIGNED_IN_MESSAGE
    return text


def _credential_mismatch_message(credential_mode: str, auth_method: str) -> str | None:
    """Why the CLI's sign-in is not the one Buddy expects for this provider, or None when it is."""
    if credential_mode == CREDENTIAL_PLAN_TOKEN:
        if auth_method == PLAN_TOKEN_AUTH_METHOD:
            return None
        return 'Claude Code is not using your Claude token. Add it again in Settings → Machines.'
    if credential_mode == CREDENTIAL_API_KEY:
        if auth_method == API_KEY_AUTH_METHOD:
            return None
        return 'Claude Code is not using your Anthropic API key. Add it again in Settings → Machines.'
    if auth_method == PLAN_AUTH_METHOD:
        return None
    return _not_a_plan_message(auth_method)


def _not_a_plan_message(auth_method: str) -> str:
    """Why a signed-in Claude Code account cannot run plan models."""
    if auth_method == 'third_party':
        return (
            'Claude Code is set up to use a cloud provider such as Amazon Bedrock or Google Vertex AI, which bills '
            'that account instead of a Claude plan. Remove that setup and sign in with your Claude account.'
        )
    if not auth_method:
        return (
            'Buddy could not confirm that Claude Code is signed in with a Claude plan. '
            'Update Claude Code, then sign in with your Claude account.'
        )
    return (
        'Claude Code is signed in with an Anthropic Console account, API key, or token, which bills API usage '
        'instead of a Claude plan. Sign out and sign in with your Claude account.'
    )


def _worker_state(status) -> str:
    """The state of a worker whose task ended with this Claude Code status."""
    if status == 'completed':
        return WORKER_DONE
    if status in ('stopped', 'killed'):
        return WORKER_STOPPED
    return WORKER_FAILED


def _worker_label(task: dict) -> str:
    """A worker's name in the list: the description its Agent call gave the task."""
    label = _shorten(task.get('description'), 100)
    if label:
        return label
    return task.get('subagent_type') or 'Worker'


def _task_tokens(message: dict) -> int | None:
    """The tokens a task has used so far, from a task_progress or task_notification message."""
    usage = message.get('usage')
    if not isinstance(usage, dict):
        return None
    tokens = usage.get('total_tokens')
    if isinstance(tokens, int):
        return tokens
    return None


class ClaudeWorkers:
    """The workers of one Ultra turn, followed through Claude Code's task messages.

    Claude Code runs each worker as a task. It reports task_started when one
    starts, task_progress while it works, task_updated and task_notification
    when it ends, and background_tasks_changed with every task still running
    in the background. A worker's id is its task id, which stop_task takes.

    A task that the orchestrator's own Agent call started is depth 1; one that
    a worker's Agent call started is depth 2, under that worker.
    """

    def __init__(self, options: UltraOptions):
        self._options = options
        self._workers: dict[str, WorkerUpdate] = {}
        # Each Agent call's id, and the Agent call of the worker that made it:
        # None for the orchestrator's own calls.
        self._callers: dict[str, str | None] = {}
        # The task that each Agent call started.
        self._tasks_by_call: dict[str, str] = {}

    def running(self) -> bool:
        """Whether any worker, started or listed in the background, has not ended yet."""
        for worker in self._workers.values():
            if worker.state == WORKER_RUNNING:
                return True
        return False

    def is_running(self, worker_id: str) -> bool:
        """Whether this turn has a worker with this id that has not ended yet."""
        worker = self._workers.get(worker_id)
        return worker is not None and worker.state == WORKER_RUNNING

    def running_ids(self) -> list[str]:
        """The ids of the workers that have not ended yet, in the order they were first reported."""
        ids = []
        for worker_id, worker in self._workers.items():
            if worker.state == WORKER_RUNNING:
                ids.append(worker_id)
        return ids

    def note_agent_calls(self, message: dict) -> None:
        """Remember who made each Agent call in an assistant message: the orchestrator or a worker."""
        caller = message.get('parent_tool_use_id') or None
        for block in (message.get('message') or {}).get('content') or []:
            if block.get('type') != 'tool_use' or block.get('name') not in AGENT_TOOLS:
                continue
            if block.get('id'):
                self._callers[block['id']] = caller

    def handle(self, message: dict) -> list:
        """WorkerUpdate events for one system message."""
        subtype = message.get('subtype')
        if subtype == 'task_started':
            return self._started(message)
        if subtype == 'task_progress':
            return self._progressed(message)
        if subtype == 'task_updated':
            return self._updated(message)
        if subtype == 'task_notification':
            return self._ended(message)
        if subtype == 'background_tasks_changed':
            return self._listed_in_background(message)
        return []

    def stop_running(self) -> list:
        """Every worker still running, as stopped: they end with the CLI when the turn does."""
        events = []
        for worker_id, worker in list(self._workers.items()):
            if worker.state == WORKER_RUNNING:
                events.extend(
                    self._change(worker_id, state=WORKER_STOPPED, activity=None, summary=WORKER_ENDED_WITH_TURN)
                )
        return events

    def _add(self, task_id: str, parent_id: str | None, depth: int, label: str) -> list:
        worker = WorkerUpdate(
            worker_id=task_id,
            parent_id=parent_id,
            depth=depth,
            label=label,
            state=WORKER_RUNNING,
            # Every worker runs on the person's choices, or the chat's own when both are None.
            model=self._options.worker_label or self._options.worker_model,
            effort=self._options.worker_effort,
        )
        self._workers[task_id] = worker
        return [worker]

    def _change(self, task_id: str, **changes) -> list:
        """Apply changes to a worker, and report it when anything changed.

        Each change makes a new WorkerUpdate, so an update already reported
        keeps what it said.
        """
        worker = self._workers[task_id]
        changed = dataclasses.replace(worker, **changes)
        if changed == worker:
            return []
        self._workers[task_id] = changed
        return [changed]

    def _parent(self, message: dict) -> str | None:
        """The worker that started this task, or None when the orchestrator did."""
        call_id = message.get('tool_use_id')
        if call_id in self._callers:
            caller = self._callers[call_id]
            if caller is None:
                return None
            parent_id = self._tasks_by_call.get(caller)
        else:
            # The calling worker's messages were not streamed, but Claude Code names its task.
            parent_id = message.get('parent_task_id')
        if parent_id in self._workers:
            return parent_id
        return None

    def _started(self, message: dict) -> list:
        task_id = message.get('task_id')
        if not task_id:
            return []
        call_id = message.get('tool_use_id')
        if call_id:
            self._tasks_by_call[call_id] = task_id
        parent_id = self._parent(message)
        depth = 1
        if parent_id is not None:
            depth = self._workers[parent_id].depth + 1
        label = _worker_label(message)
        if task_id in self._workers:
            # Listed in the background first, before Claude Code said who started it.
            return self._change(task_id, parent_id=parent_id, depth=depth, label=label)
        return self._add(task_id, parent_id, depth, label)

    def _progressed(self, message: dict) -> list:
        task_id = message.get('task_id')
        worker = self._workers.get(task_id)
        if worker is None or worker.state != WORKER_RUNNING:
            return []
        changes = {}
        # Claude Code's own line for the worker's latest step, such as "Reading notes.txt".
        activity = _shorten(message.get('description'), 100)
        if activity:
            changes['activity'] = activity
        tokens = _task_tokens(message)
        if tokens is not None:
            changes['tokens'] = tokens
        return self._change(task_id, **changes)

    def _updated(self, message: dict) -> list:
        task_id = message.get('task_id')
        status = (message.get('patch') or {}).get('status')
        if task_id not in self._workers or not status or status in TASK_LIVE_STATUSES:
            return []
        return self._change(task_id, state=_worker_state(status), activity=None)

    def _ended(self, message: dict) -> list:
        task_id = message.get('task_id')
        if task_id not in self._workers:
            return []
        changes = {'state': _worker_state(message.get('status')), 'activity': None}
        # The worker's final report, which the orchestrator also gets.
        summary = _clip(message.get('summary'), WORKER_SUMMARY_LIMIT)
        if summary:
            changes['summary'] = summary
        tokens = _task_tokens(message)
        if tokens is not None:
            changes['tokens'] = tokens
        return self._change(task_id, **changes)

    def _listed_in_background(self, message: dict) -> list:
        """Add any task listed as running in the background that hasn't started here yet.

        Claude Code may list a background task just before its task_started,
        and a task it lists as running is shown, so nothing runs unseen.
        """
        events = []
        for task in message.get('tasks') or []:
            if not isinstance(task, dict):
                continue
            task_id = task.get('task_id')
            if task_id and task_id not in self._workers:
                events.extend(self._add(task_id, None, 1, _worker_label(task)))
        return events


def _ended_task_id(message: dict) -> str | None:
    """The task that a task_notification reports as ended, or None for any other message."""
    if message.get('type') != 'system' or message.get('subtype') != 'task_notification':
        return None
    return message.get('task_id')


class ClaudeUltraRun:
    """A live Ultra turn's ``claude -p``, through which Buddy stops its workers.

    Claude Code stops a worker when it gets a stop_task control request with
    the worker's task id, and then reports that task as stopped.
    """

    def __init__(self, turn: TurnRequest, process, workers: ClaudeWorkers):
        # The request that was passed to run_turn, which stop_worker() is given.
        self.turn = turn
        self._process = process
        self._workers = workers
        self._stop_requests = 0

    async def stop_worker(self, worker_id: str) -> bool:
        """Ask Claude Code to stop one worker. False when it isn't running, or the CLI has ended."""
        if not self._workers.is_running(worker_id):
            return False
        try:
            await self._send_stop(worker_id)
        except ProcessClosedError:
            return False
        return True

    async def stop_all(self) -> None:
        """Stop every worker still running, before Buddy ends the CLI.

        Asks Claude Code to stop each one, then reads its output until it has
        reported all of them ended, for at most STOP_WORKERS_GRACE seconds.
        The turn has ended by then, so that output is not part of the reply.
        """
        worker_ids = self._workers.running_ids()
        if not worker_ids:
            return
        try:
            async with asyncio.timeout(STOP_WORKERS_GRACE):
                for worker_id in worker_ids:
                    await self._send_stop(worker_id)
                await self._wait_until_ended(worker_ids)
        except TimeoutError:
            log.warning(
                'Claude Code on %s did not report its Ultra workers stopped within %s seconds',
                self.turn.machine.name,
                STOP_WORKERS_GRACE,
            )
        except ProcessClosedError:
            # The CLI has already ended, and its workers with it.
            pass

    async def _send_stop(self, worker_id: str) -> None:
        self._stop_requests += 1
        request = {
            'type': 'control_request',
            'request_id': f'{STOP_REQUEST_ID}-{self._stop_requests}',
            'request': {'subtype': 'stop_task', 'task_id': worker_id},
        }
        await self._process.write(json.dumps(request) + '\n')

    async def _wait_until_ended(self, worker_ids: list[str]) -> None:
        """Read the CLI's output until Claude Code reports each of these workers ended, or the output ends."""
        waiting = set(worker_ids)
        while waiting:
            line = await self._process.read_line()
            if line is None:
                return
            waiting.discard(_ended_task_id(_parse_json_line(line)))


class ClaudeStreamParser:
    """Turns Claude Code stream-json messages into turn events.

    On Ultra turns, once track_workers() is called, it also reports the
    turn's workers, and a result ends the turn only when no worker runs.
    """

    def __init__(self):
        self.session_id: str | None = None
        self.reply_text = ''
        self.error: str | None = None
        self.finished = False
        self.rate_limit: dict | None = None
        # An Ultra turn's workers; None on other turns.
        self.workers: ClaudeWorkers | None = None
        # The usage of an Ultra turn's results so far.
        self._ultra_usage = TokenUsage()
        self._text_block_started = False
        self._reasoning_text = ''
        self._reasoning_block_started = False
        self._streamed_text_since_message = False

    def track_workers(self, options: UltraOptions) -> None:
        """Follow an Ultra turn's workers, once Claude Code knows Buddy's worker types."""
        self.workers = ClaudeWorkers(options)

    def stop_workers(self) -> list:
        """Workers still running when the CLI ends, as stopped with it."""
        if self.workers is None:
            return []
        return self.workers.stop_running()

    def handle(self, message: dict) -> list:
        kind = message.get('type')
        if kind == 'system':
            return self._handle_system(message)
        if kind == 'stream_event':
            return self._handle_stream_event(message)
        if kind == 'assistant':
            return self._handle_assistant(message)
        if kind == 'result':
            return self._handle_result(message)
        if kind == 'rate_limit_event':
            self.rate_limit = message.get('rate_limit_info') or None
        return []

    def _text(self, text: str) -> list:
        if not text:
            return []
        if self._text_block_started:
            self._text_block_started = False
            if self.reply_text and not self.reply_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self.reply_text += text
        self._streamed_text_since_message = True
        return [TextDelta(text)]

    def _reasoning(self, text: str) -> list:
        if not text:
            return []
        if self._reasoning_block_started:
            self._reasoning_block_started = False
            if self._reasoning_text and not self._reasoning_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self._reasoning_text += text
        return [ReasoningDelta(text)]

    def _handle_system(self, message: dict) -> list:
        subtype = message.get('subtype')
        if subtype == 'init':
            self.session_id = message.get('session_id') or self.session_id
        elif subtype == 'api_retry':
            return [StatusUpdate('Claude is busy, retrying…')]
        elif subtype == 'compact_boundary':
            return [StatusUpdate('Summarized earlier messages to stay within the context window')]
        elif self.workers is not None:
            return self.workers.handle(message)
        return []

    def _handle_stream_event(self, message: dict) -> list:
        if message.get('parent_tool_use_id'):
            return []
        event = message.get('event') or {}
        event_type = event.get('type')
        if event_type == 'message_start':
            self._streamed_text_since_message = False
        elif event_type == 'content_block_start':
            block_type = (event.get('content_block') or {}).get('type')
            if block_type == 'text':
                self._text_block_started = True
            elif block_type == 'thinking':
                self._reasoning_block_started = True
        elif event_type == 'content_block_delta':
            delta = event.get('delta') or {}
            if delta.get('type') == 'text_delta':
                return self._text(delta.get('text', ''))
            if delta.get('type') == 'thinking_delta':
                return self._reasoning(delta.get('thinking', ''))
        return []

    def _handle_assistant(self, message: dict) -> list:
        if self.workers is not None:
            # Read before a worker's message is left out: its Agent calls start sub-workers.
            self.workers.note_agent_calls(message)
        if message.get('parent_tool_use_id'):
            return []
        body = message.get('message') or {}
        blocks = body.get('content') or []
        if message.get('error'):
            texts = [block.get('text', '') for block in blocks if block.get('type') == 'text']
            self.error = _readable_error(' '.join(texts) or str(message['error']))
            return []

        events = []
        for block in blocks:
            if block.get('type') == 'text' and not self._streamed_text_since_message:
                # Partial messages were not streamed for this reply; use the full text.
                self._text_block_started = True
                events.extend(self._text(block.get('text', '')))
            elif block.get('type') == 'tool_use':
                description = describe_tool(block.get('name', ''), block.get('input'))
                starts_worker = self.workers is not None and block.get('name') in AGENT_TOOLS
                if not starts_worker:
                    # A worker gets its own status line when its task starts.
                    events.append(StatusUpdate(description))
                self._reasoning_block_started = True
                events.extend(self._reasoning(f'*{description}*'))
        self._streamed_text_since_message = False
        return events

    def _handle_result(self, message: dict) -> list:
        self.session_id = message.get('session_id') or self.session_id
        if message.get('is_error') or message.get('subtype') != 'success':
            result_text = message.get('result') or message.get('subtype') or 'Claude Code reported an error.'
            if not self.error:
                self.error = _readable_error(str(result_text))
        self.finished = self._result_ends_turn()
        usage = message.get('usage')
        if not isinstance(usage, dict):
            return []
        token_usage = _usage_from_result(usage)
        if self.workers is not None:
            # Each result of an Ultra turn reports only its own part, and the
            # last usage a turn reports is the one that counts.
            self._ultra_usage = _usage_sum(self._ultra_usage, token_usage)
            token_usage = self._ultra_usage
        if token_usage.input_tokens == 0 and token_usage.output_tokens == 0:
            return []
        return [token_usage]

    def _result_ends_turn(self) -> bool:
        """Whether the result just read ends the turn.

        A normal turn ends at its first result. An Ultra turn's orchestrator
        may reply while workers still run, and their reports start more
        results, so it ends at a result once no worker runs, or at an error.
        """
        if self.workers is None or self.error:
            return True
        return not self.workers.running()


class ClaudeLogin:
    """A running ``claude auth login`` process and what the user should see."""

    def __init__(self, process):
        self.process = process
        self.state = 'waiting'
        self.url: str | None = None
        self.message: str | None = None
        self.output = ''
        self.watcher: asyncio.Task | None = None

    def to_dict(self) -> dict:
        return {
            'state': self.state,
            'method': 'code',
            'url': self.url,
            'user_code': None,
            'needs_code': self.state == 'waiting',
            'message': self.message,
        }


class ClaudeCodeProvider:
    id = PROVIDER_ID
    name = 'Claude'

    def __init__(self, state_dir: Path):
        self._sessions = SessionStore(state_dir / 'claude-sessions.json')
        self._login: ClaudeLogin | None = None
        # Set by the service before each use: which sign-in the CLI should be using.
        self.credential_mode = CREDENTIAL_MACHINE
        self._usage_windows: dict[str, dict] = {}
        self._limit_reached = False
        # The Ultra turns whose CLI is running, so that their workers can be stopped one by one.
        self._ultra_runs: list[ClaudeUltraRun] = []

    async def stop_worker(self, turn: TurnRequest, worker_id: str) -> bool:
        """Stop one running worker of a live Ultra turn.

        ``turn`` is the request that was passed to run_turn. Returns False when
        that turn's CLI has ended or the worker isn't running any more. Claude
        Code then reports the worker stopped in the turn's own events. Stopping
        the whole turn cancels run_turn instead, which stops every worker.
        """
        for run in self._ultra_runs:
            if run.turn is turn:
                return await run.stop_worker(worker_id)
        return False

    def _record_rate_limit(self, info: dict) -> None:
        self._limit_reached = info.get('status') == 'rejected'
        window = rate_limit_window(info)
        if window:
            self._usage_windows[window['label']] = window

    def usage_summary(self) -> dict | None:
        """Plan usage seen in recent replies; Claude Code reports it only while chatting."""
        if not self._usage_windows and not self._limit_reached:
            return None
        return {
            'plan': None,
            'windows': list(self._usage_windows.values()),
            'limit_reached': self._limit_reached,
        }

    async def _require_cli(self, settings: ProviderSettings, machine) -> str:
        cli = await machine.find_tool(TOOL_CLAUDE, settings.cli_path)
        if not cli:
            raise SubscriptionError(f'{machine.name}: {NOT_INSTALLED_MESSAGE}')
        return cli

    async def status(self, settings: ProviderSettings, machine) -> dict:
        """Sign-in, status, and model checks run in the machine's empty chat folder."""
        cli = await machine.find_tool(TOOL_CLAUDE, settings.cli_path)
        if not cli:
            return {'installed': False, 'signed_in': False, 'message': NOT_INSTALLED_MESSAGE}
        chat_dir = await machine.chat_dir()

        version = ''
        try:
            _, version_output, _ = await machine.run([cli, '--version'], chat_dir, 30)
            version = version_output.strip().split(' ')[0]
        except (OSError, TimeoutError, SubscriptionError) as error:
            log.warning('Could not read the Claude Code version: %s', error)

        try:
            _, output, _ = await machine.run([cli, 'auth', 'status', '--json'], chat_dir, 30)
            info = json.loads(output or '{}')
        except (OSError, TimeoutError, ValueError) as error:
            return {
                'installed': True,
                'cli_path': cli,
                'version': version,
                'signed_in': False,
                'message': f'Could not check the Claude sign-in: {error}',
            }

        auth_method = str(info.get('authMethod') or '')
        status = {
            'installed': True,
            'cli_path': cli,
            'version': version,
            'signed_in': bool(info.get('loggedIn')),
            'account': {
                'email': info.get('email') or info.get('emailAddress'),
                'organization': info.get('orgName') or info.get('organizationName'),
                'plan': info.get('subscriptionType') or info.get('plan'),
                'auth_method': auth_method,
            },
            'usage': self.usage_summary(),
        }
        if status['signed_in']:
            mismatch = _credential_mismatch_message(self.credential_mode, auth_method)
            if mismatch is None:
                status['billing'] = 'api_key' if self.credential_mode == CREDENTIAL_API_KEY else 'plan'
            else:
                # Not the sign-in these models are for, so turns are refused.
                status['api_billing'] = True
                status['message'] = mismatch
        return status

    async def list_models(self, settings: ProviderSettings, machine) -> list[ProviderModel]:
        cli = await self._require_cli(settings, machine)
        args = [
            cli,
            '-p',
            '--input-format',
            'stream-json',
            '--output-format',
            'stream-json',
            '--verbose',
            '--no-session-persistence',
            '--strict-mcp-config',
            '--setting-sources',
            '',
            '--tools',
            '',
        ]
        process = await machine.start_process(args, await machine.chat_dir())
        initialize = {
            'type': 'control_request',
            'request_id': 'buddy-models',
            'request': {'subtype': 'initialize', 'hooks': None},
        }
        entries = []
        try:
            await process.write(json.dumps(initialize) + '\n')
            async with asyncio.timeout(30):
                while True:
                    line = await process.read_line()
                    if line is None:
                        break
                    message = _parse_json_line(line)
                    if message.get('type') == 'control_response':
                        response = (message.get('response') or {}).get('response') or {}
                        entries = response.get('models') or []
                        break
        except (ProcessClosedError, TimeoutError) as error:
            log.warning('Could not list Claude models: %s', error)
        finally:
            process.close_stdin()
            process.kill()

        models = models_from_initialize(entries)
        if models:
            return models
        return list(FALLBACK_MODELS)

    async def start_login(self, settings: ProviderSettings, machine, method: str) -> dict:
        # method is unused: Claude Code only offers the paste-the-code flow.
        # Codex uses it to pick 'browser' or 'device', and both share this signature.
        if self.credential_mode != CREDENTIAL_MACHINE:
            raise SubscriptionError('Claude uses your stored token or key here. Remove it to sign in on the computer.')
        await self.cancel_login()
        cli = await self._require_cli(settings, machine)
        # BROWSER=none keeps the CLI from opening a tab on that computer; Buddy
        # shows the sign-in link instead, which also works from a phone.
        process = await machine.start_process(
            [cli, 'auth', 'login', '--claudeai'],
            await machine.chat_dir(),
            extra_env={'BROWSER': 'none'},
        )
        login = ClaudeLogin(process)
        self._login = login

        try:
            async with asyncio.timeout(30):
                while not login.url:
                    text = await process.read_text()
                    if text is None:
                        break
                    login.output += text
                    urls = LOGIN_URL.findall(login.output)
                    if urls:
                        login.url = urls[-1]
        except TimeoutError:
            pass

        if not login.url:
            process.kill()
            login.state = 'error'
            output = _shorten(login.output or process.stderr_text(), 400)
            login.message = output or 'Claude Code did not provide a sign-in link.'
            return login.to_dict()

        login.watcher = asyncio.create_task(self._watch_login(login))
        return login.to_dict()

    async def _watch_login(self, login: ClaudeLogin) -> None:
        while True:
            text = await login.process.read_text()
            if text is None:
                break
            login.output = (login.output + text)[-4000:]
        returncode = await login.process.wait()
        if login.state != 'waiting':
            return
        if returncode == 0:
            login.state = 'success'
            login.message = 'Signed in to Claude.'
        else:
            login.state = 'error'
            tail = login.output.split('Paste code here if prompted >')[-1]
            login.message = _shorten(tail or login.process.stderr_text(), 400) or 'Sign-in did not finish.'

    async def submit_login_code(self, code: str) -> dict:
        login = self._login
        if not login or login.state != 'waiting':
            raise SubscriptionError('There is no Claude sign-in waiting for a code. Start the sign-in again.')
        lines = code.strip().splitlines()
        code = lines[0].strip() if lines else ''
        if not code:
            raise SubscriptionError('Paste the code shown after you approve the sign-in.')
        await login.process.write(code + '\n')
        try:
            await asyncio.wait_for(asyncio.shield(login.watcher), 60)
        except TimeoutError:
            login.message = 'Still waiting for Claude Code to finish signing in.'
        return login.to_dict()

    def login_state(self) -> dict:
        if not self._login:
            return {'state': 'idle'}
        return self._login.to_dict()

    async def cancel_login(self) -> None:
        login = self._login
        self._login = None
        if not login:
            return
        login.state = 'cancelled'
        login.process.kill()
        if login.watcher:
            login.watcher.cancel()

    async def logout(self, settings: ProviderSettings, machine) -> None:
        cli = await self._require_cli(settings, machine)
        await self.cancel_login()
        returncode, _, error_output = await machine.run([cli, 'auth', 'logout'], await machine.chat_dir(), 60)
        # Only this computer's sessions belonged to the account that signed out.
        self._sessions.forget_prefix(f'{machine.id}|')
        if returncode != 0:
            raise SubscriptionError(_shorten(error_output, 400) or 'Claude Code could not sign out.')

    def _turn_args(self, cli: str, turn: TurnRequest, resume_id: str | None, instructions_path: str) -> list[str]:
        args = [
            cli,
            '-p',
            '--input-format',
            'stream-json',
            '--output-format',
            'stream-json',
            '--verbose',
            '--include-partial-messages',
            '--permission-prompts',
            'none',
            '--system-prompt-snapshot',
            'off',
            '--model',
            turn.model,
        ]

        effort = claude_effort(turn.effort)
        if turn.is_task:
            effort = 'low'
        if effort:
            args.extend(['--effort', effort])

        if turn.is_task:
            args.append('--no-session-persistence')
        if resume_id:
            args.extend(['--resume', resume_id, '--fork-session'])

        if turn.access == ACCESS_CHAT:
            args.extend(['--system-prompt-file', instructions_path])
            args.extend(['--strict-mcp-config', '--disable-slash-commands'])
            args.extend(['--setting-sources', ''])
            if turn.ultra is not None:
                # The orchestrator gets only the Agent tool. Workers inherit
                # it, so they can only think and start sub-workers.
                args.extend(['--tools', ULTRA_CHAT_TOOLS])
            else:
                args.extend(['--tools', ''])
        elif turn.access == ACCESS_READ:
            # The working folder may be someone else's repository. Its
            # .claude/settings.json hooks and .mcp.json servers would run as
            # this user, so read mode loads no settings files, MCP servers, or
            # skills, and --restricted keeps file tools inside the folder.
            args.extend(['--append-system-prompt-file', instructions_path])
            args.append('--restricted')
            args.extend(['--strict-mcp-config', '--disable-slash-commands'])
            args.extend(['--setting-sources', ''])
            if turn.ultra is not None:
                # The read tools plus Agent, which starts workers. Agent is
                # approved up front, like WebSearch.
                args.extend(['--tools', ULTRA_READ_TOOLS])
                args.extend(['--allowedTools', 'WebSearch,Agent'])
            else:
                args.extend(['--tools', READ_ONLY_TOOLS])
                args.extend(['--allowedTools', 'WebSearch'])
        else:
            args.extend(['--append-system-prompt-file', instructions_path])
            args.append('--dangerously-skip-permissions')
            # The person's user settings can name another account, such as an apiKeyHelper or an
            # ANTHROPIC_BASE_URL for work, which would win over their plan's sign-in. Plan turns
            # always run on the plan, so only the folder's own settings load.
            args.extend(['--setting-sources', 'project,local'])
        return args

    def _turn_env(self, turn: TurnRequest) -> dict[str, str]:
        """Environment variables for this turn's ``claude -p``, on top of the computer's own."""
        if turn.is_task:
            # Titles and follow-ups run with no tools, so they cannot start agents.
            return {}
        if turn.ultra is not None:
            # Ultra turns allow background workers, because they are read and
            # listed until every worker ends.
            return ultra_env(turn.ultra)
        # A normal turn is read only up to its first result, and then the CLI
        # is ended. An agent left running in the background would be stopped
        # without anyone seeing it, so agents stay in the foreground.
        return {'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'}

    def _instructions(self, turn: TurnRequest) -> str:
        system = turn.conversation.system
        if turn.access == ACCESS_CHAT:
            return f'{CHAT_INSTRUCTIONS}\n\n{system}'.strip()
        return system or 'The user is chatting with you from Buddy, their personal chat app.'

    def _session_key(self, turn: TurnRequest, turns: list[ChatTurn]) -> str:
        # Claude Code keeps sessions per machine and working folder, so a
        # session can only be resumed where it was created.
        return f'{turn.machine.id}|{turn.cwd}|{conversation_key(turns)}'

    async def _define_workers(self, process, turn: TurnRequest) -> list[str] | None:
        """Define Buddy's worker types for an Ultra turn, before its prompt is sent.

        Returns the agent types Claude Code then lists, or None when its output
        ends before it answers. When it doesn't answer within
        WORKERS_ANSWER_TIMEOUT seconds, nothing counts as listed.
        """
        request = {
            'type': 'control_request',
            'request_id': WORKERS_REQUEST_ID,
            'request': {'subtype': 'initialize', 'hooks': None, 'agents': worker_types(turn)},
        }
        await process.write(json.dumps(request) + '\n')
        try:
            async with asyncio.timeout(WORKERS_ANSWER_TIMEOUT):
                while True:
                    line = await process.read_line()
                    if line is None:
                        return None
                    answer = _control_answer(_parse_json_line(line), WORKERS_REQUEST_ID)
                    if answer is not None:
                        return _agent_names(answer)
        except TimeoutError:
            log.warning('Claude Code on %s did not answer the request that defines Ultra workers', turn.machine.name)
            return []

    async def _run_attempt(self, cli: str, turn: TurnRequest, resume_id: str | None, attempt: 'ClaudeAttempt'):
        """Run the reply once, yielding events and recording the outcome in ``attempt``.

        When Claude Code on that computer can't run Ultra's workers, the reply
        runs again without Ultra, after a status line that says why.
        """
        async for event in self._run_cli(cli, turn, resume_id, attempt):
            yield event
        if attempt.ultra_off_reason is None:
            return
        yield StatusUpdate(ultra.OFF_STATUS.format(reason=attempt.ultra_off_reason))
        async for event in self._run_cli(cli, without_ultra(turn), resume_id, attempt):
            yield event

    async def _run_cli(self, cli: str, turn: TurnRequest, resume_id: str | None, attempt: 'ClaudeAttempt'):
        """Run ``claude -p`` once, yielding events and recording the outcome in ``attempt``.

        An Ultra turn first defines Buddy's worker types. When Claude Code then
        doesn't list buddy-worker, the run ends before the prompt is sent, with
        the reason in ``attempt.ultra_off_reason``. Otherwise its workers are
        reported as they run, and it is read past results until none runs, for
        at most ULTRA_TIME_LIMIT seconds. Meanwhile stop_worker() can stop
        them one by one, and when the turn ends while some still run, they are
        stopped before the CLI is killed.
        """
        parser = attempt.parser
        process = await turn.machine.start_process(
            self._turn_args(cli, turn, resume_id, temp_file_arg(INSTRUCTIONS_FILE)),
            turn.cwd,
            extra_env=self._turn_env(turn),
            temp_files={INSTRUCTIONS_FILE: self._instructions(turn)},
        )
        # Stays False when the caller stops the reply (Stop, a closed chat) or
        # something fails here. The CLI keeps working after stdin closes, so
        # then it is killed at once instead of being given time to exit.
        output_ended = False
        # An Ultra turn's live CLI, once Claude Code knows Buddy's worker types.
        run = None
        try:
            if turn.ultra is not None:
                agents = await self._define_workers(process, turn)
                if agents is None:
                    # Claude Code ended before it answered; its stderr says why.
                    output_ended = True
                    return
                if ultra.WORKER_AGENT not in agents:
                    attempt.ultra_off_reason = ULTRA_TOO_OLD_REASON.format(computer=turn.machine.name)
                    return
                parser.track_workers(turn.ultra)
                run = ClaudeUltraRun(turn, process, parser.workers)
                self._ultra_runs.append(run)
            await process.write(json.dumps(turn_message(turn, resume_id)) + '\n')
            deadline = _reply_deadline(turn)
            while not parser.finished:
                try:
                    line = await _read_line_by(process, deadline)
                except TimeoutError:
                    log.warning('Stopping an Ultra turn on %s that ran past its time limit', turn.machine.name)
                    attempt.timed_out = True
                    break
                if line is None:
                    break
                for event in parser.handle(_parse_json_line(line)):
                    yield event
                if parser.rate_limit:
                    self._record_rate_limit(parser.rate_limit)
                    parser.rate_limit = None
            # Past the time limit the CLI is still working, so it is killed at once.
            output_ended = not attempt.timed_out
        except ProcessClosedError:
            attempt.exited_early = True
            output_ended = True
        finally:
            try:
                if run is not None:
                    self._ultra_runs.remove(run)
                    # Workers still running, as when the person stops the reply,
                    # are stopped first, so Claude Code ends them itself.
                    await run.stop_all()
                process.close_stdin()
                if output_ended and (parser.error or not parser.finished):
                    # The CLI is exiting with an error; it explains why on stderr.
                    await process.finish_output(5)
            finally:
                # Runs even when a second cancellation interrupts a wait.
                process.kill()
                attempt.stderr = process.stderr_text()
        # The workers still running when a turn ended by error, exit or time limit stopped with it.
        for event in parser.stop_workers():
            yield event

    async def run_turn(self, turn: TurnRequest):
        """Run one chat turn and yield its events."""
        cli = await self._require_cli(turn.settings, turn.machine)
        conversation = turn.conversation
        resume_id = None
        if not turn.is_task:
            resume_id = self._sessions.get(self._session_key(turn, conversation.history))

        attempt = ClaudeAttempt()
        if resume_id:
            async for event in self._run_attempt(cli, turn, resume_id, attempt):
                yield event
            if attempt.ultra_off_reason:
                # Claude Code there can't run Ultra, so a fresh session runs without it too.
                turn = without_ultra(turn)
            if attempt.session_missing():
                log.info('Claude session %s no longer exists; starting from the chat text', resume_id)
                resume_id = None
                attempt = ClaudeAttempt()

        if not resume_id:
            async for event in self._run_attempt(cli, turn, None, attempt):
                yield event

        failure = attempt.failure()
        if failure:
            yield TurnFailed(failure)
        elif attempt.parser.session_id and not turn.is_task:
            reply = ChatTurn('assistant', attempt.parser.reply_text)
            answered = conversation.history + [conversation.prompt, reply]
            self._sessions.put(self._session_key(turn, answered), attempt.parser.session_id)


class ClaudeAttempt:
    """What one attempt at the reply produced."""

    def __init__(self):
        self.parser = ClaudeStreamParser()
        self.stderr = ''
        self.exited_early = False
        # Why the reply ran without Ultra although the person turned it on.
        self.ultra_off_reason: str | None = None
        # An Ultra turn ran past ULTRA_TIME_LIMIT and was stopped.
        self.timed_out = False

    def session_missing(self) -> bool:
        """The resumed session was deleted or belongs to another folder."""
        if self.parser.reply_text:
            return False
        output = f'{self.stderr} {self.parser.error or ""}'.lower()
        return 'no conversation found' in output

    def failure(self) -> str | None:
        details = _shorten(self.stderr, 600)
        if self.parser.error:
            # Results such as "error_during_execution" explain themselves on stderr.
            if self.parser.error.startswith('error_') and details:
                return details
            return self.parser.error
        if self.timed_out:
            return ULTRA_TIME_LIMIT_MESSAGE
        if self.exited_early:
            return f'Claude Code exited unexpectedly. {details}'.strip()
        if not self.parser.finished:
            return f'Claude Code stopped before finishing the reply. {details}'.strip()
        return None


def models_from_initialize(entries: list) -> list[ProviderModel]:
    """Build the model list from Claude Code's initialize response."""
    models = []
    used_keys = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get('value')
        if not value or value == 'default':
            continue

        display_name = entry.get('displayName') or value
        segments = [segment.strip() for segment in str(entry.get('description') or '').split('·')]
        versioned_name = segments[0] or display_name
        # Per-token prices describe API billing, which does not apply to a plan.
        details = [segment for segment in segments[1:] if segment and '$' not in segment]

        key = model_key(display_name)
        if key in used_keys:
            key = model_key(value)
        used_keys.add(key)

        models.append(
            ProviderModel(
                key=key,
                value=value,
                name=f'Claude {versioned_name}',
                description=' · '.join(details),
                efforts=list(entry.get('supportedEffortLevels') or []),
            )
        )
    return models


def _reply_deadline(turn: TurnRequest) -> float | None:
    """When an Ultra turn that started now is stopped, on the event loop's clock; None for other turns."""
    if turn.ultra is None:
        return None
    return asyncio.get_running_loop().time() + ULTRA_TIME_LIMIT


async def _read_line_by(process, deadline: float | None) -> str | None:
    """The CLI's next output line, or None once its output ends.

    Raises TimeoutError when the deadline passes first.
    """
    if deadline is None:
        return await process.read_line()
    async with asyncio.timeout_at(deadline):
        return await process.read_line()


def _parse_json_line(line: str) -> dict:
    line = line.strip()
    if not line.startswith('{'):
        return {}
    try:
        message = json.loads(line)
    except ValueError:
        return {}
    if isinstance(message, dict):
        return message
    return {}
