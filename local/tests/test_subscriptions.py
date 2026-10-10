"""Offline tests for Claude and ChatGPT subscription models; no CLI sign-in or API traffic.

Run with the repository virtualenv:
    .venv\\Scripts\\python.exe -m unittest discover -s local/tests -p test_subscriptions.py -v
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from open_webui.utils.subscriptions.conversation import (  # noqa: E402
    CONTINUE_PROMPT,
    ChatTurn,
    SessionStore,
    conversation_key,
    parse_messages,
)
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402
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


if __name__ == '__main__':
    unittest.main()
