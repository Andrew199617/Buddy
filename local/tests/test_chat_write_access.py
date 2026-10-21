"""Every route that writes into someone else's chat uses one rule (super_users.may_change_others_chat).

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_write_access.py -v

The rule itself is tested in test_super_users.py. These tests read the route source, so a
route that goes back to letting any administrator through fails here.
"""

import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / 'backend' / 'open_webui'
ANY_ADMIN = "user.role != 'admin'"
RULE = 'await may_change_others_chat(user)'


def function_source(path, name):
    source = (BACKEND / path).read_text(encoding='utf-8')
    start = source.index(f'async def {name}(')
    following = [source.find(marker, start + 1) for marker in ('\nasync def ', '\ndef ', '\n@')]
    end = min(position for position in following if position != -1)
    return source[start:end]


class ChatWriteRouteTests(unittest.TestCase):
    def assert_uses_the_rule(self, path, name):
        route = function_source(path, name)
        self.assertIn(RULE, route, f'{name} should use may_change_others_chat')
        self.assertNotIn(ANY_ADMIN, route, f'{name} still lets any administrator through')

    def test_editing_and_deleting_messages(self):
        self.assert_uses_the_rule('routers/chats.py', 'update_chat_message_by_id')
        self.assert_uses_the_rule('routers/chats.py', 'delete_chat_message_by_id')

    def test_answering_tool_calls(self):
        self.assert_uses_the_rule('utils/tool_approval.py', 'resolve_tool_call_output')

    def test_finishing_turns_and_actions(self):
        self.assert_uses_the_rule('main.py', 'verify_chat_ownership')

    def test_stopping_a_saved_chat(self):
        route = function_source('main.py', 'stop_tasks_by_chat_id_endpoint')
        saved_chat_branch = route[route.index('chat = await Chats.get_chat_by_id(chat_id)'):]
        self.assertIn(RULE, saved_chat_branch)

    def test_sending_a_turn_in_an_existing_chat(self):
        source = (BACKEND / 'main.py').read_text(encoding='utf-8')
        branch = source[source.index('# Existing chat — verify ownership'):]
        guard = branch[: branch.index('raise HTTPException')]
        self.assertIn(RULE, guard)
        self.assertNotIn(ANY_ADMIN, guard)

    def test_message_events_go_only_from_the_owner(self):
        route = function_source('routers/chats.py', 'send_chat_message_event_by_id')
        self.assertIn('if chat.user_id != user.id:', route)
        self.assertNotIn(ANY_ADMIN, route)


if __name__ == '__main__':
    unittest.main()
