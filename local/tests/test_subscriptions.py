"""Offline tests for Claude and ChatGPT subscription models; no CLI sign-in or API traffic.

Run with the repository virtualenv:
    .venv\\Scripts\\python.exe -m unittest discover -s local/tests -p test_subscriptions.py -v
"""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from open_webui.utils.subscriptions import claude_code, codex, discovery, runner, streaming  # noqa: E402
from open_webui.utils.subscriptions.common import (  # noqa: E402
    ProviderSettings,
    TurnRequest,
    requested_effort,
)
from open_webui.utils.subscriptions.conversation import (  # noqa: E402
    CONTINUE_PROMPT,
    ChatTurn,
    SessionStore,
    conversation_key,
    parse_messages,
)
from open_webui.utils.subscriptions.events import (  # noqa: E402
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
)
from open_webui.utils.subscriptions.machines import LocalMachine, RemoteMachine, temp_file_arg  # noqa: E402
from open_webui.utils.subscriptions.process import ChildProcess, subscription_env  # noqa: E402

PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgo='


def stream_event(event: dict, parent: str | None = None) -> dict:
    return {'type': 'stream_event', 'event': event, 'parent_tool_use_id': parent}


async def collect(async_iterable) -> list:
    return [item async for item in async_iterable]


class ConversationTests(unittest.TestCase):
    def test_splits_system_history_and_prompt(self):
        conversation = parse_messages(
            [
                {'role': 'system', 'content': 'Be brief.'},
                {'role': 'user', 'content': 'Hello'},
                {'role': 'assistant', 'content': '<details type="reasoning">thinking</details>Hi there'},
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': 'What is this?'},
                        {'type': 'image_url', 'image_url': {'url': PNG_DATA_URL}},
                    ],
                },
            ]
        )
        self.assertEqual(conversation.system, 'Be brief.')
        self.assertEqual([turn.role for turn in conversation.history], ['user', 'assistant'])
        self.assertEqual(conversation.history[1].text, 'Hi there')
        self.assertEqual(conversation.prompt.text, 'What is this?')
        self.assertEqual(conversation.prompt.images, [PNG_DATA_URL])

    def test_continue_response_adds_a_continue_prompt(self):
        conversation = parse_messages(
            [{'role': 'user', 'content': 'Write a poem'}, {'role': 'assistant', 'content': 'Roses are'}]
        )
        self.assertEqual(conversation.prompt.text, CONTINUE_PROMPT)
        self.assertEqual(len(conversation.history), 2)

    def test_empty_chat_is_an_error(self):
        with self.assertRaises(SubscriptionError):
            parse_messages([{'role': 'system', 'content': 'Only instructions'}])

    def test_key_ignores_whitespace_and_details_but_not_content(self):
        first = [ChatTurn('user', 'Hello  there'), ChatTurn('assistant', 'Hi\n\nfriend')]
        reformatted = [
            ChatTurn('user', 'Hello there'),
            ChatTurn('assistant', '<details type="tool_calls">x</details>Hi friend'),
        ]
        edited = [ChatTurn('user', 'Hello there'), ChatTurn('assistant', 'Hi enemy')]
        self.assertEqual(conversation_key(first), conversation_key(reformatted))
        self.assertNotEqual(conversation_key(first), conversation_key(edited))
        self.assertNotEqual(conversation_key(first), conversation_key(first, system='Be brief.'))

    def test_key_changes_when_an_image_is_replaced(self):
        original = [ChatTurn('user', 'What is this?', [PNG_DATA_URL])]
        replaced = [ChatTurn('user', 'What is this?', ['data:image/png;base64,R0lGODlh'])]
        self.assertNotEqual(conversation_key(original), conversation_key(replaced))

    def test_tool_calls_and_results_become_turns(self):
        conversation = parse_messages(
            [
                {'role': 'user', 'content': 'Weather in Paris?'},
                {
                    'role': 'assistant',
                    'content': '',
                    'tool_calls': [
                        {'id': 'call-1', 'function': {'name': 'get_weather', 'arguments': '{"city": "Paris"}'}}
                    ],
                },
                {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'Sunny, 21 C'},
            ]
        )
        self.assertEqual(conversation.history[1].text, '[Called tool get_weather with {"city": "Paris"}]')
        self.assertEqual(conversation.prompt.role, 'user')
        self.assertEqual(conversation.prompt.text, '[Result from tool get_weather]\nSunny, 21 C')

    def test_session_store_persists_and_drops_oldest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sessions.json'
            store = SessionStore(path, limit=2)
            store.put('a', 'session-a')
            store.put('b', 'session-b')
            store.put('c', 'session-c')
            reloaded = SessionStore(path, limit=2)
            self.assertIsNone(reloaded.get('a'))
            self.assertEqual(reloaded.get('c'), 'session-c')


class CommonTests(unittest.TestCase):
    def test_requested_effort_reads_chip_and_responses_format(self):
        self.assertEqual(requested_effort({'reasoning_effort': 'High'}), 'high')
        self.assertEqual(requested_effort({'reasoning': {'effort': 'low'}}), 'low')
        self.assertIsNone(requested_effort({'reasoning_effort': ''}))


class ClaudeStreamTests(unittest.TestCase):
    def test_streams_text_thinking_and_tool_activity(self):
        parser = claude_code.ClaudeStreamParser()
        messages = [
            {'type': 'system', 'subtype': 'init', 'session_id': 'first-id'},
            stream_event({'type': 'message_start'}),
            stream_event({'type': 'content_block_start', 'content_block': {'type': 'thinking'}}),
            stream_event({'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': 'Plan'}}),
            stream_event({'type': 'content_block_start', 'content_block': {'type': 'text'}}),
            stream_event({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Checking.'}}),
            {
                'type': 'assistant',
                'message': {
                    'content': [
                        {'type': 'text', 'text': 'Checking.'},
                        {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'git status'}},
                    ]
                },
            },
            stream_event({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'sub'}}, 'tool-1'),
            stream_event({'type': 'message_start'}),
            stream_event({'type': 'content_block_start', 'content_block': {'type': 'text'}}),
            stream_event({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Clean tree.'}}),
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Clean tree.'}]}},
            {
                'type': 'result',
                'subtype': 'success',
                'is_error': False,
                'session_id': 'forked-id',
                'usage': {
                    'input_tokens': 10,
                    'cache_read_input_tokens': 90,
                    'cache_creation_input_tokens': 5,
                    'output_tokens': 7,
                },
            },
        ]
        events = []
        for message in messages:
            events.extend(parser.handle(message))

        text = ''.join(event.text for event in events if isinstance(event, TextDelta))
        self.assertEqual(text, 'Checking.\n\nClean tree.')
        self.assertEqual(parser.reply_text, text)
        self.assertIn(StatusUpdate('Running `git status`'), events)
        self.assertTrue(any(isinstance(event, ReasoningDelta) and 'Plan' in event.text for event in events))
        self.assertEqual(parser.session_id, 'forked-id')
        self.assertTrue(parser.finished)
        self.assertIsNone(parser.error)
        self.assertIn(TokenUsage(input_tokens=105, output_tokens=7, cached_input_tokens=90), events)

    def test_uses_full_text_when_partial_messages_are_missing(self):
        parser = claude_code.ClaudeStreamParser()
        events = parser.handle({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Hello'}]}})
        self.assertEqual(events, [TextDelta('Hello')])

    def test_not_signed_in_becomes_a_clear_error(self):
        parser = claude_code.ClaudeStreamParser()
        parser.handle(
            {
                'type': 'assistant',
                'error': 'authentication_failed',
                'message': {'content': [{'type': 'text', 'text': 'Not logged in · Please run /login'}]},
            }
        )
        events = parser.handle(
            {'type': 'result', 'subtype': 'success', 'is_error': True, 'result': 'Not logged in', 'usage': {}}
        )
        self.assertEqual(events, [])
        self.assertEqual(parser.error, claude_code.NOT_SIGNED_IN_MESSAGE)

    def test_models_from_initialize(self):
        entries = [
            {'value': 'default', 'displayName': 'Default', 'description': 'Use the default'},
            {
                'value': 'opus',
                'displayName': 'Opus',
                'description': 'Opus 5.5 · Best for everyday, complex tasks · $4/$20 per Mtok',
                'supportedEffortLevels': ['low', 'high'],
            },
            {'value': 'claude-fable-5-1[1m]', 'displayName': 'Fable', 'description': 'Fable 5.1 · Hardest tasks'},
        ]
        models = claude_code.models_from_initialize(entries)
        self.assertEqual([model.key for model in models], ['opus', 'fable'])
        self.assertEqual(models[0].name, 'Claude Opus 5.5')
        self.assertEqual(models[0].description, 'Best for everyday, complex tasks')
        self.assertEqual(models[0].efforts, ['low', 'high'])
        self.assertEqual(models[1].value, 'claude-fable-5-1[1m]')

    def test_turn_arguments_follow_access_level(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = claude_code.ClaudeCodeProvider(Path(directory))
            conversation = parse_messages([{'role': 'user', 'content': 'hi'}])

            def turn_args(access, resume_id=None, **turn_options):
                turn = TurnRequest(
                    model='opus',
                    conversation=conversation,
                    settings=ProviderSettings(enable=True, access=access),
                    cwd=directory,
                    **turn_options,
                )
                return provider._turn_args('claude', turn, resume_id, 'instructions.md')

            chat_args = turn_args('chat', effort='none')
            self.assertIn('--system-prompt-file', chat_args)
            self.assertEqual(chat_args[chat_args.index('--tools') + 1], '')
            self.assertEqual(chat_args[chat_args.index('--effort') + 1], 'low')
            self.assertNotIn('--dangerously-skip-permissions', chat_args)

            read_args = turn_args('read')
            self.assertEqual(read_args[read_args.index('--tools') + 1], claude_code.READ_ONLY_TOOLS)
            self.assertIn('--append-system-prompt-file', read_args)
            # A repository's hooks, MCP servers, and skills must not load in read mode.
            self.assertIn('--restricted', read_args)
            self.assertIn('--strict-mcp-config', read_args)
            self.assertEqual(read_args[read_args.index('--setting-sources') + 1], '')

            full_args = turn_args('full', effort='max')
            self.assertIn('--dangerously-skip-permissions', full_args)
            self.assertNotIn('--tools', full_args)

            task_args = turn_args('full', is_task=True)
            self.assertIn('--no-session-persistence', task_args)
            self.assertEqual(task_args[task_args.index('--tools') + 1], '')

            resume_args = turn_args('chat', resume_id='session-1')
            resume_index = resume_args.index('--resume')
            self.assertEqual(resume_args[resume_index + 1 : resume_index + 3], ['session-1', '--fork-session'])

    def test_rebuilt_session_resends_earlier_images(self):
        history = [ChatTurn('user', 'Remember this chart', [PNG_DATA_URL]), ChatTurn('assistant', 'Got it.')]
        message = claude_code.transcript_message(history, ChatTurn('user', 'What did the chart show?'))
        content = message['message']['content']
        texts = [block['text'] for block in content if block['type'] == 'text']
        images = [block for block in content if block['type'] == 'image']
        self.assertIn('<user>\nRemember this chart', texts)
        self.assertEqual(len(images), 1)
        self.assertIn('My latest message:\nWhat did the chart show?', texts[-1])
        # The image sits inside the turn it was attached to.
        self.assertEqual(content[content.index(images[0]) + 1], {'type': 'text', 'text': '</user>'})
        first_turn_only = claude_code.transcript_message([], ChatTurn('user', 'Hi'))
        self.assertEqual(first_turn_only['message']['content'], [{'type': 'text', 'text': 'Hi'}])

    def test_user_message_embeds_images(self):
        message = claude_code.user_message('Look', [PNG_DATA_URL, 'https://example.invalid/a.png', 'blob:x'])
        content = message['message']['content']
        self.assertEqual(content[0], {'type': 'text', 'text': 'Look'})
        self.assertEqual(content[1]['source'], {'type': 'base64', 'media_type': 'image/png', 'data': 'iVBORw0KGgo='})
        self.assertEqual(content[2]['source']['type'], 'url')
        self.assertEqual(len(content), 3)

    def test_missing_session_falls_back_to_chat_text(self):
        """A resumed session that no longer exists is retried as a fresh session."""
        missing_session_result = {
            'type': 'result',
            'subtype': 'error_during_execution',
            'is_error': True,
            'session_id': 'unused',
            'usage': {},
        }
        fresh_run = [
            {'type': 'system', 'subtype': 'init', 'session_id': 'fresh-session'},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'You are Ada.'}]}},
            {'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'fresh-session', 'usage': {}},
        ]
        scripted_runs = [
            ([missing_session_result], 'No conversation found with session ID: old-session'),
            (fresh_run, ''),
        ]
        started = []

        class FakeProcess:
            def __init__(self, args):
                messages, stderr = scripted_runs[len(started)]
                started.append(self)
                self.args = args
                self.written = ''
                self._lines = [json.dumps(message) for message in messages]
                self._stderr = stderr

            async def write(self, text):
                self.written += text

            async def read_line(self):
                if self._lines:
                    return self._lines.pop(0)
                return None

            async def finish_output(self, timeout):
                return None

            def stderr_text(self):
                return self._stderr

            def close_stdin(self):
                pass

            def kill(self):
                pass

        class FakeMachine:
            id = 'fake'
            name = 'Fake machine'

            async def find_tool(self, tool, configured_path=''):
                return 'claude'

            async def start_process(self, args, cwd, extra_env=None, temp_files=None):
                return FakeProcess(args)

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                provider = claude_code.ClaudeCodeProvider(Path(directory))
                conversation = parse_messages(
                    [
                        {'role': 'user', 'content': 'My name is Ada'},
                        {'role': 'assistant', 'content': 'Hi Ada'},
                        {'role': 'user', 'content': 'Who am I?'},
                    ]
                )
                turn = TurnRequest(
                    'opus', conversation, ProviderSettings(enable=True), directory, machine=FakeMachine()
                )
                provider._sessions.put(provider._session_key(turn, conversation.history), 'old-session')
                events = await collect(provider.run_turn(turn))
                answered = conversation.history + [conversation.prompt, ChatTurn('assistant', 'You are Ada.')]
                return events, provider._sessions.get(provider._session_key(turn, answered))

        events, stored_session = asyncio.run(run())
        self.assertEqual(events, [TextDelta('You are Ada.')])
        self.assertIn('--resume', started[0].args)
        self.assertNotIn('--resume', started[1].args)
        self.assertIn('My name is Ada', started[1].written)
        self.assertEqual(stored_session, 'fresh-session')

    def test_rate_limit_events_become_usage_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = claude_code.ClaudeCodeProvider(Path(directory))
            self.assertIsNone(provider.usage_summary())
            provider._record_rate_limit(
                {'status': 'allowed', 'rateLimitType': 'five_hour', 'utilization': 0.42, 'resetsAt': 1_800_000_000_000}
            )
            provider._record_rate_limit({'status': 'rejected', 'rateLimitType': 'seven_day'})
            summary = provider.usage_summary()
        self.assertEqual(summary['windows'], [{'label': '5-hour', 'used_percent': 42, 'resets_at': 1_800_000_000}])
        self.assertTrue(summary['limit_reached'])

    def test_login_code_uses_only_the_first_line(self):
        class FakeProcess:
            def __init__(self):
                self.written = ''

            async def write(self, text):
                self.written += text

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                provider = claude_code.ClaudeCodeProvider(Path(directory))
                process = FakeProcess()
                login = claude_code.ClaudeLogin(process)
                login.watcher = asyncio.get_running_loop().create_future()
                login.watcher.set_result(None)
                provider._login = login
                await provider.submit_login_code('  abc#state  \nextra\n')
                return process.written

        self.assertEqual(asyncio.run(run()), 'abc#state\n')


class CodexTests(unittest.TestCase):
    def test_turn_parser_streams_messages_activity_and_usage(self):
        parser = codex.CodexTurnParser('turn-1')

        def notify(method, **params):
            return parser.handle({'method': method, 'params': {'threadId': 't', 'turnId': 'turn-1', **params}})

        events = []
        events += notify('item/started', item={'type': 'agentMessage', 'id': 'm1'})
        events += notify('item/agentMessage/delta', itemId='m1', delta='Looking.')
        events += notify('item/started', item={'type': 'commandExecution', 'command': 'ls -la'})
        events += notify('item/completed', item={'type': 'commandExecution', 'exitCode': 2})
        events += notify('item/started', item={'type': 'agentMessage', 'id': 'm2'})
        events += notify('item/agentMessage/delta', itemId='m2', delta='Done.')
        events += notify('item/completed', item={'type': 'agentMessage', 'id': 'm2', 'text': 'Done.'})
        events += parser.handle({'method': 'item/agentMessage/delta', 'params': {'turnId': 'other', 'delta': 'x'}})
        notify('thread/tokenUsage/updated', tokenUsage={'total': {'inputTokens': 120, 'outputTokens': 9}})
        notify('turn/completed', turn={'id': 'turn-1', 'status': 'completed'})

        self.assertEqual(parser.reply_text, 'Looking.\n\nDone.')
        self.assertIn(StatusUpdate('Running `ls -la`'), events)
        self.assertTrue(any(isinstance(event, ReasoningDelta) and 'exit code 2' in event.text for event in events))
        self.assertTrue(parser.finished)
        self.assertIsNone(parser.error)
        usage = codex._usage_difference(parser.usage_total, {'inputTokens': 100, 'outputTokens': 4})
        self.assertEqual(usage, TokenUsage(input_tokens=20, output_tokens=5))

    def test_failed_turn_reports_the_api_message(self):
        parser = codex.CodexTurnParser('turn-1')
        api_error = json.dumps({'error': {'message': "The 'x' model is not supported."}})
        error_params = {'turnId': 'turn-1', 'willRetry': False, 'error': {'message': api_error}}
        parser.handle({'method': 'error', 'params': error_params})
        parser.handle({'method': 'turn/completed', 'params': {'turn': {'id': 'turn-1', 'status': 'failed'}}})
        self.assertEqual(parser.error, "The 'x' model is not supported.")

    def test_retry_errors_are_only_status(self):
        parser = codex.CodexTurnParser('turn-1')
        events = parser.handle(
            {'method': 'error', 'params': {'turnId': 'turn-1', 'willRetry': True, 'error': {'message': 'busy'}}}
        )
        self.assertEqual(events, [StatusUpdate('Retrying: busy')])
        self.assertIsNone(parser.error)

    def test_history_items_and_input(self):
        items = codex.history_items([ChatTurn('user', 'Hi', [PNG_DATA_URL]), ChatTurn('assistant', 'Hello')])
        self.assertEqual(items[0]['content'][1], {'type': 'input_image', 'image_url': PNG_DATA_URL})
        assistant_content = [{'type': 'output_text', 'text': 'Hello'}]
        self.assertEqual(items[1], {'type': 'message', 'role': 'assistant', 'content': assistant_content})
        inputs = codex.user_input(ChatTurn('user', 'See', [PNG_DATA_URL]))
        self.assertEqual(inputs[1], {'type': 'image', 'url': PNG_DATA_URL})

    def test_thread_config_limits_chat_and_read_threads(self):
        class FakeServer:
            def __init__(self):
                self.methods = []

            async def request(self, method, params=None, timeout=None):
                self.methods.append(method)
                return {'config': {'mcp_servers': {'node_repl': {'command': 'node'}}}}

        provider = codex.CodexProvider()
        conversation = parse_messages([{'role': 'user', 'content': 'hi'}])
        server = FakeServer()

        def thread_config(access):
            turn = TurnRequest('gpt-x', conversation, ProviderSettings(access=access), '.')
            return asyncio.run(provider._thread_config(server, turn))

        chat_config = thread_config('chat')
        self.assertEqual(chat_config['mcp_servers'], {'node_repl': {'enabled': False}})
        self.assertFalse(chat_config['features.shell_tool'])
        self.assertFalse(chat_config['features.unified_exec'])

        read_config = thread_config('read')
        self.assertEqual(read_config, {'mcp_servers': {'node_repl': {'enabled': False}}})

        self.assertIsNone(thread_config('full'))
        # Chat and read threads read the effective config; full threads skip it.
        self.assertEqual(server.methods, ['config/read', 'config/read'])

    def test_hooks_are_a_disabled_feature(self):
        # Hooks run commands outside the read-only sandbox.
        self.assertIn('hooks', codex.DISABLED_FEATURES)

    def test_dropped_live_threads_are_released(self):
        class FakeServer:
            running = True
            generation = 2

            def __init__(self):
                self.requests = []

            async def request(self, method, params=None, timeout=None):
                self.requests.append((method, params))
                return {}

        provider = codex.CodexProvider()
        server = FakeServer()
        provider._servers['local'] = server
        conversation = parse_messages([{'role': 'user', 'content': 'hi'}])
        chat_turn = TurnRequest('gpt-x', conversation, ProviderSettings(access='chat'), '.')

        def read_thread(thread_id, generation=2):
            return codex.LiveThread(thread_id, 'local', generation, 'read', '.', None)

        async def run():
            # The chat's access level changed, so its kept thread is dropped.
            provider._keep_live_thread('chat-1', read_thread('changed-access'))
            changed_access = provider._take_live_thread('chat-1', chat_turn, server.generation)
            # A newer thread for the same chat replaces the kept one.
            provider._keep_live_thread('chat-2', read_thread('replaced'))
            provider._keep_live_thread('chat-2', read_thread('replacement'))
            # The app-server restarted since this thread was kept, so it is already gone.
            provider._keep_live_thread('chat-3', read_thread('restarted', generation=1))
            restarted = provider._take_live_thread('chat-3', chat_turn, server.generation)
            # A thread whose access, folder and app-server still match is reused, not released.
            provider._keep_live_thread('chat-4', codex.LiveThread('matching', 'local', 2, 'chat', '.', None))
            matching = provider._take_live_thread('chat-4', chat_turn, server.generation)
            # Let the release tasks send their requests.
            await asyncio.sleep(0)
            return changed_access, restarted, matching

        changed_access, restarted, matching = asyncio.run(run())
        self.assertIsNone(changed_access)
        self.assertIsNone(restarted)
        self.assertEqual(matching.thread_id, 'matching')
        self.assertEqual(provider._live_threads['chat-2'].thread_id, 'replacement')
        self.assertEqual(
            server.requests,
            [
                ('thread/unsubscribe', {'threadId': 'changed-access'}),
                ('thread/unsubscribe', {'threadId': 'replaced'}),
            ],
        )

    def test_signing_out_releases_the_machines_threads(self):
        class FakeServer:
            running = True
            generation = 1

            def __init__(self):
                self.requests = []

            async def request(self, method, params=None, timeout=None):
                self.requests.append((method, params))
                return {}

        provider = codex.CodexProvider()
        server = FakeServer()
        provider._servers['local'] = server
        provider._keep_live_thread('chat-1', codex.LiveThread('on-local', 'local', 1, 'chat', '.', None))
        provider._keep_live_thread('chat-2', codex.LiveThread('on-runner', 'office-pc', 1, 'chat', '.', None))

        async def run():
            provider._release_machine_threads('local')
            # Let the release task send its request.
            await asyncio.sleep(0)

        asyncio.run(run())
        self.assertEqual(server.requests, [('thread/unsubscribe', {'threadId': 'on-local'})])
        self.assertEqual(list(provider._live_threads), ['chat-2'])

    def test_summarize_rate_limits(self):
        summary = codex.summarize_rate_limits(
            {
                'planType': 'plus',
                'primary': {'usedPercent': 40, 'windowDurationMins': 300, 'resetsAt': 10},
                'secondary': {'usedPercent': 85, 'windowDurationMins': 10080, 'resetsAt': 20},
            }
        )
        self.assertEqual([window['label'] for window in summary['windows']], ['5-hour', 'Weekly'])
        self.assertEqual(summary['plan'], 'plus')
        self.assertIsNone(codex.summarize_rate_limits(None))

    def test_effort_uses_model_levels(self):
        provider = codex.CodexProvider()
        provider._model_efforts['gpt-x'] = ['low', 'medium', 'high']
        conversation = parse_messages([{'role': 'user', 'content': 'hi'}])

        def effort(requested, is_task=False):
            turn = TurnRequest('gpt-x', conversation, ProviderSettings(), '.', effort=requested, is_task=is_task)
            return provider._effort(turn)

        self.assertEqual(effort('high'), 'high')
        self.assertEqual(effort('none'), 'low')
        self.assertIsNone(effort('max'))
        self.assertEqual(effort('high', is_task=True), 'low')


class DiscoveryTests(unittest.TestCase):
    def test_finds_newest_desktop_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = Path(directory) / 'Roaming'
            local_appdata = Path(directory) / 'Local'
            # The MSIX app's real files live under Packages; %APPDATA% is virtualized.
            package_bundles = local_appdata / 'Packages' / 'Claude_abc123' / 'LocalCache' / 'Roaming' / 'Claude'
            installs = [(appdata / 'Claude', '2.1.9'), (package_bundles, '2.1.295'), (appdata / 'Claude', '2.1.30')]
            for root, version in installs:
                binary = root / 'claude-code' / version / 'abc' / 'claude.exe'
                binary.parent.mkdir(parents=True)
                binary.write_text('')
            with patch.dict(os.environ, {'APPDATA': str(appdata), 'LOCALAPPDATA': str(local_appdata)}):
                newest = discovery._newest_desktop_bundle()
            self.assertIn('Claude_abc123', newest)
            self.assertIn('2.1.295', newest)


class ProcessTests(unittest.TestCase):
    def test_env_drops_session_and_api_key_variables(self):
        variables = {
            'CLAUDECODE': '1',
            'CLAUDE_CODE_ENTRYPOINT': 'desktop',
            'CLAUDE_CODE_GIT_BASH_PATH': r'C:\Git\bin\bash.exe',
            'ANTHROPIC_API_KEY': 'test-key',
            'OPENAI_API_KEY': 'test-key',
            'PATH_FOR_TEST': 'kept',
        }
        with patch.dict(os.environ, variables):
            env = subscription_env({'BROWSER': 'none'})
        self.assertNotIn('CLAUDECODE', env)
        self.assertNotIn('CLAUDE_CODE_ENTRYPOINT', env)
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertEqual(env['CLAUDE_CODE_GIT_BASH_PATH'], r'C:\Git\bin\bash.exe')
        self.assertEqual(env['PATH_FOR_TEST'], 'kept')
        self.assertEqual(env['BROWSER'], 'none')

    def test_child_process_reads_lines_and_stops(self):
        script = 'import sys\nfor line in sys.stdin:\n    print("echo:" + line.strip(), flush=True)\n'

        async def run():
            process = ChildProcess([sys.executable, '-c', script], os.getcwd(), dict(os.environ))
            await process.write('first\n')
            first = await asyncio.wait_for(process.read_line(), 10)
            await process.write('second\n')
            second = await asyncio.wait_for(process.read_line(), 10)
            process.close_stdin()
            end = await asyncio.wait_for(process.read_line(), 10)
            await process.wait(10)
            process.kill()
            return first, second, end

        self.assertEqual(asyncio.run(run()), ('echo:first', 'echo:second', None))


class MachineTests(unittest.TestCase):
    def test_local_machine_substitutes_temp_files(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                machine = LocalMachine(Path(directory))
                process = await machine.start_process(
                    [sys.executable, '-c', 'import sys; print(open(sys.argv[1]).read())', temp_file_arg('notes')],
                    await machine.chat_dir(),
                    temp_files={'notes': 'hi'},
                )
                line = await asyncio.wait_for(process.read_line(), 20)
                await process.wait(20)
                temp_path = process.args[-1]
                process.kill()
                return line, os.path.exists(temp_path)

        line, still_there = asyncio.run(run())
        self.assertEqual(line, 'hi')
        self.assertFalse(still_there)


class RunnerTests(unittest.TestCase):
    """A real runner served in-process, driven through RemoteMachine."""

    def run_with_runner(self, scenario, key='test-key', client_key=None):
        from aiohttp.test_utils import TestServer

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                server = TestServer(runner.build_app(key, Path(directory)))
                await server.start_server()
                machine = RemoteMachine('pc', 'Test PC', str(server.make_url('')), client_key or key)
                try:
                    return await scenario(machine, Path(directory))
                finally:
                    await machine.close()
                    await server.close()

        return asyncio.run(run())

    def test_health_and_short_commands(self):
        async def scenario(machine, state_dir):
            info = await machine.info()
            returncode, stdout, _ = await machine.run(
                [sys.executable, '-c', 'print("hello from runner")'], await machine.chat_dir(), 30
            )
            exists = await machine.path_exists(str(state_dir), 'dir')
            missing = await machine.path_exists(str(state_dir / 'nope.txt'), 'file')
            return info, returncode, stdout, exists, missing

        info, returncode, stdout, exists, missing = self.run_with_runner(scenario)
        self.assertTrue(info['ok'])
        self.assertTrue(info['chat_dir'].endswith('chat'))
        self.assertEqual((returncode, stdout.strip()), (0, 'hello from runner'))
        self.assertTrue(exists)
        self.assertFalse(missing)

    def test_streams_a_process_over_pipes(self):
        script = (
            'import sys\n'
            'print("instructions:" + open(sys.argv[1], encoding="utf-8").read(), flush=True)\n'
            'for line in sys.stdin:\n'
            '    print("echo:" + line.strip(), flush=True)\n'
        )

        async def scenario(machine, state_dir):
            process = await machine.start_process(
                [sys.executable, '-c', script, temp_file_arg('instructions')],
                await machine.chat_dir(),
                temp_files={'instructions': 'be brief'},
            )
            first = await asyncio.wait_for(process.read_line(), 20)
            # JSON with escapes and a long line must pass through unchanged.
            payload = json.dumps({'text': 'quote " slash \\ newline \\n', 'pad': 'x' * 10000})
            await process.write(payload + '\n')
            second = await asyncio.wait_for(process.read_line(), 20)
            process.close_stdin()
            end = await asyncio.wait_for(process.read_line(), 20)
            code = await process.wait(20)
            leftover_temp_files = list((state_dir / 'tmp').iterdir())
            return first, second, payload, end, code, leftover_temp_files

        first, second, payload, end, code, leftover = self.run_with_runner(scenario)
        self.assertEqual(first, 'instructions:be brief')
        self.assertEqual(second, 'echo:' + payload)
        self.assertIsNone(end)
        self.assertEqual(code, 0)
        self.assertEqual(leftover, [])

    def test_wrong_key_is_rejected(self):
        async def scenario(machine, state_dir):
            with self.assertRaises(SubscriptionError) as raised:
                await machine.info()
            return str(raised.exception)

        message = self.run_with_runner(scenario, client_key='wrong-key')
        self.assertIn('runner key', message)


class StreamingTests(unittest.TestCase):
    def test_stream_events_produce_openai_chunks(self):
        async def events():
            yield StatusUpdate('Running `ls`')
            yield ReasoningDelta('thinking')
            yield TextDelta('Hello')
            yield TokenUsage(input_tokens=3, output_tokens=2, cached_input_tokens=1)
            yield TurnFailed('Limit reached')

        lines = asyncio.run(collect(streaming.stream_events(events(), 'codex.gpt-x')))
        payloads = [json.loads(line[6:]) for line in lines if line.startswith('data: {')]
        self.assertEqual(payloads[0]['event']['data']['description'], 'Running `ls`')
        self.assertEqual(payloads[1]['choices'][0]['delta'], {'reasoning_content': 'thinking'})
        self.assertEqual(payloads[2]['choices'][0]['delta'], {'content': 'Hello'})
        self.assertEqual(payloads[3]['usage']['prompt_tokens'], 3)
        self.assertEqual(payloads[3]['choices'], [])
        self.assertEqual(payloads[4], {'error': {'message': 'Limit reached'}})
        self.assertTrue(payloads[5]['event']['data']['hidden'])
        self.assertEqual(payloads[6]['choices'][0]['finish_reason'], 'stop')
        self.assertEqual(lines[-1], 'data: [DONE]\n\n')

    def test_collect_events_builds_a_completion(self):
        async def events():
            yield TextDelta('Hi ')
            yield TextDelta('there')
            yield TokenUsage(input_tokens=1, output_tokens=2)

        completion = asyncio.run(streaming.collect_events(events(), 'claude-code.opus'))
        self.assertEqual(completion['choices'][0]['message']['content'], 'Hi there')
        self.assertEqual(completion['usage']['total_tokens'], 3)


class StandInConfig:
    """Open WebUI's config table, kept in memory."""

    def __init__(self):
        self.values = {}
        # A key whose writes fail, to check what a failed save leaves behind.
        self.failing_key = None

    async def get(self, key: str, default=None):
        return self.values.get(key, default)

    async def upsert(self, updates: dict) -> None:
        if self.failing_key in updates:
            raise RuntimeError(f'Could not save {self.failing_key}')
        self.values.update(updates)


class StandInRunnerConnection:
    """A cached connection to a Buddy Runner that only records whether it was closed."""

    def __init__(self):
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def load_subscription_service(data_dir: Path, config: StandInConfig):
    """Load service.py with stand-ins for the Open WebUI modules that reach the database."""
    stand_ins = {
        'open_webui.env': types.SimpleNamespace(DATA_DIR=str(data_dir)),
        'open_webui.models.config': types.SimpleNamespace(Config=config),
        'open_webui.models.models': types.SimpleNamespace(Models=None),
        'open_webui.utils.payload': types.SimpleNamespace(
            apply_model_params_to_body_openai=None,
            apply_system_prompt_to_body=None,
        ),
    }
    service_path = ROOT_DIR / 'backend' / 'open_webui' / 'utils' / 'subscriptions' / 'service.py'
    spec = importlib.util.spec_from_file_location('subscription_service_under_test', service_path)
    service = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stand_ins):
        spec.loader.exec_module(service)
    return service


class DeleteMachineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)
        self.config = StandInConfig()
        self.service = load_subscription_service(self.data_dir, self.config)

    def test_providers_move_to_this_server_and_forget_the_machines_paths(self):
        # Paths that exist only on the machine being removed.
        workspace = str(self.data_dir / 'office-pc-projects')
        cli_path = str(self.data_dir / 'office-pc-claude.exe')
        office_pc = {'id': 'office-pc', 'name': 'Office PC', 'url': 'http://office-pc:8765', 'key': 'test-key'}
        self.config.values['subscriptions.machines'] = [office_pc]
        self.config.values['subscriptions.claude'] = {
            'enable': True,
            'workspace': workspace,
            'cli_path': cli_path,
            'machine_id': 'office-pc',
        }
        self.config.values['subscriptions.codex'] = {
            'enable': True,
            'workspace': workspace,
            'machine_id': 'office-pc',
        }
        runner_connection = StandInRunnerConnection()
        self.service._remote_machines['office-pc'] = runner_connection

        asyncio.run(self.service.delete_machine('office-pc'))

        for provider_id in ('claude', 'codex'):
            settings = self.config.values[f'subscriptions.{provider_id}']
            self.assertEqual(settings['machine_id'], 'local')
            self.assertEqual(settings['workspace'], '')
            self.assertEqual(settings['cli_path'], '')
            self.assertTrue(settings['enable'])
        self.assertEqual(self.config.values['subscriptions.machines'], [])
        self.assertTrue(runner_connection.closed)

    def test_a_failed_move_keeps_the_machine(self):
        office_pc = {'id': 'office-pc', 'name': 'Office PC', 'url': 'http://office-pc:8765', 'key': 'test-key'}
        self.config.values['subscriptions.machines'] = [office_pc]
        self.config.values['subscriptions.codex'] = {'enable': True, 'machine_id': 'office-pc'}
        self.config.failing_key = 'subscriptions.codex'
        runner_connection = StandInRunnerConnection()
        self.service._remote_machines['office-pc'] = runner_connection

        with self.assertRaises(RuntimeError):
            asyncio.run(self.service.delete_machine('office-pc'))

        self.assertEqual(self.config.values['subscriptions.machines'], [office_pc])
        self.assertEqual(self.config.values['subscriptions.codex']['machine_id'], 'office-pc')
        self.assertFalse(runner_connection.closed)

    def test_this_server_cannot_be_removed(self):
        workspace = str(self.data_dir)
        self.config.values['subscriptions.claude'] = {'workspace': workspace, 'machine_id': 'local'}

        with self.assertRaises(SubscriptionError):
            asyncio.run(self.service.delete_machine('local'))

        self.assertEqual(self.config.values['subscriptions.claude']['workspace'], workspace)


if __name__ == '__main__':
    unittest.main()
