"""Offline tests: Codex Ultra turns, replayed from a recorded codex app-server stream.

codex-p.jsonl under fixtures/ultra is what the app-server of Codex 0.153.4
wrote on stdout for an Ultra turn recorded on 2026-10-20: two workers at once,
on a persistent thread with a read-only sandbox. It answers initialize,
model/list, thread/start and turn/start, in that order, and then streams the
turn. The orchestrator's thread and each worker's own thread report under their
own thread ids. The notifications a turn doesn't read that carry the account's
rate limits or the computer's remote-control status were left out, and paths
name the user "fixture".

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_codex.py -v
"""

import asyncio
import dataclasses
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

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
from open_webui.utils.subscriptions.events import TextDelta, TurnFailed  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'ultra'
PROMPT = 'What is 17 times 23, and what are three prime numbers above 100?'
FOLDER_INSTRUCTIONS = 'Folder instructions: answer in plain words.'
PROJECT = 'C:\\code\\site'
PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgo='
# The chat's model in the recording. Its model list offers it every level up to 'ultra'.
CHAT_MODEL = 'gpt-6-astra'
# Another model in the recording's model list, which offers levels up to 'max' only.
LUNA = 'gpt-5.6-luna'
LUNA_LOW = UltraOptions(worker_model=LUNA, worker_label='GPT-5.6-Luna', worker_effort='low')
SAME_AS_CHAT = UltraOptions()
# The ids of the requests the recording answers.
RECORDED_IDS = {'initialize': 1, 'model/list': 2, 'thread/start': 3, 'turn/start': 4}


def recorded(name: str) -> list[dict]:
    """The messages of a recorded app-server stream, one per stdout line."""
    lines = (FIXTURES / name).read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines]


def recorded_answer(messages: list[dict], method: str) -> dict:
    """The recording's answer to its request for ``method``."""
    for message in messages:
        if message.get('id') == RECORDED_IDS[method] and 'method' not in message:
            return message
    raise AssertionError(f'The recording has no answer to {method}')


def final_answer(messages: list[dict], thread_id: str) -> str:
    """The last message of a thread's turn, as its recorded turn/completed lists it."""
    for message in messages:
        params = message.get('params') or {}
        if message.get('method') == 'turn/completed' and params.get('threadId') == thread_id:
            return params['turn']['items'][-1]['text']
    raise AssertionError(f'The recording has no completed turn on thread {thread_id}')


def worker_thread_ids(messages: list[dict]) -> list[str]:
    """The threads of the workers the recorded orchestrator started."""
    thread_ids = []
    for message in messages:
        item = (message.get('params') or {}).get('item') or {}
        if message.get('method') != 'item/started' or item.get('type') != 'subAgentActivity':
            continue
        if item['kind'] == 'started':
            thread_ids.append(item['agentThreadId'])
    return thread_ids


class ReplayProcess:
    """A ``codex app-server`` process that answers the way the recorded one did.

    initialize, model/list and thread/start get the recorded answers, under the
    ids of Buddy's own requests. Once the turn starts, it writes out everything
    the recording has after its thread/start answer, with the turn/start answer
    under Buddy's id. config/read, which the recording never sent, gets an empty
    config. Every request is kept in ``requests``.
    """

    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.requests = []
        self.returncode = None
        self._lines = asyncio.Queue()

    def _answer(self, request_id, result) -> None:
        self._lines.put_nowait(json.dumps({'id': request_id, 'result': result}))

    def _write_turn(self, request_id) -> None:
        start = self.messages.index(recorded_answer(self.messages, 'thread/start')) + 1
        for message in self.messages[start:]:
            if message.get('id') == RECORDED_IDS['turn/start'] and 'method' not in message:
                message = {**message, 'id': request_id}
            self._lines.put_nowait(json.dumps(message))

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

    Its model list is the recording's.
    """

    generation = 1
    running = True

    def __init__(self):
        self.requests = []
        self.queues = {}
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

    def unsubscribe(self, thread_id):
        self.queues.pop(thread_id, None)

    def sent(self, method: str) -> list[dict]:
        return [params for sent_method, params in self.requests if sent_method == method]


def chat_messages(earlier=(), prompt=PROMPT) -> list[dict]:
    return [{'role': 'system', 'content': FOLDER_INSTRUCTIONS}, *earlier, {'role': 'user', 'content': prompt}]


def ultra_turn(machine=None, access='read', options=LUNA_LOW, effort='low', earlier=(), prompt=PROMPT):
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


def text_input(text: str) -> dict:
    return {'type': 'text', 'text': text, 'text_elements': []}


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
                'agents.default_subagent_model': LUNA,
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
        orchestrator_thread = recorded_answer(messages, 'thread/start')['result']['thread']['id']
        text = reply(events)
        self.assertTrue(text.startswith("I\u2019ll start two sub-agents in parallel"))
        self.assertTrue(text.endswith(final_answer(messages, orchestrator_thread)))
        worker_threads = worker_thread_ids(messages)
        self.assertEqual(len(worker_threads), 2)
        for worker_thread in worker_threads:
            self.assertNotIn(final_answer(messages, worker_thread), text)
        self.assertEqual(failures(events), [])

    async def test_workers_are_on_at_every_access_level_with_that_levels_keys(self):
        options = UltraOptions(worker_model=LUNA, worker_label='GPT-5.6-Luna')
        worker_keys = {'features.multi_agent': True, 'agents.default_subagent_model': LUNA}
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

        # The recording's Luna offers levels up to 'max', and the chat's model up to 'ultra'.
        luna_ultra = UltraOptions(worker_model=LUNA, worker_label='GPT-5.6-Luna', worker_effort='ultra')
        params = await self.thread_start(ultra_turn(options=luna_ultra))
        self.assertEqual(worker_keys(params), {'agents.default_subagent_model': LUNA})

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
        task = dataclasses.replace(plain_turn(), is_task=True, ultra_options=LUNA_LOW)
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

    async def test_a_kept_thread_runs_only_turns_with_the_ultra_choice_it_started_with(self):
        def kept(thread_id, options):
            return codex.LiveThread(thread_id, FakeMachine.id, 1, 'read', PROJECT, None, ultra=options)

        # Even under the same key, neither kind of thread runs the other kind's turns.
        self.provider._keep_live_thread('chat', kept('ultra-thread', LUNA_LOW))
        self.assertIsNone(self.provider._take_live_thread('chat', plain_turn(), 1))
        self.provider._keep_live_thread('chat', kept('plain-thread', None))
        self.assertIsNone(self.provider._take_live_thread('chat', ultra_turn(), 1))
        # The workers' model and level were set when the thread started.
        self.provider._keep_live_thread('chat', kept('luna-thread', LUNA_LOW))
        self.assertIsNone(self.provider._take_live_thread('chat', ultra_turn(options=SAME_AS_CHAT), 1))
        # Let the release tasks send their requests.
        await asyncio.sleep(0)
        released = [params['threadId'] for params in self.server.sent('thread/unsubscribe')]
        self.assertEqual(released, ['ultra-thread', 'plain-thread', 'luna-thread'])

        same_choice = kept('matching', LUNA_LOW)
        self.provider._keep_live_thread('chat', same_choice)
        self.assertIs(self.provider._take_live_thread('chat', ultra_turn(), 1), same_choice)


if __name__ == '__main__':
    unittest.main()
