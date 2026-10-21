"""Offline tests: Claude Code Ultra turns, replayed from a recorded Claude Code stream.

fixtures/ultra/claude-chat.jsonl is what Claude Code 2.1.295 wrote on stdout for
an Ultra turn in Chat access that ran two workers at once, recorded on
2026-10-20. Its first line answers the initialize request that defined
Buddy's worker types; the rest is the turn. The answer's account details were
removed, and paths name the user "fixture".

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
from open_webui.utils.subscriptions.events import StatusUpdate, TextDelta, TurnFailed  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'ultra'
PROMPT = 'What is 17 times 23, and what are three prime numbers above 100?'
FOLDER_INSTRUCTIONS = 'Folder instructions: answer in plain words.'
HAIKU_LOW = UltraOptions(worker_model='haiku', worker_label='Claude Haiku', worker_effort='low')
SAME_AS_CHAT = UltraOptions()
TOO_OLD_STATUS = 'Ultra is off for this reply: Claude Code on Fixture PC is too old for Ultra.'
MISSING_SESSION = 'No conversation found with session ID: old-session'


def recorded(name: str) -> list[dict]:
    """The messages of a recorded Claude Code stream, one per stdout line."""
    lines = (FIXTURES / name).read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines]


def recorded_chat() -> tuple[dict, list[dict]]:
    """The recorded answer to the initialize request, and the turn that followed it."""
    messages = recorded('claude-chat.jsonl')
    return messages[0], messages[1:]


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


if __name__ == '__main__':
    unittest.main()
