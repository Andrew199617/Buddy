"""Offline tests: Codex Ultra turns, replayed from recorded codex app-server streams.

The recordings under fixtures/ultra are what the app-server of Codex 0.153.4
wrote on stdout for Ultra turns recorded on 2026-10-20, each on a persistent
thread with a read-only sandbox:

- codex-p.jsonl: two workers at once, which both finish before the orchestrator replies.
- codex-stop-children.jsonl: two workers that write long essays. The
  orchestrator's turn was interrupted while they wrote, and they wrote on until
  each one was interrupted too.
- codex-depth.jsonl: a worker that starts a sub-worker, which starts one more.
  Nothing interrupted that third level, which ran to the end.

Codex keeps a thread's workers when its turn ends, and the thread's next turn
may give one of them more to do. No such turn was recorded, so follow_up()
writes one for codex-p.jsonl's thread from the shape of the recorded turns.

Each one answers initialize, model/list (codex-p.jsonl only), thread/start and
turn/start, in that order, and then streams the turn. The orchestrator's thread
and each worker's own thread report under their own thread ids. The recording
session sent the interrupts, not Buddy, and their answers were left out. So
were the notifications a turn doesn't read that carry the account's rate limits
or the computer's remote-control status, and paths name the user "fixture".
Where Buddy interrupts a turn, InterruptibleProcess ends it the way Codex ended
the turns that the recording session interrupted.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_codex.py -v
"""

import asyncio
import copy
import dataclasses
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from open_webui.utils.subscriptions import codex, ultra  # noqa: E402
from open_webui.utils.subscriptions.common import (  # noqa: E402
    CHAT_INSTRUCTIONS,
    ProviderSettings,
    TurnRequest,
    UltraOptions,
)
from open_webui.utils.subscriptions.conversation import ChatTurn, parse_messages  # noqa: E402
from open_webui.utils.subscriptions.events import (  # noqa: E402
    WORKER_DONE,
    WORKER_RUNNING,
    WORKER_STOPPED,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
    WorkerUpdate,
)

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'ultra'
PROMPT = 'What is 17 times 23, and what are three prime numbers above 100?'
FOLDER_INSTRUCTIONS = 'Folder instructions: answer in plain words.'
PROJECT = 'C:\\code\\site'
PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgo='
# The chat's model in the recording. Its model list offers it every level up to 'ultra'.
CHAT_MODEL = 'gpt-6-astra'
# Other models in the recording's model list. Sol and Terra offer the same
# levels as the chat's model and run Codex's v2 workers, as it does.
SOL = 'gpt-5.6-sol'
TERRA = 'gpt-5.6-terra'
SOL_LOW = UltraOptions(worker_model=SOL, worker_label='GPT-5.6-Sol', worker_effort='low')
# The recording's one model that runs Codex's v1 workers, which no recorded Ultra turn ran.
LUNA = 'gpt-5.6-luna'
SAME_AS_CHAT = UltraOptions()
# What each recorded answer's result holds, which says the request it answers.
# The recordings number their requests differently.
ANSWER_KEYS = {'initialize': 'userAgent', 'model/list': 'data', 'thread/start': 'thread', 'turn/start': 'turn'}
# The Codex version that the recordings' app-server reported at initialize.
CODEX_VERSION = '0.153.4'
# The recordings' userAgent, as Codex 0.153.3 would have reported it.
OLDER_USER_AGENT = 'buddy/0.153.3 (Windows 10.0.26200; x86_64) xterm-256color (buddy; 1.0)'


def recorded(name: str) -> list[dict]:
    """The messages of a recorded app-server stream, one per stdout line."""
    lines = (FIXTURES / name).read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines]


def recorded_answer(messages: list[dict], method: str) -> dict:
    """The recording's answer to its request for ``method``."""
    key = ANSWER_KEYS[method]
    for message in messages:
        if 'method' not in message and key in (message.get('result') or {}):
            return message
    raise AssertionError(f'The recording has no answer to {method}')


def with_user_agent(messages: list[dict], user_agent: str | None) -> list[dict]:
    """A copy of a recording whose initialize answer reports another userAgent, or a null one."""
    messages = copy.deepcopy(messages)
    recorded_answer(messages, 'initialize')['result']['userAgent'] = user_agent
    return messages


def orchestrator_thread(messages: list[dict]) -> str:
    """The thread of the recorded Ultra turn's orchestrator."""
    return recorded_answer(messages, 'thread/start')['result']['thread']['id']


def thread_of(message: dict) -> str | None:
    return (message.get('params') or {}).get('threadId')


def final_answer(messages: list[dict], thread_id: str) -> str:
    """The last message of a thread's turn, as its recorded turn/completed lists it."""
    return turn_completion(messages, thread_id)['params']['turn']['items'][-1]['text']


def turn_completion(messages: list[dict], thread_id: str) -> dict:
    """The recording's turn/completed for a thread's turn."""
    for message in messages:
        if message.get('method') == 'turn/completed' and thread_of(message) == thread_id:
            return message
    raise AssertionError(f'The recording has no completed turn on thread {thread_id}')


def turn_start(messages: list[dict], thread_id: str) -> dict:
    """The recording's turn/started for a thread's turn."""
    for message in messages:
        if message.get('method') == 'turn/started' and thread_of(message) == thread_id:
            return message
    raise AssertionError(f'The recording has no started turn on thread {thread_id}')


def agent_messages(messages: list[dict], thread_id: str) -> list[str]:
    """The text of each message that a thread's turn wrote, as its item/completed has it."""
    texts = []
    for message in messages:
        if message.get('method') != 'item/completed' or thread_of(message) != thread_id:
            continue
        item = message['params']['item']
        if item.get('type') == 'agentMessage':
            texts.append(item['text'])
    return texts


def recorded_workers(messages: list[dict]) -> dict[str, str]:
    """The thread of each worker started in the recording, by its agent path, in the order they started."""
    workers = {}
    for message in messages:
        item = (message.get('params') or {}).get('item') or {}
        if message.get('method') != 'item/started' or item.get('type') != 'subAgentActivity':
            continue
        if item['kind'] == 'started':
            workers[item['agentPath']] = item['agentThreadId']
    return workers


def completion_reports(messages: list[dict], worker_id: str) -> list[dict]:
    """The notifications in which the thread that started a worker reports a turn of that worker over."""
    reports = []
    for message in messages:
        item = (message.get('params') or {}).get('item') or {}
        if item.get('type') != 'subAgentActivity' or item['kind'] != 'completed':
            continue
        if item['agentThreadId'] == worker_id:
            reports.append(message)
    return reports


class ReplayProcess:
    """A ``codex app-server`` process that answers the way the recorded one did.

    initialize, model/list and thread/start get the recorded answers, under the
    ids of Buddy's own requests. Once the turn starts, it writes out everything
    the recording has after its thread/start answer, with the turn/start answer
    under Buddy's id. config/read, which the recording never sent, gets an empty
    config, and thread/unsubscribe and turn/interrupt an empty answer. Every
    request is kept in ``requests``. With ``exits``, its output ends after the
    turn's, as when the app-server exits.
    """

    def __init__(self, messages: list[dict], exits: bool = False):
        self.messages = messages
        self.exits = exits
        self.requests = []
        self.returncode = None
        self._lines = asyncio.Queue()

    def _answer(self, request_id, result) -> None:
        self._lines.put_nowait(json.dumps({'id': request_id, 'result': result}))

    def _write_turn(self, request_id) -> None:
        for message in self._turn_messages(request_id):
            self._lines.put_nowait(json.dumps(message))
        if self.exits:
            self._lines.put_nowait(None)

    def _turn_messages(self, request_id) -> list[dict]:
        """Everything the recording has after its thread/start answer, with the turn/start answer under Buddy's id."""
        start = self.messages.index(recorded_answer(self.messages, 'thread/start')) + 1
        turn_answer = recorded_answer(self.messages, 'turn/start')
        messages = []
        for message in self.messages[start:]:
            if message is turn_answer:
                message = {**message, 'id': request_id}
            messages.append(message)
        return messages

    async def write(self, text):
        message = json.loads(text)
        method = message['method']
        if 'id' not in message:
            # The initialized notification needs no answer.
            return
        self.requests.append((method, message.get('params')))
        if method in ('initialize', 'model/list', 'thread/start'):
            self._answer(message['id'], recorded_answer(self.messages, method)['result'])
        elif method == 'config/read':
            self._answer(message['id'], {'config': {}})
        elif method in ('thread/unsubscribe', 'turn/interrupt'):
            self._answer(message['id'], {})
        elif method == 'turn/start':
            self._write_turn(message['id'])
        else:
            raise AssertionError(f'The recording has no answer to {method}')

    async def read_line(self):
        return await self._lines.get()

    def stderr_text(self):
        return ''

    def close_stdin(self):
        pass

    def kill(self):
        self.returncode = 1
        # Its output ends, so Buddy's reader does too.
        self._lines.put_nowait(None)

    def sent(self, method: str) -> list[dict]:
        return [params for sent_method, params in self.requests if sent_method == method]

    def drained(self) -> bool:
        """Whether Buddy has read everything it has written so far."""
        return self._lines.empty()


def interrupted_completion(thread_id: str, turn_id: str) -> dict:
    """The turn/completed with which Codex ends a turn that turn/interrupt stopped.

    That is how codex-stop-children.jsonl recorded it, without its times.
    """
    turn = {'id': turn_id, 'items': [], 'itemsView': 'notLoaded', 'status': 'interrupted', 'error': None}
    return {'method': 'turn/completed', 'params': {'threadId': thread_id, 'turn': turn}}


class InterruptibleProcess(ReplayProcess):
    """A replayed app-server that ends a turn when Buddy interrupts it, as Codex did.

    It answers Buddy's turn/interrupt, and then ends that turn with a
    turn/completed whose status is 'interrupted', unless the turn has ended
    already. The turns of ``waiting`` threads that the recording session
    interrupted end only that way: each one's recorded turn/completed is
    held back until Buddy interrupts it.
    """

    def __init__(self, messages: list[dict], waiting=()):
        # The recorded turn/completed of each held-back turn, by (thread id, turn id).
        self._held_back = {}
        replayed = []
        for message in messages:
            turn = (message.get('params') or {}).get('turn') or {}
            if message.get('method') == 'turn/completed' and thread_of(message) in waiting:
                if turn.get('status') == 'interrupted':
                    self._held_back[(thread_of(message), turn['id'])] = message
                    continue
            replayed.append(message)
        super().__init__(replayed)
        # The turns that end in what it replays, which it writes out as soon as the turn starts.
        self._ended = set()
        for message in replayed:
            if message.get('method') == 'turn/completed':
                self._ended.add((thread_of(message), message['params']['turn']['id']))

    async def write(self, text):
        message = json.loads(text)
        if message.get('method') != 'turn/interrupt':
            await super().write(text)
            return
        self.requests.append(('turn/interrupt', message['params']))
        self._interrupted(message['id'], message['params'])

    def _interrupted(self, request_id, params: dict) -> None:
        """Answer a turn/interrupt, and end its turn unless it has ended already."""
        self._answer(request_id, {})
        turn = (params['threadId'], params['turnId'])
        if turn in self._ended:
            return
        self._ended.add(turn)
        completion = self._held_back.pop(turn, None) or interrupted_completion(*turn)
        self._lines.put_nowait(json.dumps(completion))


class SlowInterruptProcess(InterruptibleProcess):
    """An interruptible app-server that answers turn/interrupt on ``slow_thread`` only once release() is called.

    In the recorded stops, Codex took about 4 seconds to answer the
    orchestrator's turn/interrupt, and milliseconds to answer each worker's.
    A busy computer or a Runner's connection can take longer still.
    """

    def __init__(self, messages: list[dict], waiting, slow_thread: str):
        super().__init__(messages, waiting=waiting)
        self.slow_thread = slow_thread
        # The turn/interrupt requests on slow_thread not answered yet, as (request id, params).
        self._unanswered = []

    def _interrupted(self, request_id, params: dict) -> None:
        if params['threadId'] == self.slow_thread:
            self._unanswered.append((request_id, params))
            return
        super()._interrupted(request_id, params)

    def release(self) -> None:
        """Answer the held turn/interrupt requests, and end their turns."""
        unanswered = self._unanswered
        self._unanswered = []
        for request_id, params in unanswered:
            super()._interrupted(request_id, params)


class PausedProcess(InterruptibleProcess):
    """An interruptible app-server that writes its recorded turn up to ``pause_after``, and the rest on resume()."""

    def __init__(self, messages: list[dict], waiting, pause_after: dict):
        super().__init__(messages, waiting=waiting)
        self.pause_after = pause_after
        # What the turn has still to write, until resume() writes it.
        self._rest = []

    def _write_turn(self, request_id) -> None:
        messages = self._turn_messages(request_id)
        cut = messages.index(self.pause_after) + 1
        for message in messages[:cut]:
            self._lines.put_nowait(json.dumps(message))
        self._rest = messages[cut:]

    def resume(self) -> None:
        rest = self._rest
        self._rest = []
        for message in rest:
            self._lines.put_nowait(json.dumps(message))


class FollowUpProcess(InterruptibleProcess):
    """An interruptible replayed app-server whose thread runs one more turn after the recorded one.

    That turn writes ``follow_up``, whose first message answers its turn/start.
    """

    def __init__(self, messages: list[dict], follow_up: list[dict]):
        super().__init__(messages)
        self.follow_up = follow_up
        self._turns_written = 0
        for message in follow_up:
            if message.get('method') == 'turn/completed':
                self._ended.add((thread_of(message), message['params']['turn']['id']))

    def _write_turn(self, request_id) -> None:
        self._turns_written += 1
        if self._turns_written == 1:
            super()._write_turn(request_id)
            return
        answer, *rest = self.follow_up
        self._lines.put_nowait(json.dumps({**answer, 'id': request_id}))
        for message in rest:
            self._lines.put_nowait(json.dumps(message))


class ReplayMachine:
    id = 'fixture-pc'
    name = 'Fixture PC'

    def __init__(self, process):
        self.process = process

    async def find_tool(self, tool, configured_path=''):
        return 'codex'

    async def chat_dir(self):
        return '.'

    async def start_process(self, args, cwd, extra_env=None, temp_files=None):
        return self.process


class CountingAppServer:
    """An app-server that numbers its threads and answers each turn with one message.

    Its model list and version are the recording's.
    """

    generation = 1
    running = True
    version = CODEX_VERSION

    def __init__(self):
        self.requests = []
        self.queues = {}
        # The threads whose workers' notifications Buddy asked for.
        self.followed = []
        self.thread_count = 0
        self.turn_count = 0
        self.models = recorded_answer(recorded('codex-p.jsonl'), 'model/list')['result']

    async def request(self, method, params=None, timeout=None):
        self.requests.append((method, params))
        if method == 'model/list':
            return self.models
        if method == 'config/read':
            return {'config': {'mcp_servers': {'node_repl': {}}}}
        if method == 'thread/start':
            self.thread_count += 1
            return {'thread': {'id': f'thread-{self.thread_count}'}}
        if method == 'turn/start':
            return self._answer_turn(params['threadId'])
        if method == 'thread/inject_items' or method == 'thread/unsubscribe':
            return {}
        raise AssertionError(f'Unexpected app-server request: {method}')

    def _answer_turn(self, thread_id: str) -> dict:
        self.turn_count += 1
        turn_id = f'turn-{self.turn_count}'
        queue = self.queues[thread_id]
        delta = {
            'threadId': thread_id,
            'turnId': turn_id,
            'itemId': f'message-{self.turn_count}',
            'delta': f'Reply {self.turn_count}',
        }
        queue.put_nowait({'method': 'item/agentMessage/delta', 'params': delta})
        completed = {'threadId': thread_id, 'turn': {'id': turn_id, 'status': 'completed'}}
        queue.put_nowait({'method': 'turn/completed', 'params': completed})
        return {'turn': {'id': turn_id}}

    def subscribe(self, thread_id):
        queue = asyncio.Queue()
        self.queues[thread_id] = queue
        return queue

    def follow_workers(self, thread_id):
        self.followed.append(thread_id)

    def unsubscribe(self, thread_id):
        self.queues.pop(thread_id, None)

    def sent(self, method: str) -> list[dict]:
        return [params for sent_method, params in self.requests if sent_method == method]


def chat_messages(earlier=(), prompt=PROMPT) -> list[dict]:
    return [{'role': 'system', 'content': FOLDER_INSTRUCTIONS}, *earlier, {'role': 'user', 'content': prompt}]


def ultra_turn(machine=None, access='read', options=SOL_LOW, effort='low', earlier=(), prompt=PROMPT):
    """An Ultra turn as the server hands it on, with the orchestrator note ahead of the chat's instructions."""
    conversation = parse_messages(chat_messages(earlier, prompt))
    noted = ultra.with_orchestrator_note(conversation.system, codex.PROVIDER_ID, access, options)
    return TurnRequest(
        model=CHAT_MODEL,
        conversation=dataclasses.replace(conversation, system=noted),
        settings=ProviderSettings(enable=True, access=access),
        cwd=PROJECT,
        effort=effort,
        machine=machine,
        ultra_options=options,
    )


def plain_turn(machine=None, access='read', effort='low', earlier=(), prompt=PROMPT):
    """A turn without Ultra, in the same chat as ultra_turn()."""
    conversation = parse_messages(chat_messages(earlier, prompt))
    settings = ProviderSettings(enable=True, access=access)
    return TurnRequest(CHAT_MODEL, conversation, settings, PROJECT, effort=effort, machine=machine)


async def collect(events) -> list:
    return [event async for event in events]


def reply(events: list) -> str:
    return ''.join(event.text for event in events if isinstance(event, TextDelta))


def failures(events: list) -> list[str]:
    return [event.message for event in events if isinstance(event, TurnFailed)]


def usages(events: list) -> list[TokenUsage]:
    return [event for event in events if isinstance(event, TokenUsage)]


def worker_updates(events: list, worker_id: str) -> list[WorkerUpdate]:
    return [event for event in events if isinstance(event, WorkerUpdate) and event.worker_id == worker_id]


def latest_workers(events: list) -> dict[str, WorkerUpdate]:
    """Each worker's last update, in the order the workers were first reported."""
    latest = {}
    for event in events:
        if isinstance(event, WorkerUpdate):
            latest[event.worker_id] = event
    return latest


def activities(events: list, worker_id: str) -> list[str | None]:
    """What a worker was doing, in order, each time that changed."""
    seen = []
    for update in worker_updates(events, worker_id):
        if not seen or seen[-1] != update.activity:
            seen.append(update.activity)
    return seen


def states(events: list, worker_id: str) -> list[str]:
    """A worker's states, in order, each time that changed."""
    seen = []
    for update in worker_updates(events, worker_id):
        if not seen or seen[-1] != update.state:
            seen.append(update.state)
    return seen


def first_update_index(events: list, worker_id: str, state: str) -> int:
    """Where in the turn's events a worker was first reported in this state."""
    for index, event in enumerate(events):
        if isinstance(event, WorkerUpdate) and event.worker_id == worker_id and event.state == state:
            return index
    raise AssertionError(f'{worker_id} was never {state}')


def text_input(text: str) -> dict:
    return {'type': 'text', 'text': text, 'text_elements': []}


def start_report(messages: list[dict], worker_id: str) -> dict:
    """The notification in which the thread that started a worker first reports it."""
    for message in messages:
        item = (message.get('params') or {}).get('item') or {}
        if message.get('method') != 'item/started' or item.get('type') != 'subAgentActivity':
            continue
        if item['kind'] == 'started' and item['agentThreadId'] == worker_id:
            return message
    raise AssertionError(f'The recording never reports {worker_id} started')


# The person's next message, and what follow_up() has the orchestrator and its worker write.
FOLLOW_UP_PROMPT = 'And 18 times 23?'
FOLLOW_UP_REPORT = '18 \u00d7 23 = 414.'
FOLLOW_UP_REPLY = '18 \u00d7 23 = **414**.'
# The turns that follow_up() writes on the orchestrator's thread and on its worker's.
FOLLOW_UP_TURN = 'follow-up-turn'
FOLLOW_UP_WORKER_TURN = 'follow-up-worker-turn'


def last_usage_total(messages: list[dict], thread_id: str) -> dict:
    """A thread's last token usage total in a recording."""
    totals = [
        message['params']['tokenUsage']['total']
        for message in messages
        if message.get('method') == 'thread/tokenUsage/updated' and thread_of(message) == thread_id
    ]
    return totals[-1]


def grown(total: dict, input_tokens: int, output_tokens: int) -> dict:
    """A thread's token usage total once it has used this many more tokens."""
    return {
        **total,
        'inputTokens': total['inputTokens'] + input_tokens,
        'outputTokens': total['outputTokens'] + output_tokens,
        'totalTokens': total['totalTokens'] + input_tokens + output_tokens,
    }


def usage_update(thread_id: str, turn_id: str, total: dict) -> dict:
    """The notification in which a thread reports its token usage total."""
    token_usage = {'total': total, 'last': None, 'modelContextWindow': 258400}
    return {
        'method': 'thread/tokenUsage/updated',
        'params': {'threadId': thread_id, 'turnId': turn_id, 'tokenUsage': token_usage},
    }


def follow_up(messages: list[dict], worker_id: str, ends: bool = True) -> list[dict]:
    """The next turn on a recorded Ultra turn's thread, which gives one of its workers more to do.

    Codex 0.153.4 does that with its followup_task tool, but no turn of it
    was recorded. So the orchestrator's thread here reports only its own
    turn, and the worker's thread a turn shaped like its recorded one: it
    writes FOLLOW_UP_REPORT, using 2000 tokens in and 10 out, and the
    orchestrator then uses 3000 in and 20 out to reply. With ``ends`` False,
    the stream stops once the worker's turn has started, while both turns run.
    """
    orchestrator = orchestrator_thread(messages)
    turn = {'id': FOLLOW_UP_TURN, 'items': [], 'itemsView': 'notLoaded', 'status': 'inProgress', 'error': None}
    worker_turn = {**turn, 'id': FOLLOW_UP_WORKER_TURN}
    report = {'type': 'agentMessage', 'id': 'follow-up-report', 'text': ''}
    stream = [
        {'id': None, 'result': {'turn': turn}},
        {'method': 'turn/started', 'params': {'threadId': orchestrator, 'turn': turn}},
        {'method': 'turn/started', 'params': {'threadId': worker_id, 'turn': worker_turn}},
        {'method': 'item/started', 'params': {'threadId': worker_id, 'turnId': FOLLOW_UP_WORKER_TURN, 'item': report}},
    ]
    if not ends:
        return stream
    worker_total = grown(last_usage_total(messages, worker_id), 2000, 10)
    orchestrator_total = grown(last_usage_total(messages, orchestrator), 3000, 20)
    reported = {**report, 'text': FOLLOW_UP_REPORT}
    delta = {'threadId': orchestrator, 'turnId': FOLLOW_UP_TURN, 'itemId': 'follow-up-reply', 'delta': FOLLOW_UP_REPLY}
    stream += [
        {
            'method': 'item/completed',
            'params': {'threadId': worker_id, 'turnId': FOLLOW_UP_WORKER_TURN, 'item': reported},
        },
        usage_update(worker_id, FOLLOW_UP_WORKER_TURN, worker_total),
        {'method': 'turn/completed', 'params': {'threadId': worker_id, 'turn': {**worker_turn, 'status': 'completed'}}},
        {'method': 'item/agentMessage/delta', 'params': delta},
        usage_update(orchestrator, FOLLOW_UP_TURN, orchestrator_total),
        {'method': 'turn/completed', 'params': {'threadId': orchestrator, 'turn': {**turn, 'status': 'completed'}}},
    ]
    return stream


def ultra_parser(messages: list[dict]) -> codex.CodexTurnParser:
    """A parser for a recorded Ultra turn that follows its workers."""
    turn_id = recorded_answer(messages, 'turn/start')['result']['turn']['id']
    parser = codex.CodexTurnParser(turn_id, orchestrator_thread(messages))
    parser.track_workers(SAME_AS_CHAT)
    return parser


def feed(parser: codex.CodexTurnParser, messages: list[dict]) -> list[tuple[dict, tuple[str, str]]]:
    """Hand recorded notifications to a parser.

    Returns each turn that it hands over to interrupt, with the notification after which it did.
    """
    handed_over = []
    for message in messages:
        if 'method' not in message:
            continue
        parser.handle(message)
        for interrupt in parser.take_interrupts():
            handed_over.append((message, interrupt))
    return handed_over


async def until(condition) -> None:
    """Let the other tasks run until ``condition()`` holds."""
    while not condition():
        await asyncio.sleep(0)


class FakeMachine:
    id = 'fixture-pc'
    name = 'Fixture PC'


class UltraThreadTests(unittest.IsolatedAsyncioTestCase):
    """Ultra turns start a thread of their own, with Codex's workers on."""

    def setUp(self):
        self.provider = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(self.provider.stop)

    def use_server(self, server) -> None:
        self.provider._server_for = AsyncMock(return_value=server)

    async def run_turn(self, turn) -> list:
        return await collect(self.provider.run_turn(turn))

    async def thread_start(self, turn) -> dict:
        """What a provider of its own sends to start the turn's thread."""
        provider = codex.CodexProvider()
        server = CountingAppServer()
        provider._server_for = AsyncMock(return_value=server)
        await collect(provider.run_turn(dataclasses.replace(turn, machine=FakeMachine())))
        return server.sent('thread/start')[0]

    async def test_a_recorded_ultra_turn_runs_on_a_persistent_thread_with_workers_on(self):
        messages = recorded('codex-p.jsonl')
        process = ReplayProcess(messages)
        turn = ultra_turn(ReplayMachine(process))
        events = await self.run_turn(turn)

        [thread_start] = process.sent('thread/start')
        # Codex fails every spawn from an ephemeral thread.
        self.assertIs(thread_start['ephemeral'], False)
        self.assertEqual(thread_start['sandbox'], 'read-only')
        self.assertEqual(
            thread_start['config'],
            {
                'features.multi_agent': True,
                'agents.default_subagent_model': SOL,
                'agents.default_subagent_reasoning_effort': 'low',
                # The Read access keys stay.
                'mcp_servers': {},
                'project_doc_max_bytes': 0,
                'skills.include_instructions': False,
            },
        )
        # The orchestrator note, which the server put ahead of the chat's instructions.
        self.assertEqual(thread_start['developerInstructions'], turn.conversation.system)
        self.assertTrue(thread_start['developerInstructions'].startswith(ultra.ORCHESTRATOR_INTRO))
        self.assertTrue(thread_start['developerInstructions'].endswith(FOLDER_INSTRUCTIONS))
        [turn_start] = process.sent('turn/start')
        self.assertEqual(turn_start['input'], [text_input(PROMPT), text_input(ultra.ULTRA_TURN_NOTE)])
        self.assertEqual(turn_start['effort'], 'low')

        # The reply is the orchestrator's own; its workers' messages stay on their threads.
        text = reply(events)
        self.assertTrue(text.startswith("I\u2019ll start two sub-agents in parallel"))
        self.assertTrue(text.endswith(final_answer(messages, orchestrator_thread(messages))))
        worker_threads = recorded_workers(messages).values()
        self.assertEqual(len(worker_threads), 2)
        for worker_thread in worker_threads:
            self.assertNotIn(final_answer(messages, worker_thread), text)
        self.assertEqual(failures(events), [])

    async def test_an_ultra_turn_on_an_older_codex_runs_without_ultra(self):
        # The server judged Ultra by a model list from another computer, whose Codex is newer.
        messages = with_user_agent(recorded('codex-p.jsonl'), OLDER_USER_AGENT)
        process = ReplayProcess(messages)
        events = await self.run_turn(ultra_turn(ReplayMachine(process)))

        reason = codex.ULTRA_TOO_OLD_REASON.format(computer=ReplayMachine.name)
        self.assertEqual(events[0], StatusUpdate(ultra.OFF_STATUS.format(reason=reason), keep=True))
        # Its thread is an ordinary one, with workers off, and nothing asks for workers.
        [thread_start] = process.sent('thread/start')
        self.assertIs(thread_start['ephemeral'], True)
        self.assertIs(thread_start['config']['features.multi_agent'], False)
        self.assertNotIn('agents.default_subagent_model', thread_start['config'])
        self.assertEqual(thread_start['developerInstructions'], FOLDER_INSTRUCTIONS)
        [turn_start] = process.sent('turn/start')
        self.assertEqual(turn_start['input'], [text_input(PROMPT)])
        # Only the orchestrator's thread is read, and kept for turns without Ultra.
        self.assertFalse(any(isinstance(event, WorkerUpdate) for event in events))
        self.assertEqual(self.provider._servers[ReplayMachine.id]._worker_routes, {})
        [live] = self.provider._live_threads.values()
        self.assertIsNone(live.ultra)
        self.assertEqual(failures(events), [])

    async def test_workers_are_on_at_every_access_level_with_that_levels_keys(self):
        options = UltraOptions(worker_model=SOL, worker_label='GPT-5.6-Sol')
        worker_keys = {'features.multi_agent': True, 'agents.default_subagent_model': SOL}
        for access in ('chat', 'read', 'full'):
            with self.subTest(access=access):
                turn = ultra_turn(access=access, options=options, effort=None)
                params = await self.thread_start(turn)
                self.assertIs(params['ephemeral'], False)
                config = params['config']
                self.assertEqual({key: config[key] for key in worker_keys}, worker_keys)
                # A whole features table would turn back on what the app-server disabled at start.
                self.assertNotIn('features', config)
                # The orchestrator note reaches the thread through the turn's instructions.
                instructions = turn.conversation.system
                if access == 'chat':
                    instructions = f'{CHAT_INSTRUCTIONS}\n\n{instructions}'
                self.assertEqual(params['developerInstructions'], instructions)
                if access == 'full':
                    self.assertEqual(config, worker_keys)
                else:
                    self.assertEqual(config['mcp_servers'], {'node_repl': {'enabled': False}})
                    self.assertEqual(config['project_doc_max_bytes'], 0)
                if access == 'chat':
                    self.assertIs(config['features.shell_tool'], False)
                    self.assertIs(config['features.unified_exec'], False)

    async def test_threads_without_ultra_stay_ephemeral_with_workers_off(self):
        for access in ('chat', 'read', 'full'):
            with self.subTest(access=access):
                params = await self.thread_start(plain_turn(access=access))
                self.assertIs(params['ephemeral'], True)
                self.assertIs(params['config']['features.multi_agent'], False)
                self.assertNotIn('agents.default_subagent_model', params['config'])
                self.assertNotIn('agents.default_subagent_reasoning_effort', params['config'])

    async def test_the_workers_level_is_sent_only_when_their_model_offers_it(self):
        def worker_keys(params):
            return {key: value for key, value in params['config'].items() if key.startswith('agents.')}

        # The recording's Sol, like the chat's model, offers the levels from 'low' up to 'ultra'.
        sol_minimal = UltraOptions(worker_model=SOL, worker_label='GPT-5.6-Sol', worker_effort='minimal')
        params = await self.thread_start(ultra_turn(options=sol_minimal))
        self.assertEqual(worker_keys(params), {'agents.default_subagent_model': SOL})

        chat_model_ultra = UltraOptions(worker_effort='ultra')
        params = await self.thread_start(ultra_turn(options=chat_model_ultra))
        self.assertEqual(worker_keys(params), {'agents.default_subagent_reasoning_effort': 'ultra'})

        params = await self.thread_start(ultra_turn(options=SAME_AS_CHAT))
        self.assertEqual(worker_keys(params), {})

    async def test_a_level_for_the_workers_alone_is_checked_against_the_model_list(self):
        server = CountingAppServer()
        self.use_server(server)
        # No level for the chat itself, so only the workers' level needs the model list.
        await self.run_turn(ultra_turn(FakeMachine(), effort=None))
        self.assertEqual(len(server.sent('model/list')), 1)
        config = server.sent('thread/start')[0]['config']
        self.assertEqual(config['agents.default_subagent_reasoning_effort'], 'low')
        self.assertNotIn('effort', server.sent('turn/start')[0])

    def test_the_ultra_note_follows_the_persons_message_and_images(self):
        turn = ultra_turn()
        prompt = ChatTurn('user', PROMPT, [PNG_DATA_URL])
        turn = dataclasses.replace(turn, conversation=dataclasses.replace(turn.conversation, prompt=prompt))
        self.assertEqual(
            codex.turn_input(turn),
            [text_input(PROMPT), {'type': 'image', 'url': PNG_DATA_URL}, text_input(ultra.ULTRA_TURN_NOTE)],
        )
        self.assertEqual(codex.turn_input(plain_turn()), [text_input(PROMPT)])
        # Titles and follow-ups never start workers, so they get no note either.
        task = dataclasses.replace(plain_turn(), is_task=True, ultra_options=SOL_LOW)
        self.assertEqual(codex.turn_input(task), [text_input(PROMPT)])


class UltraThreadReuseTests(unittest.IsolatedAsyncioTestCase):
    """An Ultra thread runs only Ultra turns, and other threads only other turns."""

    def setUp(self):
        self.provider = codex.CodexProvider()
        self.server = CountingAppServer()
        self.provider._servers[FakeMachine.id] = self.server
        self.provider._server_for = AsyncMock(return_value=self.server)
        self.machine = FakeMachine()

    async def run_turn(self, turn) -> list:
        return await collect(self.provider.run_turn(turn))

    async def test_turning_ultra_on_or_off_starts_a_fresh_thread(self):
        first = await self.run_turn(ultra_turn(self.machine))
        after_ultra = [{'role': 'user', 'content': PROMPT}, {'role': 'assistant', 'content': reply(first)}]

        # The next message with Ultra still on continues the Ultra thread.
        second = await self.run_turn(ultra_turn(self.machine, earlier=after_ultra, prompt='And 18 times 23?'))
        self.assertEqual(len(self.server.sent('thread/start')), 1)
        self.assertEqual(self.server.sent('turn/start')[1]['threadId'], 'thread-1')

        # With Ultra off, the next message starts a thread without workers.
        after_second = [
            *after_ultra,
            {'role': 'user', 'content': 'And 18 times 23?'},
            {'role': 'assistant', 'content': reply(second)},
        ]
        third = await self.run_turn(plain_turn(self.machine, earlier=after_second, prompt='Thanks!'))
        plain_start = self.server.sent('thread/start')[1]
        self.assertIs(plain_start['ephemeral'], True)
        self.assertIs(plain_start['config']['features.multi_agent'], False)
        self.assertEqual(self.server.sent('turn/start')[2]['threadId'], 'thread-2')
        self.assertEqual(self.server.sent('turn/start')[2]['input'], [text_input('Thanks!')])
        # It starts from the chat's text instead.
        [injected] = self.server.sent('thread/inject_items')
        self.assertEqual(injected['threadId'], 'thread-2')
        self.assertEqual(len(injected['items']), len(after_second))

        # And turned on again, it starts another Ultra thread rather than continue either one.
        after_third = [
            *after_second,
            {'role': 'user', 'content': 'Thanks!'},
            {'role': 'assistant', 'content': reply(third)},
        ]
        await self.run_turn(ultra_turn(self.machine, earlier=after_third, prompt='One more?'))
        self.assertEqual(len(self.server.sent('thread/start')), 3)
        self.assertIs(self.server.sent('thread/start')[2]['ephemeral'], False)
        self.assertEqual(self.server.sent('turn/start')[3]['threadId'], 'thread-3')
        # Only the Ultra turns followed their threads' workers.
        self.assertEqual(self.server.followed, ['thread-1', 'thread-1', 'thread-3'])

    async def test_a_kept_thread_runs_only_turns_with_the_ultra_choice_it_started_with(self):
        def kept(thread_id, options):
            return codex.LiveThread(thread_id, FakeMachine.id, 1, 'read', PROJECT, None, ultra=options)

        # Even under the same key, neither kind of thread runs the other kind's turns.
        self.provider._keep_live_thread('chat', kept('ultra-thread', SOL_LOW))
        self.assertIsNone(self.provider._take_live_thread('chat', plain_turn(), 1))
        self.provider._keep_live_thread('chat', kept('plain-thread', None))
        self.assertIsNone(self.provider._take_live_thread('chat', ultra_turn(), 1))
        # The workers' model and level were set when the thread started.
        self.provider._keep_live_thread('chat', kept('sol-thread', SOL_LOW))
        self.assertIsNone(self.provider._take_live_thread('chat', ultra_turn(options=SAME_AS_CHAT), 1))
        # Let the release tasks send their requests.
        await asyncio.sleep(0)
        released = [params['threadId'] for params in self.server.sent('thread/unsubscribe')]
        self.assertEqual(released, ['ultra-thread', 'plain-thread', 'sol-thread'])

        same_choice = kept('matching', SOL_LOW)
        self.provider._keep_live_thread('chat', same_choice)
        self.assertIs(self.provider._take_live_thread('chat', ultra_turn(), 1), same_choice)


class UltraWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Workers listed from the recorded turns' own threads, and how long the turns are followed."""

    def setUp(self):
        self.provider = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(self.provider.stop)

    async def replay(self, messages: list[dict], options=SAME_AS_CHAT) -> list:
        """Run an Ultra turn against a recorded stream and return its events.

        The chat has no thinking level of its own, so only a level chosen for
        the workers loads the model list, which only codex-p.jsonl recorded.
        """
        process = ReplayProcess(messages)
        turn = ultra_turn(ReplayMachine(process), options=options, effort=None)
        return await collect(self.provider.run_turn(turn))

    async def test_the_recorded_workers_are_listed_from_start_to_finish(self):
        messages = recorded('codex-p.jsonl')
        events = await self.replay(messages, options=SOL_LOW)
        workers = recorded_workers(messages)
        multiply = workers['/root/multiply']
        primes = workers['/root/primes']

        # Each worker is its own thread, on the person's choices, under the orchestrator.
        self.assertEqual(list(latest_workers(events)), [multiply, primes])
        for label, worker_id in (('Multiply', multiply), ('Primes', primes)):
            started = WorkerUpdate(
                worker_id=worker_id,
                parent_id=None,
                depth=1,
                label=label,
                state=WORKER_RUNNING,
                model='GPT-5.6-Sol',
                effort='low',
            )
            self.assertEqual(worker_updates(events, worker_id)[0], started)
        # Both ran at once: the second started before the first ended.
        self.assertLess(
            first_update_index(events, primes, WORKER_RUNNING), first_update_index(events, multiply, WORKER_DONE)
        )
        # Each one ends with its thread's last message as its report, and the tokens its thread used.
        latest = latest_workers(events)
        self.assertEqual(
            (latest[multiply].state, latest[multiply].summary, latest[multiply].tokens, latest[multiply].activity),
            (WORKER_DONE, '17 \u00d7 23 = 391.', 13991, None),
        )
        self.assertEqual(
            (latest[primes].state, latest[primes].summary, latest[primes].tokens, latest[primes].activity),
            (WORKER_DONE, '101, 103, and 107.', 13992, None),
        )
        # What each one did, from its own thread.
        self.assertEqual(activities(events, multiply), [None, 'Working', 'Writing', None])
        self.assertEqual(failures(events), [])
        # Once the turn is over, no worker's thread reaches a queue.
        server = self.provider._servers[ReplayMachine.id]
        self.assertEqual(server._thread_queues, {})
        self.assertEqual(server._worker_routes, {})

    async def test_the_reply_counts_its_workers_tokens(self):
        events = await self.replay(recorded('codex-p.jsonl'), options=SOL_LOW)
        # The orchestrator's thread used 71584 tokens in, 56320 of them cached,
        # and 180 out. Each worker's thread used 13979 in, 11776 of the second's
        # cached, and 12 and 13 out.
        self.assertEqual(usages(events), [TokenUsage(input_tokens=99542, output_tokens=205, cached_input_tokens=68096)])
        # The kept thread remembers only the orchestrator's own total, which the
        # next turn's usage starts from.
        [live] = self.provider._live_threads.values()
        self.assertEqual(live.usage_total['inputTokens'], 71584)

    async def test_workers_that_outlive_the_orchestrators_turn_keep_the_turn_open(self):
        messages = recorded('codex-stop-children.jsonl')
        orchestrator = orchestrator_thread(messages)
        workers = recorded_workers(messages)
        rivers = workers['/root/rivers_essay']
        mountains = workers['/root/mountains_essay']
        # The orchestrator's turn was interrupted while both workers wrote, and Codex kept them running.
        completed = [thread_of(message) for message in messages if message.get('method') == 'turn/completed']
        self.assertEqual(completed, [orchestrator, rivers, mountains])

        events = await self.replay(messages)

        # Buddy followed the turn until each worker ended, as stopped.
        latest = latest_workers(events)
        self.assertEqual(list(latest), [rivers, mountains])
        self.assertEqual([worker.label for worker in latest.values()], ['Rivers essay', 'Mountains essay'])
        for worker in latest.values():
            self.assertEqual((worker.state, worker.activity), (WORKER_STOPPED, None))
        self.assertEqual(activities(events, rivers), [None, 'Working', 'Thinking', 'Writing', None])
        # Only then did the reply end, failed as the orchestrator's interrupted turn did.
        self.assertEqual(failures(events), ['The reply was stopped.'])
        self.assertIsInstance(events[-1], TurnFailed)
        # The essays stay out of the reply, which is the orchestrator's one message.
        self.assertEqual(reply(events), agent_messages(messages, orchestrator)[0])

    async def test_a_worker_still_running_when_the_orchestrator_replies_keeps_the_turn_open(self):
        messages = recorded('codex-p.jsonl')
        primes = recorded_workers(messages)['/root/primes']
        primes_end = turn_completion(messages, primes)
        # Here the primes worker's turn ends after the orchestrator's.
        reordered = [message for message in messages if message is not primes_end] + [primes_end]
        events = await self.replay(reordered)

        self.assertEqual(latest_workers(events)[primes].state, WORKER_DONE)
        self.assertTrue(reply(events).endswith(final_answer(messages, orchestrator_thread(messages))))
        self.assertEqual(failures(events), [])
        self.assertEqual(len(self.provider._live_threads), 1)

    async def test_sub_workers_are_listed_under_the_worker_that_started_them(self):
        messages = recorded('codex-depth.jsonl')
        events = await self.replay(messages)
        workers = recorded_workers(messages)
        level1 = workers['/root/level1']
        level2 = workers['/root/level1/level2']
        level3 = workers['/root/level1/level2/level3']

        # Each worker's own thread reports the sub-worker it starts. Codex
        # doesn't limit how deep they go, so a third level started too.
        placed = {level1: (None, 1, 'Level1'), level2: (level1, 2, 'Level2'), level3: (level2, 3, 'Level3')}
        self.assertEqual(list(latest_workers(events)), list(placed))
        for worker_id, (parent_id, depth, label) in placed.items():
            first = worker_updates(events, worker_id)[0]
            self.assertEqual((first.parent_id, first.depth, first.label), (parent_id, depth, label))
        # The first two levels end with their last message as their report.
        # Buddy stops the third, as UltraDepthTests shows.
        for worker_id in (level1, level2):
            latest = latest_workers(events)[worker_id]
            self.assertEqual((latest.state, latest.summary), (WORKER_DONE, agent_messages(messages, worker_id)[-1]))
        # A worker that waits for its sub-worker says so.
        self.assertIn('Waiting for its workers', activities(events, level1))
        orchestrator = orchestrator_thread(messages)
        self.assertTrue(reply(events).startswith(agent_messages(messages, orchestrator)[0].strip()))
        self.assertTrue(reply(events).endswith(final_answer(messages, orchestrator)))
        self.assertEqual(failures(events), [])

    async def test_workers_whose_own_threads_report_nothing_end_when_their_parent_says_so(self):
        messages = recorded('codex-p.jsonl')
        workers = recorded_workers(messages)
        orchestrator_only = [message for message in messages if thread_of(message) not in workers.values()]
        events = await self.replay(orchestrator_only)

        for worker_id in workers.values():
            self.assertEqual(states(events, worker_id), [WORKER_RUNNING, WORKER_DONE])
        self.assertEqual(failures(events), [])
        self.assertEqual(len(self.provider._live_threads), 1)

    async def test_a_worker_ends_as_its_own_turn_did_when_its_parent_reports_it_first(self):
        messages = recorded('codex-p.jsonl')
        primes = recorded_workers(messages)['/root/primes']
        primes_end = turn_completion(messages, primes)
        # The orchestrator's thread reports the primes worker's turn over just
        # before that turn's own turn/completed, which here says it was interrupted.
        first_report = completion_reports(messages, primes)[0]
        self.assertLess(messages.index(first_report), messages.index(primes_end))
        interrupted = copy.deepcopy(primes_end)
        interrupted['params']['turn']['status'] = 'interrupted'
        events = await self.replay([interrupted if message is primes_end else message for message in messages])

        # It was never shown done.
        self.assertEqual(states(events, primes), [WORKER_RUNNING, WORKER_STOPPED])

    async def test_workers_still_running_when_codex_exits_are_listed_as_stopped(self):
        messages = recorded('codex-stop-children.jsonl')
        orchestrator_end = turn_completion(messages, orchestrator_thread(messages))
        # The app-server exits after the orchestrator's turn, while both workers write.
        until_orchestrator_ends = messages[: messages.index(orchestrator_end) + 1]
        process = ReplayProcess(until_orchestrator_ends, exits=True)
        turn = ultra_turn(ReplayMachine(process), effort=None, options=SAME_AS_CHAT)
        events = []
        with self.assertRaises(SubscriptionError):
            async for event in self.provider.run_turn(turn):
                events.append(event)

        latest = latest_workers(events)
        self.assertEqual(list(latest), list(recorded_workers(messages).values()))
        for worker in latest.values():
            self.assertEqual((worker.state, worker.summary), (WORKER_STOPPED, codex.WORKER_ENDED_WITH_CODEX))

    async def test_an_ultra_turn_is_followed_only_up_to_its_time_limit(self):
        messages = recorded('codex-p.jsonl')
        primes = recorded_workers(messages)['/root/primes']
        primes_end = turn_completion(messages, primes)
        # The primes worker's turn never ends here, unless Buddy interrupts it.
        never_ends = [message for message in messages if message is not primes_end]
        process = InterruptibleProcess(never_ends)
        turn = ultra_turn(ReplayMachine(process), options=SAME_AS_CHAT, effort=None)
        with patch.object(codex, 'ULTRA_TIME_LIMIT', 0.2):
            with self.assertLogs(codex.log, level='WARNING'):
                events = await collect(self.provider.run_turn(turn))

        self.assertEqual(failures(events), [codex.ULTRA_TIME_LIMIT_MESSAGE])
        # Codex doesn't stop workers with the reply, so Buddy interrupted the
        # primes worker's turn, and the list shows it stopped before the reply ends.
        primes_turn = turn_start(messages, primes)['params']['turn']['id']
        self.assertEqual(process.sent('turn/interrupt'), [{'threadId': primes, 'turnId': primes_turn}])
        self.assertEqual(states(events, primes), [WORKER_RUNNING, WORKER_STOPPED])
        self.assertLess(first_update_index(events, primes, WORKER_STOPPED), events.index(usages(events)[0]))
        self.assertIsInstance(events[-1], TurnFailed)
        # The orchestrator's reply is all there, with its usage, but its thread isn't kept.
        self.assertTrue(reply(events).endswith(final_answer(messages, orchestrator_thread(messages))))
        self.assertEqual(len(usages(events)), 1)
        self.assertEqual(len(self.provider._live_threads), 0)

    async def test_a_turn_without_ultra_reads_only_its_own_thread(self):
        messages = recorded('codex-p.jsonl')
        process = ReplayProcess(messages)
        events = await collect(self.provider.run_turn(plain_turn(ReplayMachine(process), effort=None)))

        self.assertFalse(any(isinstance(event, WorkerUpdate) for event in events))
        # The reply and its usage are the orchestrator's thread's alone.
        self.assertTrue(reply(events).endswith(final_answer(messages, orchestrator_thread(messages))))
        self.assertEqual(usages(events), [TokenUsage(input_tokens=71584, output_tokens=180, cached_input_tokens=56320)])
        self.assertEqual(failures(events), [])
        self.assertEqual(self.provider._servers[ReplayMachine.id]._worker_routes, {})


class UltraDepthTests(unittest.IsolatedAsyncioTestCase):
    """Codex ignores agents.max_depth, so Buddy stops the workers that sub-workers start.

    In codex-depth.jsonl nothing interrupted the third level, so here its
    recorded turn runs on after Buddy's interrupt, which gets an empty answer.
    """

    def setUp(self):
        self.provider = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(self.provider.stop)
        # A turn held open by mistake then fails within seconds instead of hours.
        time_limit = patch.object(codex, 'ULTRA_TIME_LIMIT', 5)
        time_limit.start()
        self.addCleanup(time_limit.stop)
        self.messages = recorded('codex-depth.jsonl')
        workers = recorded_workers(self.messages)
        self.level1 = workers['/root/level1']
        self.level2 = workers['/root/level1/level2']
        self.level3 = workers['/root/level1/level2/level3']
        self.level3_turn = turn_start(self.messages, self.level3)['params']['turn']['id']

    async def replay(self, messages: list[dict]) -> tuple[list, ReplayProcess]:
        """Run an Ultra turn against a recorded stream; return its events and the replayed app-server."""
        process = ReplayProcess(messages)
        turn = ultra_turn(ReplayMachine(process), options=SAME_AS_CHAT, effort=None)
        events = await collect(self.provider.run_turn(turn))
        # Let the interrupts' tasks send their requests.
        await asyncio.sleep(0)
        return events, process

    def about_level3(self, message: dict) -> bool:
        """Whether a message is the third level's own, or its parent's report that its turn is over."""
        if thread_of(message) == self.level3:
            return True
        item = (message.get('params') or {}).get('item') or {}
        if item.get('type') != 'subAgentActivity' or item.get('kind') != 'completed':
            return False
        return item.get('agentThreadId') == self.level3

    async def test_a_worker_that_a_sub_worker_starts_is_interrupted(self):
        events, process = await self.replay(self.messages)

        # Only the third level's turn was interrupted, on its own thread.
        self.assertEqual(process.sent('turn/interrupt'), [{'threadId': self.level3, 'turnId': self.level3_turn}])
        # It is listed as stopped from the moment its parent reports it.
        stopped = WorkerUpdate(
            worker_id=self.level3,
            parent_id=self.level2,
            depth=3,
            label='Level3',
            state=WORKER_STOPPED,
            summary=codex.WORKER_TOO_DEEP,
        )
        self.assertEqual(worker_updates(events, self.level3)[0], stopped)
        # What it did and wrote before the interrupt landed doesn't change that, but its tokens count.
        self.assertEqual(states(events, self.level3), [WORKER_STOPPED])
        latest = latest_workers(events)[self.level3]
        self.assertEqual((latest.summary, latest.activity, latest.tokens), (codex.WORKER_TOO_DEEP, None, 13995))
        # The orchestrator's worker and its sub-worker ran to the end.
        for worker_id in (self.level1, self.level2):
            self.assertEqual(states(events, worker_id), [WORKER_RUNNING, WORKER_DONE])
        self.assertEqual(failures(events), [])

    def test_the_interrupt_is_handed_over_as_soon_as_the_workers_turn_starts(self):
        turn_id = recorded_answer(self.messages, 'turn/start')['result']['turn']['id']
        parser = codex.CodexTurnParser(turn_id, orchestrator_thread(self.messages))
        parser.track_workers(SAME_AS_CHAT)
        handed_over = []
        for index, message in enumerate(self.messages):
            if 'method' not in message:
                continue
            parser.handle(message)
            for interrupt in parser.take_interrupts():
                handed_over.append((index, interrupt))

        # Not when its parent reports it, since Codex hasn't named its turn yet, and only once.
        level3_start = self.messages.index(turn_start(self.messages, self.level3))
        self.assertEqual(handed_over, [(level3_start, (self.level3, self.level3_turn))])

    async def test_the_turn_stays_open_until_the_worker_is_stopped(self):
        # Here the third level's turn starts only after the orchestrator's has completed.
        late = [message for message in self.messages if self.about_level3(message)]
        reordered = [message for message in self.messages if not self.about_level3(message)] + late
        events, process = await self.replay(reordered)

        self.assertEqual(process.sent('turn/interrupt'), [{'threadId': self.level3, 'turnId': self.level3_turn}])
        # Its tokens, reported after the orchestrator's turn completed, reached the list.
        self.assertEqual(latest_workers(events)[self.level3].tokens, 13995)
        self.assertEqual(failures(events), [])
        self.assertEqual(len(self.provider._live_threads), 1)

    async def test_a_worker_whose_own_thread_reports_nothing_ends_when_its_parent_says_so(self):
        silent = [message for message in self.messages if thread_of(message) != self.level3]
        events, process = await self.replay(silent)

        # Codex never named its turn, so there was none to interrupt, and the turn didn't wait for one.
        self.assertEqual(process.sent('turn/interrupt'), [])
        self.assertEqual(states(events, self.level3), [WORKER_STOPPED])
        self.assertEqual(failures(events), [])


class UltraStopTests(unittest.IsolatedAsyncioTestCase):
    """Stopping one worker of a live Ultra turn, and stopping the whole turn.

    Codex doesn't stop workers with their orchestrator: in
    codex-stop-children.jsonl the workers wrote on after the orchestrator's
    turn was interrupted, until each one was interrupted on its own thread.
    """

    def setUp(self):
        self.provider = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(self.provider.stop)
        # A turn held open by mistake then fails within seconds instead of hours.
        time_limit = patch.object(codex, 'ULTRA_TIME_LIMIT', 5)
        time_limit.start()
        self.addCleanup(time_limit.stop)
        self.messages = recorded('codex-stop-children.jsonl')
        self.orchestrator = orchestrator_thread(self.messages)
        workers = recorded_workers(self.messages)
        self.rivers = workers['/root/rivers_essay']
        self.mountains = workers['/root/mountains_essay']

    def interrupt_of(self, thread_id: str) -> dict:
        """The turn/interrupt that stops a thread's recorded turn."""
        turn_id = turn_start(self.messages, thread_id)['params']['turn']['id']
        return {'threadId': thread_id, 'turnId': turn_id}

    def without_worker_ends(self) -> list[dict]:
        """The recording without the workers' turn/completed, so that they write on."""
        workers = (self.rivers, self.mountains)
        return [
            message
            for message in self.messages
            if message.get('method') != 'turn/completed' or thread_of(message) not in workers
        ]

    async def start(self, process: ReplayProcess):
        """Run an Ultra turn in a task, until Buddy has handled all that the replayed app-server wrote.

        Returns the turn, the task, and the list that the turn's events are added to as they come.
        """
        turn = ultra_turn(ReplayMachine(process), options=SAME_AS_CHAT, effort=None)
        events = []

        async def run():
            async for event in self.provider.run_turn(turn):
                events.append(event)

        task = asyncio.create_task(run())
        self.addAsyncCleanup(self.finish, task)
        await self.eventually(lambda: self.handled(process))
        return turn, task, events

    def handled(self, process: ReplayProcess) -> bool:
        """Whether Buddy has read and handled all that the replayed app-server wrote once the turn started."""
        server = self.provider._servers.get(ReplayMachine.id)
        if server is None or not process.sent('turn/start'):
            return False
        if not process.drained():
            return False
        return all(queue.empty() for queue in server._thread_queues.values())

    async def eventually(self, condition) -> None:
        await asyncio.wait_for(until(condition), 5)

    async def finish(self, task: asyncio.Task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def stop_reply(self, task: asyncio.Task):
        """Stop the reply as Stop does: cancel it and wait until it has ended."""
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)

    def unloaded_threads(self, process: ReplayProcess) -> list[str]:
        return [params['threadId'] for params in process.sent('thread/unsubscribe')]

    def test_a_turn_that_buddy_interrupts_ends_as_the_recorded_ones_did(self):
        recorded_end = turn_completion(self.messages, self.rivers)
        turn = dict(recorded_end['params']['turn'])
        for timing in ('startedAt', 'completedAt', 'durationMs'):
            del turn[timing]
        self.assertEqual(
            {'method': recorded_end['method'], 'params': {**recorded_end['params'], 'turn': turn}},
            interrupted_completion(self.rivers, turn['id']),
        )

    async def test_stopping_one_worker_interrupts_its_turn_on_its_own_thread(self):
        # The orchestrator's turn has ended, and here each worker writes on until Buddy interrupts it.
        process = InterruptibleProcess(self.messages, waiting=[self.rivers, self.mountains])
        turn, task, events = await self.start(process)
        latest = latest_workers(events)
        self.assertEqual([latest[self.rivers].state, latest[self.mountains].state], [WORKER_RUNNING, WORKER_RUNNING])

        self.assertTrue(await self.provider.stop_worker(turn, self.rivers))
        await self.eventually(lambda: latest_workers(events)[self.rivers].state == WORKER_STOPPED)

        self.assertEqual(process.sent('turn/interrupt'), [self.interrupt_of(self.rivers)])
        self.assertEqual(latest_workers(events)[self.mountains].state, WORKER_RUNNING)
        self.assertFalse(task.done())
        # Once Codex has reported it stopped, it can't be stopped again.
        self.assertFalse(await self.provider.stop_worker(turn, self.rivers))

        self.assertTrue(await self.provider.stop_worker(turn, self.mountains))
        # Then no worker is left, so the turn is over.
        await asyncio.wait_for(task, 5)
        interrupts = [self.interrupt_of(self.rivers), self.interrupt_of(self.mountains)]
        self.assertEqual(process.sent('turn/interrupt'), interrupts)
        self.assertEqual(states(events, self.mountains), [WORKER_RUNNING, WORKER_STOPPED])
        # The reply failed as the orchestrator's interrupted turn did.
        self.assertEqual(failures(events), ['The reply was stopped.'])
        self.assertFalse(await self.provider.stop_worker(turn, self.mountains))
        # A worker's interrupt leaves its thread loaded; only the orchestrator's thread is unloaded.
        await asyncio.sleep(0)
        self.assertEqual(self.unloaded_threads(process), [self.orchestrator])

    async def test_only_a_running_worker_of_a_live_turn_can_be_stopped(self):
        messages = recorded('codex-p.jsonl')
        workers = recorded_workers(messages)
        multiply = workers['/root/multiply']
        primes = workers['/root/primes']
        # Here the recording stops after the multiply worker has finished, while the primes worker runs.
        primes_reported_done = completion_reports(messages, primes)[0]
        process = InterruptibleProcess(messages[: messages.index(primes_reported_done)])
        turn, _, events = await self.start(process)
        latest = latest_workers(events)
        self.assertEqual([latest[multiply].state, latest[primes].state], [WORKER_DONE, WORKER_RUNNING])

        self.assertFalse(await self.provider.stop_worker(turn, multiply))
        self.assertFalse(await self.provider.stop_worker(turn, 'no-such-worker'))
        # The orchestrator is not one of its own workers.
        self.assertFalse(await self.provider.stop_worker(turn, orchestrator_thread(messages)))
        # The turn is found by the very request that was passed to run_turn, not an equal copy.
        self.assertFalse(await self.provider.stop_worker(dataclasses.replace(turn), primes))
        # Nor once its app-server has exited, which ends its workers with it.
        process.returncode = 0
        self.assertFalse(await self.provider.stop_worker(turn, primes))
        await asyncio.sleep(0)
        self.assertEqual(process.sent('turn/interrupt'), [])

    async def test_stopping_the_reply_interrupts_the_orchestrator_and_then_every_running_worker(self):
        # Here the orchestrator still waits for both workers when the person stops the reply.
        waiting = [self.orchestrator, self.rivers, self.mountains]
        process = InterruptibleProcess(self.messages, waiting=waiting)
        turn, task, events = await self.start(process)
        self.assertEqual(len(latest_workers(events)), 2)

        await self.stop_reply(task)

        # The orchestrator's first, so that it starts no more workers, then each worker's on its own thread.
        self.assertEqual(process.sent('turn/interrupt'), [self.interrupt_of(thread) for thread in waiting])
        # Buddy read until Codex reported all three turns ended, so it interrupted none of them twice.
        self.assertTrue(process.drained())
        await asyncio.sleep(0)
        self.assertEqual(self.unloaded_threads(process), [self.orchestrator])
        self.assertFalse(await self.provider.stop_worker(turn, self.rivers))
        server = self.provider._servers[ReplayMachine.id]
        self.assertEqual(server._thread_queues, {})
        self.assertEqual(server._worker_routes, {})

    async def test_the_workers_are_interrupted_without_waiting_for_codex_to_answer_the_orchestrators_interrupt(self):
        # Here Codex answers the orchestrator's interrupt only after Buddy has stopped waiting.
        waiting = [self.orchestrator, self.rivers, self.mountains]
        process = SlowInterruptProcess(self.messages, waiting=waiting, slow_thread=self.orchestrator)
        _, task, _ = await self.start(process)
        [run] = self.provider._ultra_runs

        with patch.object(codex, 'STOP_WORKERS_GRACE', 0.2):
            with self.assertLogs(codex.log, level='WARNING'):
                await self.stop_reply(task)

        # Each worker's interrupt went out right after the orchestrator's, before Codex answered it.
        self.assertEqual(process.sent('turn/interrupt')[:3], [self.interrupt_of(thread) for thread in waiting])
        # So both workers' turns ended within the grace, and only the orchestrator's didn't.
        self.assertFalse(run.parser.workers.running())
        self.assertFalse(run.parser.finished)
        # As Buddy let the orchestrator's thread go, it interrupted its turn once more.
        await asyncio.sleep(0)
        self.assertEqual(process.sent('turn/interrupt')[3:], [self.interrupt_of(self.orchestrator)])
        process.release()

    async def test_a_worker_whose_turn_starts_while_the_reply_stops_is_interrupted_too(self):
        waiting = [self.orchestrator, self.rivers, self.mountains]
        # The orchestrator has reported the mountains worker, but that worker's thread hasn't named its turn yet.
        reported = start_report(self.messages, self.mountains)
        self.assertLess(self.messages.index(reported), self.messages.index(turn_start(self.messages, self.mountains)))
        process = PausedProcess(self.messages, waiting=waiting, pause_after=reported)
        _, task, _ = await self.start(process)
        [run] = self.provider._ultra_runs

        with patch.object(codex, 'STOP_WORKERS_GRACE', 1):
            task.cancel()
            # The orchestrator's turn and the rivers worker's are interrupted at once.
            await self.eventually(lambda: len(process.sent('turn/interrupt')) == 2)
            # Then the mountains worker's thread names its turn, while Buddy waits for the turns to end.
            process.resume()
            with self.assertNoLogs(codex.log, level='WARNING'):
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)

        # Its turn was interrupted as soon as it was named, so it ended within the grace, as stopped.
        self.assertEqual(process.sent('turn/interrupt'), [self.interrupt_of(thread) for thread in waiting])
        self.assertEqual(run.parser.workers._workers[self.mountains].state, WORKER_STOPPED)
        self.assertTrue(run.parser.turn_over())

    async def test_the_reply_ends_soon_when_codex_does_not_report_its_workers_stopped(self):
        # Here Codex only answers the interrupts.
        process = ReplayProcess(self.without_worker_ends())
        _, task, _ = await self.start(process)

        with patch.object(codex, 'STOP_WORKERS_GRACE', 0.05):
            with self.assertLogs(codex.log, level='WARNING'):
                await self.stop_reply(task)

        # The orchestrator's turn had ended, so only the workers' turns were interrupted.
        interrupts = [self.interrupt_of(self.rivers), self.interrupt_of(self.mountains)]
        self.assertEqual(process.sent('turn/interrupt'), interrupts)
        self.assertEqual(self.provider._servers[ReplayMachine.id]._thread_queues, {})

    async def test_stopping_again_while_workers_stop_ends_the_reply_at_once(self):
        process = ReplayProcess(self.without_worker_ends())
        _, task, _ = await self.start(process)

        with patch.object(codex, 'STOP_WORKERS_GRACE', 60):
            task.cancel()
            # Buddy waits for Codex to report the workers stopped, and the person presses Stop again.
            await self.eventually(lambda: len(process.sent('turn/interrupt')) == 2)
            await self.stop_reply(task)

        server = self.provider._servers[ReplayMachine.id]
        self.assertEqual(server._thread_queues, {})
        self.assertEqual(server._worker_routes, {})
        await asyncio.sleep(0)
        self.assertEqual(self.unloaded_threads(process), [self.orchestrator])


class UltraFollowUpTests(unittest.IsolatedAsyncioTestCase):
    """A kept Ultra thread's next turn that gives a worker from its first turn more to do."""

    def setUp(self):
        self.provider = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(self.provider.stop)
        # A turn held open by mistake then fails within seconds instead of hours.
        time_limit = patch.object(codex, 'ULTRA_TIME_LIMIT', 5)
        time_limit.start()
        self.addCleanup(time_limit.stop)
        self.messages = recorded('codex-p.jsonl')
        self.orchestrator = orchestrator_thread(self.messages)
        self.multiply = recorded_workers(self.messages)['/root/multiply']

    async def first_turn(self, machine) -> list:
        """Run the recorded turn, which the next one continues."""
        turn = ultra_turn(machine, options=SAME_AS_CHAT, effort=None)
        return await collect(self.provider.run_turn(turn))

    def next_turn(self, machine, first: list) -> TurnRequest:
        earlier = [{'role': 'user', 'content': PROMPT}, {'role': 'assistant', 'content': reply(first)}]
        return ultra_turn(machine, options=SAME_AS_CHAT, effort=None, earlier=earlier, prompt=FOLLOW_UP_PROMPT)

    async def test_a_worker_given_more_to_do_is_listed_again_and_counted(self):
        process = FollowUpProcess(self.messages, follow_up(self.messages, self.multiply))
        # One machine for both turns, so the provider keeps its app-server and the thread.
        machine = ReplayMachine(process)
        first = await self.first_turn(machine)
        events = await collect(self.provider.run_turn(self.next_turn(machine, first)))

        self.assertEqual([params['threadId'] for params in process.sent('turn/start')], [self.orchestrator] * 2)
        # Only the worker that works again is listed, where it was, with its new report.
        self.assertEqual(list(latest_workers(events)), [self.multiply])
        self.assertEqual(states(events, self.multiply), [WORKER_RUNNING, WORKER_DONE])
        latest = latest_workers(events)[self.multiply]
        self.assertEqual((latest.label, latest.depth, latest.parent_id), ('Multiply', 1, None))
        self.assertEqual(worker_updates(events, self.multiply)[0].summary, None)
        # Its tokens, and the reply's usage, are what each thread used in this turn.
        self.assertEqual((latest.summary, latest.tokens), (FOLLOW_UP_REPORT, 2010))
        self.assertEqual(usages(events), [TokenUsage(input_tokens=5000, output_tokens=30, cached_input_tokens=0)])
        self.assertEqual(reply(events), FOLLOW_UP_REPLY)
        self.assertEqual(failures(events), [])

    async def test_stopping_the_reply_stops_a_worker_given_more_to_do(self):
        process = FollowUpProcess(self.messages, follow_up(self.messages, self.multiply, ends=False))
        machine = ReplayMachine(process)
        first = await self.first_turn(machine)
        turn = self.next_turn(machine, first)
        events = []

        async def run():
            async for event in self.provider.run_turn(turn):
                events.append(event)

        task = asyncio.create_task(run())
        server = self.provider._servers[ReplayMachine.id]
        await asyncio.wait_for(until(lambda: len(process.sent('turn/start')) == 2 and process.drained()), 5)
        await asyncio.wait_for(until(lambda: all(queue.empty() for queue in server._thread_queues.values())), 5)
        self.assertEqual(latest_workers(events)[self.multiply].state, WORKER_RUNNING)

        with self.assertNoLogs(codex.log, level='WARNING'):
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)

        # Codex doesn't stop it with the orchestrator, so Buddy interrupted its turn on its own thread too.
        interrupts = [
            {'threadId': self.orchestrator, 'turnId': FOLLOW_UP_TURN},
            {'threadId': self.multiply, 'turnId': FOLLOW_UP_WORKER_TURN},
        ]
        self.assertEqual(process.sent('turn/interrupt'), interrupts)

    def test_earlier_workers_keep_their_place_and_the_depth_limit(self):
        messages = recorded('codex-depth.jsonl')
        workers = recorded_workers(messages)
        level1 = workers['/root/level1']
        level3 = workers['/root/level1/level2/level3']
        first = ultra_parser(messages)
        feed(first, messages)
        parser = codex.CodexTurnParser(FOLLOW_UP_TURN, orchestrator_thread(messages))
        parser.track_workers(SAME_AS_CHAT, first.workers.earlier_workers())

        # The third level starts another turn: it is listed again as stopped, and its turn is interrupted at once.
        turn = {'id': 'level3-again', 'status': 'inProgress'}
        [again] = parser.handle({'method': 'turn/started', 'params': {'threadId': level3, 'turn': turn}})
        self.assertEqual((again.state, again.summary, again.depth), (WORKER_STOPPED, codex.WORKER_TOO_DEEP, 3))
        self.assertEqual(parser.take_interrupts(), [(level3, 'level3-again')])
        # A sub-worker that the first level starts now is placed under it.
        item = {
            'type': 'subAgentActivity',
            'kind': 'started',
            'agentThreadId': 'review',
            'agentPath': '/root/level1/review',
        }
        [review] = parser.handle({'method': 'item/started', 'params': {'threadId': level1, 'item': item}})
        self.assertEqual((review.parent_id, review.depth, review.state), (level1, 2, WORKER_RUNNING))


class WorkerInterruptTests(unittest.TestCase):
    """Which workers' turns the parser hands over to interrupt when Buddy stops workers."""

    def test_a_worker_stopped_before_its_thread_names_its_turn_is_interrupted_once_it_does(self):
        messages = recorded('codex-stop-children.jsonl')
        mountains = recorded_workers(messages)['/root/mountains_essay']
        mountains_start = turn_start(messages, mountains)
        parser = ultra_parser(messages)
        # The orchestrator has reported the worker, but its own thread hasn't named its turn yet.
        reported = messages.index(start_report(messages, mountains)) + 1
        self.assertLess(reported, messages.index(mountains_start))
        self.assertEqual(feed(parser, messages[:reported]), [])

        self.assertTrue(parser.workers.interrupt(mountains))
        self.assertEqual(parser.take_interrupts(), [])

        # The turn is handed over the moment the worker's thread names it, and only once.
        mountains_turn = mountains_start['params']['turn']['id']
        self.assertEqual(feed(parser, messages[reported:]), [(mountains_start, (mountains, mountains_turn))])

    def test_stopping_the_whole_turn_interrupts_every_turn_that_starts_from_then_on(self):
        messages = recorded('codex-stop-children.jsonl')
        workers = recorded_workers(messages)
        rivers = workers['/root/rivers_essay']
        mountains = workers['/root/mountains_essay']
        rivers_start = turn_start(messages, rivers)
        mountains_start = turn_start(messages, mountains)
        parser = ultra_parser(messages)
        # The rivers worker's turn has started, and the mountains worker isn't reported yet.
        cut = messages.index(rivers_start) + 1
        self.assertLess(cut, messages.index(start_report(messages, mountains)))
        feed(parser, messages[:cut])

        parser.workers.interrupt_all()
        self.assertEqual(parser.take_interrupts(), [(rivers, rivers_start['params']['turn']['id'])])

        # The mountains worker starts after that, and its turn is handed over the moment its thread names it.
        mountains_turn = mountains_start['params']['turn']['id']
        self.assertEqual(feed(parser, messages[cut:]), [(mountains_start, (mountains, mountains_turn))])

    def test_stopping_a_worker_stops_the_sub_workers_it_runs(self):
        messages = recorded('codex-depth.jsonl')
        workers = recorded_workers(messages)
        level1 = workers['/root/level1']
        level2 = workers['/root/level1/level2']
        level1_turn = turn_start(messages, level1)['params']['turn']['id']
        level2_start = turn_start(messages, level2)
        parser = ultra_parser(messages)
        # Both have started their turns, and the sub-worker hasn't started a worker of its own yet.
        feed(parser, messages[: messages.index(level2_start) + 1])

        self.assertTrue(parser.workers.interrupt(level1))
        # Codex doesn't stop a sub-worker with the worker that started it.
        level2_turn = level2_start['params']['turn']['id']
        self.assertEqual(parser.take_interrupts(), [(level1, level1_turn), (level2, level2_turn)])
        # Each turn is handed over once, however often Buddy is asked.
        self.assertTrue(parser.workers.interrupt(level2))
        self.assertEqual(parser.take_interrupts(), [])


class UltraVersionTests(unittest.TestCase):
    def test_the_version_is_read_from_the_start_of_the_user_agent(self):
        user_agents = {
            recorded_answer(recorded('codex-p.jsonl'), 'initialize')['result']['userAgent']: CODEX_VERSION,
            'codex_vscode/0.160.0 (Mac OS 15.1.0; arm64) vscode/1.99.0': '0.160.0',
            'buddy/0.154.0-alpha.3 (Ubuntu 24.4.0; x86_64) xterm-256color (buddy; 1.0)': '0.154.0-alpha.3',
            # The operating system's version is not Codex's.
            'buddy (Windows 10.0.26200; x86_64)': '',
            'Windows 10.0.26200': '',
            'buddy/unknown (Windows 10.0.26200; x86_64)': '',
            '': '',
            None: '',
            42: '',
        }
        for user_agent, version in user_agents.items():
            with self.subTest(user_agent=user_agent):
                self.assertEqual(codex.codex_version(user_agent), version)

    def test_ultra_needs_codex_0_153_4_or_newer(self):
        # Compared as numbers, so 0.153.10 is newer and 0.15.4 older.
        for version in ('0.153.4', '0.153.5', '0.153.10', '0.154.0', '0.154.0-alpha.3', '0.1000.0', '1.0.0'):
            with self.subTest(version=version):
                self.assertTrue(codex.version_runs_ultra(version))
        # A pre-release comes before its release, and a version that can't be read can't run it either.
        for version in ('0.153.3', '0.153.4-alpha.2', '0.153', '0.15.4', '0.99.999', '0.0.0', 'unknown', '', None):
            with self.subTest(version=version):
                self.assertFalse(codex.version_runs_ultra(version))


class UltraModelListTests(unittest.IsolatedAsyncioTestCase):
    """Whether Codex runs Ultra with its models, judged by its app-server's version and each model's workers."""

    def setUp(self):
        self.messages = recorded('codex-p.jsonl')
        listed = recorded_answer(self.messages, 'model/list')['result']['data']
        self.model_ids = [entry['id'] for entry in listed]

    def provider(self) -> codex.CodexProvider:
        provider = codex.CodexProvider()
        # Ends a replayed app-server, and so its reader.
        self.addCleanup(provider.stop)
        return provider

    def ultra_models(self, models) -> list[str]:
        return [model.value for model in models if model.ultra]

    async def test_the_recorded_codex_runs_ultra_with_the_models_that_run_its_v2_workers(self):
        provider = self.provider()
        machine = ReplayMachine(ReplayProcess(self.messages))
        models = await provider.list_models(ProviderSettings(enable=True), machine)
        self.assertEqual([model.value for model in models], self.model_ids)
        # Luna runs Codex's v1 workers. No recorded Ultra turn ran those, so
        # nothing shows that Buddy can list them and stop them.
        self.assertEqual(self.ultra_models(models), [CHAT_MODEL, SOL, TERRA])
        self.assertEqual(provider._servers[machine.id].version, CODEX_VERSION)

    async def test_each_model_is_judged_by_the_workers_its_entry_names(self):
        messages = copy.deepcopy(self.messages)
        listed = recorded_answer(messages, 'model/list')['result']['data']
        versions = {CHAT_MODEL: 'v1', SOL: None, LUNA: 'v2'}
        for entry in listed:
            if entry['id'] in versions:
                entry['multiAgentVersion'] = versions[entry['id']]
            else:
                # An entry that names no workers at all.
                del entry['multiAgentVersion']
        models = await self.provider().list_models(
            ProviderSettings(enable=True), ReplayMachine(ReplayProcess(messages))
        )
        self.assertEqual(self.ultra_models(models), [LUNA])

    async def test_an_older_or_unknown_codex_runs_ultra_with_no_model(self):
        user_agents = [
            OLDER_USER_AGENT,
            'buddy/0.153.4-alpha.2 (Windows 10.0.26200; x86_64) xterm-256color (buddy; 1.0)',
            'buddy (Windows 10.0.26200; x86_64) xterm-256color (buddy; 1.0)',
            None,
        ]
        for user_agent in user_agents:
            with self.subTest(user_agent=user_agent):
                process = ReplayProcess(with_user_agent(self.messages, user_agent))
                models = await self.provider().list_models(ProviderSettings(enable=True), ReplayMachine(process))
                self.assertEqual([model.value for model in models], self.model_ids)
                self.assertFalse(any(model.ultra for model in models))

    async def test_a_restarted_app_server_is_judged_by_its_own_version(self):
        provider = self.provider()
        settings = ProviderSettings(enable=True)
        machine = ReplayMachine(ReplayProcess(with_user_agent(self.messages, OLDER_USER_AGENT)))
        older = await provider.list_models(settings, machine)
        self.assertFalse(any(model.ultra for model in older))

        # Codex is updated, and its app-server ends. Its reader sees the output end.
        server = provider._servers[machine.id]
        machine.process.kill()
        await until(server._reader.done)
        machine.process = ReplayProcess(self.messages)
        newer = await provider.list_models(settings, machine)
        self.assertIs(provider._servers[machine.id], server)
        self.assertEqual(server.version, CODEX_VERSION)
        self.assertEqual(self.ultra_models(newer), [CHAT_MODEL, SOL, TERRA])


if __name__ == '__main__':
    unittest.main()
