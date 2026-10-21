"""Offline tests: Claude Code Ultra turns, replayed from recorded Claude Code streams.

The fixtures under fixtures/ultra are what Claude Code 2.1.295 wrote on stdout
for Ultra turns recorded on 2026-10-20. Each one's first line answers the
initialize request that defined Buddy's worker types; the rest is the turn:

- claude-chat.jsonl: two workers at once, in Chat access.
- claude-read.jsonl: a buddy-reader and a buddy-worker at once, in Read access.
- claude-depth.jsonl: a worker that starts a sub-worker, which can't start another.
- claude-background.jsonl: two background workers. The math worker reports
  while the orchestrator writes its first reply, which therefore says neither
  has returned. Claude Code then passes the report on in a turn of its own,
  whose result answers. The recording ends while the essay worker still runs.

The answers' account details were removed, and paths name the user "fixture".

The recordings were made without CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS, which
Ultra turns set, so they lack the message that reports Claude Code's session
idle. Tests that need it add SESSION_IDLE, in the shape of Claude Code
2.1.295's session_state_changed message.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_claude.py -v
"""

import asyncio
import copy
import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from buddy_runner.process import ProcessClosedError  # noqa: E402
from open_webui.utils.subscriptions import claude_code, ultra  # noqa: E402
from open_webui.utils.subscriptions.common import ProviderSettings, TurnRequest, UltraOptions  # noqa: E402
from open_webui.utils.subscriptions.conversation import parse_messages  # noqa: E402
from open_webui.utils.subscriptions.events import (  # noqa: E402
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_RUNNING,
    WORKER_STOPPED,
    StatusUpdate,
    TextDelta,
    TokenUsage,
    TurnFailed,
    WorkerUpdate,
)

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'ultra'
PROMPT = 'What is 17 times 23, and what are three prime numbers above 100?'
FOLDER_INSTRUCTIONS = 'Folder instructions: answer in plain words.'
HAIKU_LOW = UltraOptions(worker_model='haiku', worker_label='Claude Haiku', worker_effort='low')
SAME_AS_CHAT = UltraOptions()
TOO_OLD_STATUS = 'Ultra is off for this reply: Claude Code on Fixture PC is too old for Ultra.'
MISSING_SESSION = 'No conversation found with session ID: old-session'
ESSAY_STOPPED_REPLY = 'The essay worker was stopped, so there is no essay.'
# What `claude --version` printed for the recordings.
CLAUDE_CODE_VERSION = '2.1.295 (Claude Code)'


def recorded(name: str) -> list[dict]:
    """The messages of a recorded Claude Code stream, one per stdout line."""
    lines = (FIXTURES / name).read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines]


def recorded_turn(name: str) -> tuple[dict, list[dict]]:
    """The recorded answer to the initialize request, and the turn that followed it."""
    messages = recorded(name)
    return messages[0], messages[1:]


def recorded_chat() -> tuple[dict, list[dict]]:
    return recorded_turn('claude-chat.jsonl')


def without_buddys_workers(answer: dict) -> dict:
    """The recorded initialize answer with only Claude Code's own agent types, as an older Claude Code lists."""
    older = copy.deepcopy(answer)
    agents = older['response']['response']['agents']
    older['response']['response']['agents'] = [agent for agent in agents if not agent['name'].startswith('buddy-')]
    return older


def plain_reply(text: str, session_id: str = 'plain-session') -> list[dict]:
    """The output of a turn without workers."""
    return [
        {'type': 'system', 'subtype': 'init', 'session_id': session_id},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}},
        {'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session_id, 'usage': {}},
    ]


MISSING_SESSION_RESULT = {
    'type': 'result',
    'subtype': 'error_during_execution',
    'is_error': True,
    'session_id': 'unused',
    'usage': {},
}

# What Claude Code says once an Ultra turn is over: its last turn has ended,
# every background worker has ended, and no report waits for a turn of its own.
SESSION_IDLE = {'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle', 'session_id': 'idle-session'}


def until_idle(turn_output: list[dict]) -> list[dict]:
    """A turn's output followed by Claude Code reporting its session idle, which ends an Ultra reply."""
    return [*turn_output, SESSION_IDLE]


def stopped_notification(task_id: str) -> dict:
    """What Claude Code reports once it has stopped a task, shaped like the recorded task_notification messages."""
    return {
        'type': 'system',
        'subtype': 'task_notification',
        'task_id': task_id,
        'status': 'stopped',
        'summary': '',
        'session_id': 'stopped-session',
    }


class ReplayProcess:
    """A ``claude -p`` process that answers the way the recorded one did.

    It answers an initialize request with ``initialize_answer`` and only then
    writes out ``turn_output``, once the prompt is written. It answers a
    stop_task request with ``stop_output`` for that task, or else with a
    task_notification that the task stopped. When it has nothing more to
    say, its output ends, or with ``silent`` it waits to be written to. Once
    its output ends, it can't be written to, like a CLI that has exited.
    """

    def __init__(self, initialize_answer=None, turn_output=(), stderr='', silent=False, stop_output=None):
        self.initialize_answer = initialize_answer
        self.turn_output = list(turn_output)
        self.stderr = stderr
        self.silent = silent
        self.stop_output = stop_output or {}
        self.written = []
        # What Buddy did to the process, in order: each request's subtype or
        # message's type, then close_stdin and kill.
        self.calls = []
        self.killed = False
        self.args = []
        self.extra_env = None
        self.temp_files = None
        # Set while it waits to be written to, with nothing left to say.
        self.idle = asyncio.Event()
        self._written_to = asyncio.Event()
        self._output_ended = False
        self._lines = []

    async def write(self, text):
        if self._output_ended:
            raise ProcessClosedError('claude is no longer running')
        message = json.loads(text)
        self.written.append(message)
        self.idle.clear()
        if message['type'] == 'user':
            self.calls.append('user')
            self._say(self.turn_output)
            return
        request = message['request']
        self.calls.append(request['subtype'])
        if request['subtype'] == 'initialize' and self.initialize_answer is not None:
            answer = copy.deepcopy(self.initialize_answer)
            answer['response']['request_id'] = message['request_id']
            self._say([answer])
        elif request['subtype'] == 'stop_task':
            task_id = request['task_id']
            self._say(self.stop_output.get(task_id, [stopped_notification(task_id)]))

    def _say(self, messages):
        self._lines.extend(json.dumps(message) for message in messages)
        self._written_to.set()

    async def read_line(self):
        while not self._lines:
            if not self.silent:
                self._output_ended = True
                return None
            self.idle.set()
            self._written_to.clear()
            await self._written_to.wait()
        return self._lines.pop(0)

    async def finish_output(self, timeout):
        return None

    def stderr_text(self):
        return self.stderr

    def close_stdin(self):
        self.calls.append('close_stdin')

    def kill(self):
        self.calls.append('kill')
        self.killed = True

    def tool_list(self) -> str:
        return self.args[self.args.index('--tools') + 1]

    def unread_lines(self) -> int:
        """How many output lines Buddy never read before it ended the CLI."""
        return len(self._lines)


class ReplayMachine:
    id = 'fixture-pc'
    name = 'Fixture PC'

    def __init__(self, *processes):
        self.processes = list(processes)
        self.started = []

    async def find_tool(self, tool, configured_path=''):
        return 'claude'

    async def start_process(self, args, cwd, extra_env=None, temp_files=None):
        process = self.processes[len(self.started)]
        process.args = args
        process.extra_env = extra_env
        process.temp_files = temp_files
        self.started.append(process)
        return process


class VersionedMachine(ReplayMachine):
    """A ReplayMachine whose Claude Code is signed in to a plan and answers ``--version``.

    ``version_output`` is what ``claude --version`` prints, or an error it
    raises, and ``version_code`` its exit code.
    """

    def __init__(self, *processes, version_output=CLAUDE_CODE_VERSION, version_code=0):
        super().__init__(*processes)
        self.version_output = version_output
        self.version_code = version_code

    async def chat_dir(self):
        return '.'

    async def run(self, args, cwd, timeout, extra_env=None):
        if '--version' in args:
            if isinstance(self.version_output, Exception):
                raise self.version_output
            return self.version_code, self.version_output, ''
        return 0, json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'}), ''


def ultra_turn(
    directory, machine=None, access='chat', options=HAIKU_LOW, system=FOLDER_INSTRUCTIONS, effort=None, earlier=()
):
    """An Ultra turn as the server hands it on, with the orchestrator note ahead of the chat's instructions."""
    messages = [*earlier, {'role': 'user', 'content': PROMPT}]
    if system:
        messages.insert(0, {'role': 'system', 'content': system})
    conversation = parse_messages(messages)
    noted = ultra.with_orchestrator_note(conversation.system, claude_code.PROVIDER_ID, access, options)
    return TurnRequest(
        model='opus',
        conversation=dataclasses.replace(conversation, system=noted),
        settings=ProviderSettings(enable=True, access=access),
        cwd=directory,
        effort=effort,
        machine=machine,
        ultra_options=options,
    )


def texts(message: dict) -> list[str]:
    return [block['text'] for block in message['message']['content'] if block['type'] == 'text']


def statuses(events: list) -> list[str]:
    return [event.description for event in events if isinstance(event, StatusUpdate)]


def reply(events: list) -> str:
    return ''.join(event.text for event in events if isinstance(event, TextDelta))


def system_messages(messages: list[dict], subtype: str) -> list[dict]:
    return [message for message in messages if message['type'] == 'system' and message.get('subtype') == subtype]


def results(messages: list[dict]) -> list[dict]:
    return [message for message in messages if message['type'] == 'result']


def worker_updates(events: list, worker_id: str) -> list[WorkerUpdate]:
    return [event for event in events if isinstance(event, WorkerUpdate) and event.worker_id == worker_id]


def latest_workers(events: list) -> dict[str, WorkerUpdate]:
    """Each worker's last update, in the order the workers were first reported."""
    latest = {}
    for event in events:
        if isinstance(event, WorkerUpdate):
            latest[event.worker_id] = event
    return latest


def first_update_index(events: list, worker_id: str, state: str) -> int:
    """Where in the turn's events a worker was first reported in this state."""
    for index, event in enumerate(events):
        if isinstance(event, WorkerUpdate) and event.worker_id == worker_id and event.state == state:
            return index
    raise AssertionError(f'{worker_id} was never {state}')


def failures(events: list) -> list[str]:
    return [event.message for event in events if isinstance(event, TurnFailed)]


def last_usage(events: list) -> TokenUsage:
    return [event for event in events if isinstance(event, TokenUsage)][-1]


def stop_requests(process: ReplayProcess) -> list[str]:
    """The task ids of the stop_task requests written to a process, in order."""
    task_ids = []
    for message in process.written:
        if message['type'] == 'control_request' and message['request']['subtype'] == 'stop_task':
            task_ids.append(message['request']['task_id'])
    return task_ids


def started_tasks(turn_output: list[dict]) -> list[str]:
    return [task['task_id'] for task in system_messages(turn_output, 'task_started')]


async def until(condition) -> None:
    """Let the other tasks run until ``condition()`` holds."""
    while not condition():
        await asyncio.sleep(0)


def background_essay_finished(turn_output: list[dict]) -> list[dict]:
    """What Claude Code says when the background essay worker finishes: its report, and no background task left.

    Not recorded: the recording ends while the essay worker still runs. This
    follows the shape of the recorded task_notification and background list
    messages.
    """
    essay = system_messages(turn_output, 'task_started')[0]
    session_id = results(turn_output)[-1]['session_id']
    return [
        {
            'type': 'system',
            'subtype': 'task_notification',
            'task_id': essay['task_id'],
            'tool_use_id': essay['tool_use_id'],
            'status': 'completed',
            'summary': 'Bridges, from fallen logs to suspension spans.',
            'usage': {'total_tokens': 4096, 'tool_uses': 0, 'duration_ms': 60000},
            'session_id': session_id,
        },
        {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': [], 'session_id': session_id},
    ]


def background_essay_stopped(turn_output: list[dict]) -> list[dict]:
    """What Claude Code says once the background essay worker is stopped, ending with a third result.

    Not recorded: the recording ends while the essay worker still runs. This
    follows the shape of the recorded task_notification, background list and
    result messages. The third result's modelUsage is the second's running
    total plus its own usage.
    """
    essay = system_messages(turn_output, 'task_started')[0]
    session_id = results(turn_output)[-1]['session_id']
    return [
        {
            'type': 'system',
            'subtype': 'task_notification',
            'task_id': essay['task_id'],
            'tool_use_id': essay['tool_use_id'],
            'status': 'stopped',
            'summary': 'The essay was stopped before it was finished.',
            'usage': {'total_tokens': 2048, 'tool_uses': 0, 'duration_ms': 30000},
            'session_id': session_id,
        },
        {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': [], 'session_id': session_id},
        {'type': 'system', 'subtype': 'init', 'session_id': session_id},
        {
            'type': 'assistant',
            'message': {'content': [{'type': 'text', 'text': ESSAY_STOPPED_REPLY}]},
            'parent_tool_use_id': None,
            'session_id': session_id,
        },
        {
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': ESSAY_STOPPED_REPLY,
            'session_id': session_id,
            'usage': {
                'input_tokens': 3,
                'cache_read_input_tokens': 5000,
                'cache_creation_input_tokens': 40,
                'output_tokens': 12,
            },
            'modelUsage': {
                'claude-haiku-5-5': {
                    'inputTokens': 13,
                    'outputTokens': 714,
                    'cacheReadInputTokens': 12629,
                    'cacheCreationInputTokens': 6028,
                },
            },
        },
    ]


class WorkerTypeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def test_a_worker_has_the_turns_tools_and_a_reader_only_reads(self):
        agents = claude_code.worker_types(ultra_turn(self.directory, access='full'))
        worker_prompt = f'{ultra.WORKER_PROMPT}\n\n{FOLDER_INSTRUCTIONS}'
        # No tools of its own, so a buddy-worker has the turn's: the same access as the orchestrator.
        self.assertEqual(
            agents[ultra.WORKER_AGENT],
            {
                'description': claude_code.WORKER_DESCRIPTION,
                'prompt': worker_prompt,
                'model': 'haiku',
                'effort': 'low',
            },
        )
        self.assertEqual(
            agents[ultra.READER_AGENT],
            {
                'description': claude_code.READER_DESCRIPTION,
                'prompt': worker_prompt,
                'model': 'haiku',
                'effort': 'low',
                'tools': ['Read', 'Grep', 'Glob', 'WebSearch'],
            },
        )

    def test_only_read_and_full_turns_offer_a_reader(self):
        # A Chat turn has no file tools for a reader to keep.
        chat_agents = claude_code.worker_types(ultra_turn(self.directory, access='chat'))
        self.assertEqual(list(chat_agents), [ultra.WORKER_AGENT])
        for access in ('read', 'full'):
            with self.subTest(access=access):
                agents = claude_code.worker_types(ultra_turn(self.directory, access=access))
                self.assertEqual(list(agents), [ultra.WORKER_AGENT, ultra.READER_AGENT])
                self.assertNotIn('tools', agents[ultra.WORKER_AGENT])
                # A reader can't start sub-workers or send files anywhere.
                self.assertNotIn('Agent', agents[ultra.READER_AGENT]['tools'])
                self.assertNotIn('WebFetch', agents[ultra.READER_AGENT]['tools'])

    def test_workers_keep_the_chats_model_and_level_unless_the_person_chose_others(self):
        def worker(options, effort):
            turn = ultra_turn(self.directory, options=options, effort=effort)
            return claude_code.worker_types(turn)[ultra.WORKER_AGENT]

        same_as_chat = worker(SAME_AS_CHAT, 'high')
        self.assertEqual((same_as_chat['model'], same_as_chat['effort']), ('inherit', 'high'))
        # With no level for the chat either, Claude Code picks the workers' own.
        self.assertNotIn('effort', worker(SAME_AS_CHAT, None))
        self.assertEqual(worker(SAME_AS_CHAT, 'none')['effort'], 'low')
        chosen_level = worker(UltraOptions(worker_effort='max'), 'low')
        self.assertEqual((chosen_level['model'], chosen_level['effort']), ('inherit', 'max'))
        chosen_model = worker(UltraOptions(worker_model='claude-fable-5-1[1m]', worker_label='Claude Fable'), 'medium')
        self.assertEqual((chosen_model['model'], chosen_model['effort']), ('claude-fable-5-1[1m]', 'medium'))

    def test_workers_get_the_chats_instructions_without_the_orchestrator_note(self):
        for access in ('chat', 'read', 'full'):
            with self.subTest(access=access):
                note = ultra.orchestrator_note(claude_code.PROVIDER_ID, access, HAIKU_LOW)
                agents = claude_code.worker_types(ultra_turn(self.directory, access=access))
                for agent in agents.values():
                    self.assertTrue(agent['prompt'].startswith(ultra.WORKER_PROMPT))
                    self.assertTrue(agent['prompt'].endswith(FOLDER_INSTRUCTIONS))
                    self.assertNotIn(ultra.ORCHESTRATOR_INTRO, agent['prompt'])
                    self.assertNotIn(note, agent['prompt'])
        bare = claude_code.worker_types(ultra_turn(self.directory, system=''))
        self.assertEqual(bare[ultra.WORKER_AGENT]['prompt'], ultra.WORKER_PROMPT)


class UltraHandshakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.provider = claude_code.ClaudeCodeProvider(Path(directory.name))

    async def run_turn(self, turn):
        return [event async for event in self.provider.run_turn(turn)]

    async def test_an_ultra_turn_defines_buddys_workers_before_its_prompt(self):
        answer, turn_output = recorded_chat()
        process = ReplayProcess(answer, until_idle(turn_output))
        machine = ReplayMachine(process)
        turn = ultra_turn(self.directory, machine, access='full')
        events = await self.run_turn(turn)

        self.assertEqual(len(process.written), 2)
        self.assertEqual(
            process.written[0],
            {
                'type': 'control_request',
                'request_id': claude_code.WORKERS_REQUEST_ID,
                'request': {'subtype': 'initialize', 'hooks': None, 'agents': claude_code.worker_types(turn)},
            },
        )
        # Then the prompt, with the Ultra note as a block of its own at the end.
        prompt = process.written[1]
        self.assertEqual(prompt['type'], 'user')
        self.assertEqual(texts(prompt), [PROMPT, ultra.ULTRA_TURN_NOTE])
        self.assertEqual(reply(events), turn_output[-1]['result'])
        self.assertFalse(any(isinstance(event, TurnFailed) for event in events))
        self.assertNotIn(TOO_OLD_STATUS, statuses(events))

    async def test_a_fresh_session_ends_its_chat_transcript_with_the_ultra_note(self):
        answer, turn_output = recorded_chat()
        process = ReplayProcess(answer, until_idle(turn_output))
        earlier = [{'role': 'user', 'content': 'Hi'}, {'role': 'assistant', 'content': 'Hello!'}]
        await self.run_turn(ultra_turn(self.directory, ReplayMachine(process), earlier=earlier))
        prompt_texts = texts(process.written[1])
        self.assertIn('My latest message:\n' + PROMPT, prompt_texts[-2])
        self.assertEqual(prompt_texts[-1], ultra.ULTRA_TURN_NOTE)

    async def test_turns_without_ultra_define_no_workers(self):
        conversation = parse_messages([{'role': 'user', 'content': PROMPT}])
        settings = ProviderSettings(enable=True, access='chat')
        plain = ReplayProcess(turn_output=plain_reply('Hello!'))
        # Titles and follow-ups never start workers, even while Ultra is on.
        task = ReplayProcess(turn_output=plain_reply('A title'))
        machine = ReplayMachine(plain, task)
        await self.run_turn(TurnRequest('opus', conversation, settings, self.directory, machine=machine))
        task_turn = TurnRequest(
            'opus', conversation, settings, self.directory, is_task=True, machine=machine, ultra_options=HAIKU_LOW
        )
        await self.run_turn(task_turn)
        for process in (plain, task):
            self.assertEqual([message['type'] for message in process.written], ['user'])
            self.assertEqual(texts(process.written[0]), [PROMPT])

    async def test_an_older_claude_code_runs_the_reply_without_ultra(self):
        answer, _ = recorded_chat()
        refusals = {
            'buddy-worker is not listed': without_buddys_workers(answer),
            'the request is refused': {
                'type': 'control_response',
                'response': {'subtype': 'error', 'request_id': 'any', 'error': 'Unknown field: agents'},
            },
        }
        for name, refusal in refusals.items():
            with self.subTest(name):
                older = ReplayProcess(refusal, turn_output=recorded_chat()[1])
                plain = ReplayProcess(turn_output=plain_reply('391, and 101, 103 and 107.'))
                turn = ultra_turn(self.directory, ReplayMachine(older, plain))
                events = await self.run_turn(turn)

                self.assertEqual(events[0], StatusUpdate(TOO_OLD_STATUS))
                self.assertEqual(reply(events), '391, and 101, 103 and 107.')
                self.assertFalse(any(isinstance(event, TurnFailed) for event in events))
                # The prompt never reached the older CLI.
                self.assertEqual([message['type'] for message in older.written], ['control_request'])
                self.assertTrue(older.killed)
                # The reply runs as a normal Chat turn: no Agent tool, no workers, no Ultra notes.
                self.assertEqual([message['type'] for message in plain.written], ['user'])
                self.assertEqual(texts(plain.written[0]), [PROMPT])
                self.assertEqual(plain.tool_list(), '')
                self.assertEqual(plain.extra_env, {'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'})
                instructions = plain.temp_files[claude_code.INSTRUCTIONS_FILE]
                self.assertNotIn(ultra.ORCHESTRATOR_INTRO, instructions)
                self.assertIn(FOLDER_INSTRUCTIONS, instructions)

    async def test_no_answer_in_time_runs_the_reply_without_ultra(self):
        silent = ReplayProcess(silent=True)
        plain = ReplayProcess(turn_output=plain_reply('Done.'))
        turn = ultra_turn(self.directory, ReplayMachine(silent, plain))
        with patch.object(claude_code, 'WORKERS_ANSWER_TIMEOUT', 0.05):
            with self.assertLogs(claude_code.log, level='WARNING'):
                events = await self.run_turn(turn)
        self.assertEqual(events, [StatusUpdate(TOO_OLD_STATUS), TextDelta('Done.')])
        self.assertTrue(silent.killed)
        self.assertEqual(len(silent.written), 1)

    async def test_claude_code_ending_before_it_answers_keeps_ultra_for_a_fresh_session(self):
        # A resumed session that is gone ends the CLI before it answers. That
        # says nothing about Ultra, so the fresh session defines workers again.
        answer, turn_output = recorded_chat()
        gone = ReplayProcess(stderr=MISSING_SESSION)
        fresh = ReplayProcess(answer, until_idle(turn_output))
        machine = ReplayMachine(gone, fresh)
        turn = ultra_turn(self.directory, machine)
        self.provider._sessions.put(self.provider._session_key(turn, turn.conversation.history), 'old-session')
        events = await self.run_turn(turn)

        self.assertIn('--resume', gone.args)
        self.assertNotIn('--resume', fresh.args)
        self.assertEqual(statuses(events).count(TOO_OLD_STATUS), 0)
        self.assertEqual([message['type'] for message in fresh.written], ['control_request', 'user'])
        self.assertEqual(fresh.tool_list(), claude_code.ULTRA_CHAT_TOOLS)
        self.assertEqual(reply(events), turn_output[-1]['result'])

    async def test_a_reply_that_ran_without_ultra_starts_a_fresh_session_without_it(self):
        answer, _ = recorded_chat()
        older = ReplayProcess(without_buddys_workers(answer))
        gone = ReplayProcess(turn_output=[MISSING_SESSION_RESULT], stderr=MISSING_SESSION)
        fresh = ReplayProcess(turn_output=plain_reply('Fresh reply.'))
        machine = ReplayMachine(older, gone, fresh)
        turn = ultra_turn(self.directory, machine)
        self.provider._sessions.put(self.provider._session_key(turn, turn.conversation.history), 'old-session')
        events = await self.run_turn(turn)

        self.assertEqual(machine.started, [older, gone, fresh])
        self.assertIn('--resume', gone.args)
        self.assertNotIn('--resume', fresh.args)
        # Said once, and the fresh session doesn't ask the older CLI again.
        self.assertEqual(statuses(events), [TOO_OLD_STATUS])
        self.assertEqual([message['type'] for message in fresh.written], ['user'])
        self.assertEqual(texts(fresh.written[0]), [PROMPT])
        self.assertEqual(fresh.tool_list(), '')
        self.assertEqual(reply(events), 'Fresh reply.')


class UltraWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Workers reported from the recorded turns, and how long the turns are read."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.provider = claude_code.ClaudeCodeProvider(Path(directory.name))

    async def replay(self, name, access='chat', extra_output=(), silent=False, idle=True):
        """Run an Ultra turn against a recorded stream; returns its events, the process, and the recorded turn.

        With ``idle``, Claude Code reports its session idle after the
        recording and ``extra_output``.
        """
        answer, turn_output = recorded_turn(name)
        output = [*turn_output, *extra_output]
        if idle:
            output = until_idle(output)
        process = ReplayProcess(answer, output, silent=silent)
        turn = ultra_turn(self.directory, ReplayMachine(process), access=access)
        events = [event async for event in self.provider.run_turn(turn)]
        return events, process, turn_output

    def assert_worker_started(self, events, task: dict, parent_id=None, depth=1):
        """The worker's first update says it runs, as the person chose, under the worker that started it."""
        self.assertEqual(
            worker_updates(events, task['task_id'])[0],
            WorkerUpdate(
                worker_id=task['task_id'],
                parent_id=parent_id,
                depth=depth,
                label=task['description'],
                state=WORKER_RUNNING,
                model='Claude Haiku',
                effort='low',
            ),
        )

    def assert_worker_ended(self, events, notification: dict):
        """The worker's last update carries its report and the tokens it used."""
        latest = latest_workers(events)[notification['task_id']]
        self.assertEqual(latest.state, WORKER_DONE)
        self.assertEqual(latest.summary, notification['summary'])
        self.assertEqual(latest.tokens, notification['usage']['total_tokens'])
        self.assertIsNone(latest.activity)

    async def test_parallel_workers_are_listed_from_start_to_finish(self):
        events, process, turn_output = await self.replay('claude-chat.jsonl')
        started = system_messages(turn_output, 'task_started')
        notifications = system_messages(turn_output, 'task_notification')

        self.assertEqual(list(latest_workers(events)), [task['task_id'] for task in started])
        for task in started:
            self.assert_worker_started(events, task)
        for notification in notifications:
            self.assert_worker_ended(events, notification)
        # Both ran at once: the second started before the first ended.
        first, second = (task['task_id'] for task in started)
        self.assertLess(
            first_update_index(events, second, WORKER_RUNNING), first_update_index(events, first, WORKER_DONE)
        )
        # The workers' own text stays out of the reply, and their start has no status line of its own here.
        self.assertEqual(reply(events), results(turn_output)[0]['result'])
        self.assertFalse(any(status.startswith('Starting a subagent') for status in statuses(events)))
        self.assertEqual(failures(events), [])
        self.assertTrue(process.killed)

    async def test_read_workers_report_what_they_are_doing(self):
        events, _, turn_output = await self.replay('claude-read.jsonl', access='read')
        started = system_messages(turn_output, 'task_started')
        self.assertEqual([task['subagent_type'] for task in started], [ultra.READER_AGENT, ultra.WORKER_AGENT])

        for task in started:
            self.assert_worker_started(events, task)
        for progress in system_messages(turn_output, 'task_progress'):
            updates = worker_updates(events, progress['task_id'])
            # Claude Code's own line for the worker's latest step, such as "Finding *.txt".
            doing = [update for update in updates if update.activity == progress['description']]
            self.assertEqual(len(doing), 1)
            self.assertEqual(doing[0].state, WORKER_RUNNING)
            self.assertEqual(doing[0].tokens, progress['usage']['total_tokens'])
        for notification in system_messages(turn_output, 'task_notification'):
            self.assert_worker_ended(events, notification)
        # The workers' Glob and Read calls stay out of the reply's status lines.
        self.assertEqual(statuses(events), [])
        self.assertEqual(reply(events), results(turn_output)[0]['result'])
        self.assertEqual(failures(events), [])

    async def test_an_ultra_reply_counts_its_workers_tokens(self):
        # Each recording's last modelUsage, which counts the orchestrator and
        # its workers. Its usage counts only the orchestrator: for the Chat
        # turn, 7219 tokens in and 363 out.
        totals = {
            'claude-chat.jsonl': TokenUsage(input_tokens=9438, output_tokens=423, cached_input_tokens=3209),
            'claude-read.jsonl': TokenUsage(input_tokens=32218, output_tokens=1221, cached_input_tokens=15255),
            'claude-depth.jsonl': TokenUsage(input_tokens=15960, output_tokens=1794, cached_input_tokens=9597),
        }
        for name, total in totals.items():
            with self.subTest(name):
                events, _, _ = await self.replay(name)
                self.assertEqual(last_usage(events), total)

    async def test_each_result_of_an_ultra_reply_reports_the_running_total(self):
        _, turn_output = recorded_turn('claude-background.jsonl')
        events, _, _ = await self.replay('claude-background.jsonl', extra_output=background_essay_finished(turn_output))
        # modelUsage adds up over the run, so the second result's replaces the first's.
        self.assertEqual(
            [event for event in events if isinstance(event, TokenUsage)],
            [
                TokenUsage(input_tokens=8719, output_tokens=680, cached_input_tokens=3329),
                TokenUsage(input_tokens=13627, output_tokens=702, cached_input_tokens=7629),
            ],
        )

    async def test_a_worker_started_by_a_worker_is_listed_under_it(self):
        events, _, turn_output = await self.replay('claude-depth.jsonl')
        level1, level2 = system_messages(turn_output, 'task_started')

        self.assert_worker_started(events, level1)
        # Started by level 1's own Agent call, as Claude Code's general-purpose type.
        self.assertEqual(level2['subagent_type'], 'general-purpose')
        self.assert_worker_started(events, level2, parent_id=level1['task_id'], depth=2)
        for notification in system_messages(turn_output, 'task_notification'):
            self.assert_worker_ended(events, notification)
        self.assertEqual(len(latest_workers(events)), 2)
        self.assertEqual(reply(events), results(turn_output)[0]['result'])

    async def test_a_sub_worker_is_placed_by_its_parent_task_when_its_caller_is_not_streamed(self):
        answer, turn_output = recorded_turn('claude-depth.jsonl')
        # Without level 1's own messages, only task_started's parent_task_id says who started level 2.
        main_thread = [message for message in turn_output if not message.get('parent_tool_use_id')]
        process = ReplayProcess(answer, main_thread)
        events = [event async for event in self.provider.run_turn(ultra_turn(self.directory, ReplayMachine(process)))]
        level1, level2 = system_messages(turn_output, 'task_started')
        self.assert_worker_started(events, level2, parent_id=level1['task_id'], depth=2)

    async def test_a_background_worker_keeps_the_turn_open_past_two_results(self):
        _, turn_output = recorded_turn('claude-background.jsonl')
        essay_stopped = background_essay_stopped(turn_output)
        events, process, _ = await self.replay('claude-background.jsonl', extra_output=essay_stopped)
        essay, math = system_messages(turn_output, 'task_started')
        self.assertTrue(essay['is_backgrounded'])
        first, second = results(turn_output)
        [third] = results(essay_stopped)

        # Every result's text is in the reply, in order, and the turn ended
        # when Claude Code reported its session idle after the third.
        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}\n\n{third['result']}")
        self.assertEqual(process.unread_lines(), 0)
        self.assertEqual(failures(events), [])
        self.assertTrue(process.killed)
        # Listed in the background just before its task_started, which changes nothing.
        self.assertEqual(
            [update.state for update in worker_updates(events, essay['task_id'])], [WORKER_RUNNING, WORKER_STOPPED]
        )
        self.assert_worker_started(events, essay)
        self.assertEqual(
            latest_workers(events)[essay['task_id']].summary, 'The essay was stopped before it was finished.'
        )
        [math_report] = system_messages(turn_output, 'task_notification')
        self.assertEqual(math_report['task_id'], math['task_id'])
        self.assert_worker_ended(events, math_report)
        # The reply's usage is the third result's running total, workers included.
        self.assertEqual(
            last_usage(events), TokenUsage(input_tokens=18670, output_tokens=714, cached_input_tokens=12629)
        )

    async def test_a_report_that_arrives_while_the_orchestrator_writes_gets_a_turn_of_its_own(self):
        answer, turn_output = recorded_turn('claude-background.jsonl')
        essay, math = system_messages(turn_output, 'task_started')
        [math_report] = system_messages(turn_output, 'task_notification')
        first, second = results(turn_output)
        # The math worker reported before the first result, which says it
        # hasn't. The second result, in a turn of Claude Code's own, passes
        # the report on.
        self.assertLess(turn_output.index(math_report), turn_output.index(first))
        self.assertEqual(second['origin']['kind'], 'task-notification')
        self.assertEqual(second['origin']['runId'], math['run_id'])
        # Here the essay worker also finishes before the first result, so no worker runs there.
        at_first = turn_output.index(first)
        output = [*turn_output[:at_first], *background_essay_finished(turn_output), *turn_output[at_first:]]
        process = ReplayProcess(answer, until_idle(output))
        events = [event async for event in self.provider.run_turn(ultra_turn(self.directory, ReplayMachine(process)))]

        # Buddy read on to the later turn's answer, and ended when Claude Code reported its session idle.
        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        self.assertEqual(process.unread_lines(), 0)
        self.assertEqual(failures(events), [])
        self.assertEqual(latest_workers(events)[essay['task_id']].state, WORKER_DONE)
        self.assertEqual(latest_workers(events)[math['task_id']].state, WORKER_DONE)
        self.assertEqual(stop_requests(process), [])

    async def test_idle_ends_an_ultra_turn_only_after_its_result_with_no_worker_running(self):
        answer, turn_output = recorded_turn('claude-background.jsonl')
        first, second = results(turn_output)
        init = system_messages(turn_output, 'init')[0]
        at_init = turn_output.index(init) + 1
        at_first = turn_output.index(first) + 1
        # Idle before any result, and after the first result while the essay worker runs, ends nothing.
        output = [
            *turn_output[:at_init],
            SESSION_IDLE,
            *turn_output[at_init:at_first],
            SESSION_IDLE,
            *turn_output[at_first:],
            *background_essay_finished(turn_output),
            SESSION_IDLE,
        ]
        process = ReplayProcess(answer, output)
        events = [event async for event in self.provider.run_turn(ultra_turn(self.directory, ReplayMachine(process)))]

        # The turn ended at the last idle, once the essay worker had finished.
        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        self.assertEqual(process.unread_lines(), 0)
        self.assertEqual(failures(events), [])
        self.assertFalse(any(update.state == WORKER_RUNNING for update in latest_workers(events).values()))

    async def test_an_ultra_turn_ends_soon_after_its_result_when_claude_code_never_says_idle(self):
        with patch.object(claude_code, 'TURN_OVER_GRACE', 0.05):
            with self.assertLogs(claude_code.log, level='WARNING'):
                events, process, turn_output = await self.replay('claude-chat.jsonl', silent=True, idle=False)

        self.assertEqual(reply(events), results(turn_output)[0]['result'])
        self.assertEqual(failures(events), [])
        self.assertEqual(process.calls, ['initialize', 'user', 'close_stdin', 'kill'])

    async def test_output_ending_while_a_worker_runs_stops_it_with_the_turn(self):
        # The recording ends while the essay worker runs, as if Claude Code had exited.
        events, process, turn_output = await self.replay('claude-background.jsonl', idle=False)
        essay = system_messages(turn_output, 'task_started')[0]
        first, second = results(turn_output)

        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        latest = latest_workers(events)[essay['task_id']]
        self.assertEqual((latest.state, latest.summary), (WORKER_STOPPED, claude_code.WORKER_ENDED_WITH_TURN))
        self.assertEqual(len(failures(events)), 1)
        self.assertTrue(failures(events)[0].startswith('Claude Code stopped before finishing the reply.'))
        # Claude Code had exited, so asking it to stop the essay worker failed quietly.
        self.assertNotIn('stop_task', process.calls)
        self.assertTrue(process.killed)

    async def test_an_ultra_turn_stops_with_its_workers_at_the_time_limit(self):
        # Buddy doesn't end a turn soon after its result while a worker still runs.
        with patch.object(claude_code, 'TURN_OVER_GRACE', 0.01), patch.object(claude_code, 'ULTRA_TIME_LIMIT', 0.2):
            with self.assertLogs(claude_code.log, level='WARNING'):
                events, process, turn_output = await self.replay('claude-background.jsonl', silent=True, idle=False)
        essay = system_messages(turn_output, 'task_started')[0]
        first, second = results(turn_output)

        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        self.assertEqual(latest_workers(events)[essay['task_id']].state, WORKER_STOPPED)
        self.assertEqual(failures(events), [claude_code.ULTRA_TIME_LIMIT_MESSAGE])
        # Claude Code was asked to stop the essay worker before the CLI was ended.
        self.assertEqual(stop_requests(process), [essay['task_id']])
        self.assertEqual(process.calls[-3:], ['stop_task', 'close_stdin', 'kill'])

    async def test_an_error_result_ends_an_ultra_turn_while_a_worker_runs(self):
        answer, turn_output = recorded_turn('claude-background.jsonl')
        # The first result, while the essay worker runs, becomes an error.
        first_result = turn_output.index(results(turn_output)[0])
        error = {
            'type': 'result',
            'subtype': 'error_during_execution',
            'is_error': True,
            'session_id': 'x',
            'usage': {},
        }
        output = [*turn_output[:first_result], error, *turn_output[first_result + 1 :]]
        process = ReplayProcess(answer, output, stderr='API Error: 500')
        events = [event async for event in self.provider.run_turn(ultra_turn(self.directory, ReplayMachine(process)))]
        essay, math = system_messages(turn_output, 'task_started')

        self.assertEqual(failures(events), ['API Error: 500'])
        self.assertEqual(latest_workers(events)[essay['task_id']].state, WORKER_STOPPED)
        self.assertEqual(latest_workers(events)[math['task_id']].state, WORKER_DONE)
        # Nothing after the error reached the reply. Buddy read on only while
        # Claude Code stopped the essay worker, before the CLI was ended.
        self.assertNotIn(results(turn_output)[1]['result'], reply(events))
        self.assertEqual(stop_requests(process), [essay['task_id']])
        self.assertEqual(process.calls[-3:], ['stop_task', 'close_stdin', 'kill'])

    async def test_a_normal_turn_still_ends_at_its_first_result(self):
        _, turn_output = recorded_turn('claude-background.jsonl')
        process = ReplayProcess(turn_output=turn_output)
        conversation = parse_messages([{'role': 'user', 'content': PROMPT}])
        settings = ProviderSettings(enable=True, access='chat')
        turn = TurnRequest('opus', conversation, settings, self.directory, machine=ReplayMachine(process))
        events = [event async for event in self.provider.run_turn(turn)]
        first_result = turn_output.index(results(turn_output)[0])

        self.assertEqual(reply(events), turn_output[first_result]['result'])
        self.assertFalse(any(isinstance(event, WorkerUpdate) for event in events))
        # The result's own usage, as before Ultra.
        self.assertEqual(
            last_usage(events), TokenUsage(input_tokens=7633, output_tokens=677, cached_input_tokens=3329)
        )
        self.assertIn('Starting a subagent: Compute 6 times 7', statuses(events))
        self.assertEqual(process.unread_lines(), len(turn_output) - first_result - 1)
        self.assertEqual(failures(events), [])
        self.assertTrue(process.killed)


class UltraStopTests(unittest.IsolatedAsyncioTestCase):
    """Stopping one worker of a live Ultra turn, and stopping the whole turn."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.provider = claude_code.ClaudeCodeProvider(Path(directory.name))

    async def start(self, process: ReplayProcess, turn: TurnRequest):
        """Run the turn in a task, until Claude Code has nothing more to say for now.

        Returns the task, and the list that the turn's events are added to as they come.
        """
        events = []

        async def run():
            async for event in self.provider.run_turn(turn):
                events.append(event)

        task = asyncio.create_task(run())
        self.addAsyncCleanup(self.finish, task)
        await asyncio.wait_for(process.idle.wait(), 5)
        return task, events

    async def finish(self, task: asyncio.Task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def stop_reply(self, task: asyncio.Task):
        """Stop the reply as Stop does: cancel it and wait until it has ended."""
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)

    def background_turn(self, stop_output=None):
        """The recorded background turn, which waits after its second result while the essay worker runs."""
        answer, turn_output = recorded_turn('claude-background.jsonl')
        process = ReplayProcess(answer, turn_output, silent=True, stop_output=stop_output)
        return process, ultra_turn(self.directory, ReplayMachine(process)), turn_output

    def parallel_turn(self, reports_stops=True):
        """The recorded Chat turn until its first worker ends, while both of its workers run.

        Unless ``reports_stops``, Claude Code never says that it stopped them.
        """
        answer, turn_output = recorded_chat()
        first_end = turn_output.index(system_messages(turn_output, 'task_updated')[0])
        stop_output = {}
        if not reports_stops:
            for task_id in started_tasks(turn_output):
                stop_output[task_id] = []
        process = ReplayProcess(answer, turn_output[:first_end], silent=True, stop_output=stop_output)
        return process, ultra_turn(self.directory, ReplayMachine(process)), turn_output

    async def test_stopping_one_worker_asks_claude_code_to_stop_its_task(self):
        _, recorded_output = recorded_turn('claude-background.jsonl')
        essay, _ = system_messages(recorded_output, 'task_started')
        essay_stopped = background_essay_stopped(recorded_output)
        process, turn, _ = self.background_turn(stop_output={essay['task_id']: until_idle(essay_stopped)})
        task, events = await self.start(process, turn)
        self.assertEqual(latest_workers(events)[essay['task_id']].state, WORKER_RUNNING)

        self.assertTrue(await self.provider.stop_worker(turn, essay['task_id']))
        await asyncio.wait_for(task, 5)

        request = process.written[-1]
        self.assertEqual(request['type'], 'control_request')
        self.assertTrue(request['request_id'].startswith(claude_code.STOP_REQUEST_ID))
        self.assertEqual(request['request'], {'subtype': 'stop_task', 'task_id': essay['task_id']})
        # Claude Code reported the worker stopped, the orchestrator replied to
        # that, and Claude Code then reported its session idle, which ended the turn.
        latest = latest_workers(events)[essay['task_id']]
        self.assertEqual(latest.state, WORKER_STOPPED)
        self.assertEqual(latest.summary, 'The essay was stopped before it was finished.')
        self.assertTrue(reply(events).endswith(ESSAY_STOPPED_REPLY))
        self.assertEqual(failures(events), [])
        # Asked once, while the turn still read. Nothing was left to stop when it ended.
        self.assertEqual(process.calls, ['initialize', 'user', 'stop_task', 'close_stdin', 'kill'])
        # The turn has ended, so it has no workers to stop any more.
        self.assertFalse(await self.provider.stop_worker(turn, essay['task_id']))

    async def stop_last_worker(self, stop_output: list[dict]) -> None:
        """Stop the essay worker of the recorded background turn after both of its results, and check how it ends.

        ``stop_output`` is all Claude Code says once asked to stop it: no
        other turn, so no other result.
        """
        _, recorded_output = recorded_turn('claude-background.jsonl')
        essay, _ = system_messages(recorded_output, 'task_started')
        first, second = results(recorded_output)
        process, turn, _ = self.background_turn(stop_output={essay['task_id']: stop_output})
        task, events = await self.start(process, turn)

        self.assertTrue(await self.provider.stop_worker(turn, essay['task_id']))
        await asyncio.wait_for(task, 5)

        self.assertEqual(latest_workers(events)[essay['task_id']].state, WORKER_STOPPED)
        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        self.assertEqual(failures(events), [])
        self.assertEqual(process.calls, ['initialize', 'user', 'stop_task', 'close_stdin', 'kill'])

    async def test_stopping_the_last_worker_ends_the_reply_when_claude_code_reports_idle(self):
        essay = system_messages(recorded_turn('claude-background.jsonl')[1], 'task_started')[0]
        await self.stop_last_worker(until_idle([stopped_notification(essay['task_id'])]))

    async def test_stopping_the_last_worker_ends_the_reply_soon_even_if_claude_code_says_nothing_more(self):
        essay = system_messages(recorded_turn('claude-background.jsonl')[1], 'task_started')[0]
        with patch.object(claude_code, 'TURN_OVER_GRACE', 0.05):
            with self.assertLogs(claude_code.log, level='WARNING'):
                await self.stop_last_worker([stopped_notification(essay['task_id'])])

    async def test_only_a_running_worker_of_a_live_turn_can_be_stopped(self):
        process, turn, turn_output = self.background_turn()
        essay, math = system_messages(turn_output, 'task_started')
        await self.start(process, turn)

        # The math worker has reported already, and no worker has that id.
        self.assertFalse(await self.provider.stop_worker(turn, math['task_id']))
        self.assertFalse(await self.provider.stop_worker(turn, 'no-such-task'))
        # The turn is found by the very request that was passed to run_turn, not an equal copy.
        self.assertFalse(await self.provider.stop_worker(dataclasses.replace(turn), essay['task_id']))
        self.assertEqual(stop_requests(process), [])

    async def test_stopping_the_reply_stops_every_running_worker_before_the_cli_ends(self):
        process, turn, turn_output = self.parallel_turn()
        task, events = await self.start(process, turn)
        workers = latest_workers(events)
        self.assertEqual([worker.state for worker in workers.values()], [WORKER_RUNNING, WORKER_RUNNING])

        await self.stop_reply(task)

        self.assertEqual(stop_requests(process), started_tasks(turn_output))
        # Every request has an id of its own.
        request_ids = [message['request_id'] for message in process.written if message['type'] == 'control_request']
        self.assertEqual(len(set(request_ids)), len(request_ids))
        # Claude Code was asked to stop both, and reported them stopped, before stdin closed and the CLI was killed.
        self.assertEqual(process.calls, ['initialize', 'user', 'stop_task', 'stop_task', 'close_stdin', 'kill'])
        self.assertEqual(process.unread_lines(), 0)
        self.assertFalse(await self.provider.stop_worker(turn, started_tasks(turn_output)[0]))

    async def test_workers_that_do_not_report_stopping_in_time_end_with_the_cli(self):
        process, turn, _ = self.parallel_turn(reports_stops=False)
        task, _ = await self.start(process, turn)

        with patch.object(claude_code, 'STOP_WORKERS_GRACE', 0.05):
            with self.assertLogs(claude_code.log, level='WARNING'):
                await self.stop_reply(task)

        self.assertEqual(process.calls, ['initialize', 'user', 'stop_task', 'stop_task', 'close_stdin', 'kill'])

    async def test_stopping_again_while_workers_stop_ends_the_cli_at_once(self):
        process, turn, _ = self.parallel_turn(reports_stops=False)
        task, _ = await self.start(process, turn)

        task.cancel()
        # Buddy waits for Claude Code to report the workers stopped, and the person presses Stop again.
        await asyncio.wait_for(until(lambda: process.calls.count('stop_task') == 2), 5)
        await self.stop_reply(task)

        self.assertEqual(process.calls[-3:], ['stop_task', 'stop_task', 'kill'])
        self.assertTrue(process.killed)


def ultra_parser() -> claude_code.ClaudeStreamParser:
    parser = claude_code.ClaudeStreamParser()
    parser.track_workers(HAIKU_LOW)
    return parser


def task_message(subtype: str, **fields) -> dict:
    """A task message about a worker's task, unless ``fields`` say otherwise."""
    return {'type': 'system', 'subtype': subtype, 'task_id': 'task-1', 'task_type': 'local_agent', **fields}


def background_list(*tasks: dict) -> dict:
    return {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': list(tasks)}


SUCCESS_RESULT = {'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {}}


class WorkerStateTests(unittest.TestCase):
    def test_each_way_a_task_ends_sets_the_workers_state(self):
        endings = {
            'completed': WORKER_DONE,
            'stopped': WORKER_STOPPED,
            'killed': WORKER_STOPPED,
            'failed': WORKER_FAILED,
        }
        for status, state in endings.items():
            with self.subTest(status=status):
                parser = ultra_parser()
                parser.handle(task_message('task_started', description='Check the fix'))
                [update] = parser.handle(task_message('task_notification', status=status))
                self.assertEqual(update.state, state)
                self.assertFalse(parser.workers.running())

    def test_a_task_update_ends_a_worker_only_with_a_final_status(self):
        parser = ultra_parser()
        parser.handle(task_message('task_started', description='Check the fix'))
        self.assertEqual(parser.handle(task_message('task_updated', patch={'status': 'running'})), [])
        self.assertTrue(parser.workers.running())
        [update] = parser.handle(task_message('task_updated', patch={'status': 'failed', 'end_time': 1}))
        self.assertEqual(update.state, WORKER_FAILED)
        # The notification that follows adds the report without changing the state again.
        [update] = parser.handle(task_message('task_notification', status='failed', summary='Tests fail.'))
        self.assertEqual((update.state, update.summary), (WORKER_FAILED, 'Tests fail.'))

    def test_a_long_report_is_clipped(self):
        parser = ultra_parser()
        parser.handle(task_message('task_started', description='Write the essay'))
        report = 'Bridges.\n' * 1000
        [update] = parser.handle(task_message('task_notification', status='completed', summary=report))
        self.assertEqual(len(update.summary), claude_code.WORKER_SUMMARY_LIMIT)
        self.assertTrue(update.summary.startswith('Bridges.\nBridges.\n'))
        self.assertTrue(update.summary.endswith('…'))

    def test_shell_commands_and_housekeeping_are_not_workers(self):
        not_workers = {
            # A shell command that has run for two seconds, and one left running in the background.
            'command': {'task_type': 'local_bash', 'description': 'Run the tests', 'is_backgrounded': False},
            'background command': {
                'task_type': 'local_bash',
                'description': 'Start the dev server',
                'is_backgrounded': True,
            },
            # Claude Code's own housekeeping.
            'dream': {'task_type': 'dream', 'description': 'Consolidating memories'},
            'ambient': {'task_type': 'local_agent', 'description': 'Scanning', 'ambient': True},
            'kept out of the transcript': {'task_type': 'local_agent', 'description': 'Recap', 'skip_transcript': True},
        }
        for name, fields in not_workers.items():
            with self.subTest(name):
                parser = ultra_parser()
                self.assertEqual(parser.handle(task_message('task_started', **fields)), [])
                self.assertEqual(parser.handle(background_list({'task_id': 'task-1', **fields})), [])
                self.assertEqual(parser.handle(task_message('task_progress', description='Still going')), [])
                self.assertFalse(parser.workers.running())
                # It doesn't keep the reply open past its result.
                parser.handle(SUCCESS_RESULT)
                self.assertTrue(parser.waiting_for_turn_over())

    def test_a_worker_listed_in_the_background_is_shown_before_it_starts(self):
        parser = ultra_parser()
        [update] = parser.handle(
            background_list({'task_id': 'task-1', 'task_type': 'local_agent', 'description': 'Essay'})
        )
        self.assertEqual((update.state, update.label), (WORKER_RUNNING, 'Essay'))
        parser.handle(SUCCESS_RESULT)
        self.assertFalse(parser.waiting_for_turn_over())

    def test_workers_on_another_model_are_counted_in_the_usage(self):
        parser = ultra_parser()
        result = {
            **SUCCESS_RESULT,
            'usage': {'input_tokens': 4, 'output_tokens': 300, 'cache_read_input_tokens': 2000},
            'modelUsage': {
                'claude-opus-5-5': {'inputTokens': 4, 'outputTokens': 300, 'cacheReadInputTokens': 2000},
                'claude-haiku-5-5': {
                    'inputTokens': 6,
                    'outputTokens': 900,
                    'cacheReadInputTokens': 5000,
                    'cacheCreationInputTokens': 1000,
                },
            },
        }
        [usage] = parser.handle(result)
        self.assertEqual(usage, TokenUsage(input_tokens=8010, output_tokens=1200, cached_input_tokens=7000))

    def test_a_worker_without_a_description_is_named_by_its_type(self):
        parser = ultra_parser()
        [update] = parser.handle(task_message('task_started', subagent_type=ultra.READER_AGENT))
        self.assertEqual(update.label, ultra.READER_AGENT)

    def test_task_messages_of_a_normal_turn_report_no_workers(self):
        parser = claude_code.ClaudeStreamParser()
        self.assertEqual(parser.handle(task_message('task_started', description='Check the fix')), [])
        parser.handle({'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {}})
        self.assertTrue(parser.finished)


class UltraVersionTests(unittest.TestCase):
    def test_the_version_number_is_read_from_the_version_output(self):
        outputs = {
            '2.1.295 (Claude Code)\n': '2.1.295',
            'Claude Code 2.1.300': '2.1.300',
            'v2.2.0': '2.2.0',
            'unknown': '',
            '': '',
            None: '',
        }
        for output, version in outputs.items():
            with self.subTest(output=output):
                self.assertEqual(claude_code.cli_version(output), version)

    def test_ultra_needs_claude_code_2_1_295_or_newer(self):
        # Compared as numbers, so 2.1.1000 is newer and 2.1.29 older.
        for version in ('2.1.295 (Claude Code)', '2.1.296', '2.1.1000', '2.2.0', '2.10', '3.0.0'):
            with self.subTest(version=version):
                self.assertTrue(claude_code.version_runs_ultra(version))
        # A version that can't be read can't run it either.
        for version in ('2.1.294 (Claude Code)', '2.1.29', '2.0.999', '1.99.999', '2.1', 'unknown', '', None):
            with self.subTest(version=version):
                self.assertFalse(claude_code.version_runs_ultra(version))


class UltraModelListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.provider = claude_code.ClaudeCodeProvider(Path(directory.name))
        self.answer, _ = recorded_chat()

    async def list_models(self, process, **version):
        machine = VersionedMachine(process, **version)
        return await self.provider.list_models(ProviderSettings(enable=True), machine)

    async def test_a_recent_enough_claude_code_runs_ultra_with_every_model(self):
        models = await self.list_models(ReplayProcess(self.answer))
        self.assertEqual([model.key for model in models], ['opus', 'fable', 'sonnet', 'haiku'])
        self.assertTrue(all(model.ultra for model in models))

    async def test_an_older_or_unknown_claude_code_runs_ultra_with_no_model(self):
        for output in ('2.1.294 (Claude Code)', 'unknown', ''):
            with self.subTest(output=output):
                models = await self.list_models(ReplayProcess(self.answer), version_output=output)
                self.assertEqual(len(models), 4)
                self.assertFalse(any(model.ultra for model in models))

    async def test_a_version_that_cant_be_read_runs_ultra_with_no_model(self):
        failures = [
            # A failing CLI's message may name another version, such as the Node.js it needs.
            {'version_output': 'Claude Code needs Node.js 18.0.0 or newer.', 'version_code': 1},
            {'version_output': TimeoutError('claude did not finish within 30 seconds')},
            {'version_output': ProcessClosedError('the computer closed the process')},
        ]
        for failure in failures:
            with self.subTest(**failure):
                with self.assertLogs(claude_code.log, 'WARNING'):
                    models = await self.list_models(ReplayProcess(self.answer), **failure)
                self.assertEqual(len(models), 4)
                self.assertFalse(any(model.ultra for model in models))

    async def test_the_fallback_models_follow_the_version_too(self):
        # A Claude Code that never answers initialize leaves Buddy its fallback list.
        models = await self.list_models(ReplayProcess())
        self.assertEqual([model.key for model in models], ['opus', 'sonnet', 'haiku'])
        self.assertTrue(all(model.ultra for model in models))
        # The shared list keeps no computer's flag.
        self.assertFalse(any(model.ultra for model in claude_code.FALLBACK_MODELS))
        older = await self.list_models(ReplayProcess(), version_output='2.1.294 (Claude Code)')
        self.assertFalse(any(model.ultra for model in older))

    async def test_the_status_shows_the_version_number_that_ultra_is_judged_by(self):
        for output in (CLAUDE_CODE_VERSION, 'Claude Code 2.1.295'):
            with self.subTest(output=output):
                machine = VersionedMachine(version_output=output)
                status = await self.provider.status(ProviderSettings(enable=True), machine)
                self.assertEqual(status['version'], '2.1.295')


if __name__ == '__main__':
    unittest.main()
