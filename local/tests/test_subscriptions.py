"""Offline tests for Claude and ChatGPT subscription models; no CLI sign-in or API traffic.

Run with the repository virtualenv:
    .venv\\Scripts\\python.exe -m unittest discover -s local/tests -p test_subscriptions.py -v
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from open_webui.utils.subscriptions import runner  # noqa: E402
from open_webui.utils.subscriptions.conversation import (  # noqa: E402
    CONTINUE_PROMPT,
    ChatTurn,
    SessionStore,
    conversation_key,
    parse_messages,
)
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402
from open_webui.utils.subscriptions.machines import LocalMachine, RemoteMachine, temp_file_arg  # noqa: E402
from open_webui.utils.subscriptions.process import ChildProcess, subscription_env  # noqa: E402

PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgo='


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


if __name__ == '__main__':
    unittest.main()
