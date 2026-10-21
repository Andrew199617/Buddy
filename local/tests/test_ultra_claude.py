"""Offline tests: Claude Code Ultra turns, replayed from recorded Claude Code streams.

The fixtures under fixtures/ultra are what Claude Code 2.1.295 wrote on stdout
for Ultra turns recorded on 2026-10-20. Each one's first line answers the
initialize request that defined Buddy's worker types; the rest is the turn:

- claude-chat.jsonl: two workers at once, in Chat access.
- claude-read.jsonl: a buddy-reader and a buddy-worker at once, in Read access.
- claude-depth.jsonl: a worker that starts a sub-worker, which can't start another.
- claude-background.jsonl: two background workers. The orchestrator replies
  while both run, and again when one reports; the recording ends while the
  other still runs.

The answers' account details were removed, and paths name the user "fixture".

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


class ReplayProcess:
    """A ``claude -p`` process that answers the way the recorded one did.

    It answers an initialize request with ``initialize_answer`` and only then
    writes out ``turn_output``, once the prompt is written. Without an answer,
    its output ends, or with ``silent`` it never says anything more.
    """

    def __init__(self, initialize_answer=None, turn_output=(), stderr='', silent=False):
        self.initialize_answer = initialize_answer
        self.turn_output = list(turn_output)
        self.stderr = stderr
        self.silent = silent
        self.written = []
        self.killed = False
        self.args = []
        self.extra_env = None
        self.temp_files = None
        self._lines = []

    async def write(self, text):
        message = json.loads(text)
        self.written.append(message)
        if message['type'] == 'control_request' and self.initialize_answer is not None:
            answer = copy.deepcopy(self.initialize_answer)
            answer['response']['request_id'] = message['request_id']
            self._lines.append(json.dumps(answer))
        elif message['type'] == 'user':
            self._lines.extend(json.dumps(line) for line in self.turn_output)

    async def read_line(self):
        if self._lines:
            return self._lines.pop(0)
        if self.silent:
            await asyncio.Event().wait()
        return None

    async def finish_output(self, timeout):
        return None

    def stderr_text(self):
        return self.stderr

    def close_stdin(self):
        pass

    def kill(self):
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


def background_essay_stopped(turn_output: list[dict]) -> list[dict]:
    """What Claude Code says once the background essay worker is stopped, ending with a third result.

    Not recorded: the recording ends while the essay worker still runs. This
    follows the shape of the recorded task_notification, background list and
    result messages.
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
        process = ReplayProcess(answer, turn_output)
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
        process = ReplayProcess(answer, turn_output)
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
        fresh = ReplayProcess(answer, turn_output)
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

    async def replay(self, name, access='chat', extra_output=(), silent=False):
        """Run an Ultra turn against a recorded stream; returns its events, the process, and the recorded turn."""
        answer, turn_output = recorded_turn(name)
        process = ReplayProcess(answer, [*turn_output, *extra_output], silent=silent)
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

        # Every result's text is in the reply, in order, and the turn ended at the third.
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
        # The reply's usage counts all three results, since only the last usage reported counts.
        usage = TokenUsage()
        for result in (first, second, third):
            usage = claude_code._usage_sum(usage, claude_code._usage_from_result(result['usage']))
        self.assertEqual(last_usage(events), usage)

    async def test_output_ending_while_a_worker_runs_stops_it_with_the_turn(self):
        # The recording ends while the essay worker runs, as if Claude Code had exited.
        events, process, turn_output = await self.replay('claude-background.jsonl')
        essay = system_messages(turn_output, 'task_started')[0]
        first, second = results(turn_output)

        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        latest = latest_workers(events)[essay['task_id']]
        self.assertEqual((latest.state, latest.summary), (WORKER_STOPPED, claude_code.WORKER_ENDED_WITH_TURN))
        self.assertEqual(len(failures(events)), 1)
        self.assertTrue(failures(events)[0].startswith('Claude Code stopped before finishing the reply.'))
        self.assertTrue(process.killed)

    async def test_an_ultra_turn_stops_with_its_workers_at_the_time_limit(self):
        with patch.object(claude_code, 'ULTRA_TIME_LIMIT', 0.2):
            with self.assertLogs(claude_code.log, level='WARNING'):
                events, process, turn_output = await self.replay('claude-background.jsonl', silent=True)
        essay = system_messages(turn_output, 'task_started')[0]
        first, second = results(turn_output)

        self.assertEqual(reply(events), f"{first['result']}\n\n{second['result']}")
        self.assertEqual(latest_workers(events)[essay['task_id']].state, WORKER_STOPPED)
        self.assertEqual(failures(events), [claude_code.ULTRA_TIME_LIMIT_MESSAGE])
        self.assertTrue(process.killed)

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
        # Nothing after the error was read.
        self.assertEqual(process.unread_lines(), len(output) - first_result - 1)

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
        self.assertIn('Starting a subagent: Compute 6 times 7', statuses(events))
        self.assertEqual(process.unread_lines(), len(turn_output) - first_result - 1)
        self.assertEqual(failures(events), [])
        self.assertTrue(process.killed)


def ultra_parser() -> claude_code.ClaudeStreamParser:
    parser = claude_code.ClaudeStreamParser()
    parser.track_workers(HAIKU_LOW)
    return parser


def task_message(subtype: str, **fields) -> dict:
    return {'type': 'system', 'subtype': subtype, 'task_id': 'task-1', **fields}


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

    def test_a_worker_without_a_description_is_named_by_its_type(self):
        parser = ultra_parser()
        [update] = parser.handle(task_message('task_started', subagent_type=ultra.READER_AGENT))
        self.assertEqual(update.label, ultra.READER_AGENT)

    def test_task_messages_of_a_normal_turn_report_no_workers(self):
        parser = claude_code.ClaudeStreamParser()
        self.assertEqual(parser.handle(task_message('task_started', description='Check the fix')), [])
        parser.handle({'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {}})
        self.assertTrue(parser.finished)


if __name__ == '__main__':
    unittest.main()
