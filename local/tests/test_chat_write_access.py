"""Who may edit or delete messages in someone else's chat (routers/chats.py _may_change_others_chat).

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_write_access.py -v
"""

import ast
import types
import unittest
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[2] / 'backend' / 'open_webui' / 'routers' / 'chats.py'

ADMIN = types.SimpleNamespace(id='admin', role='admin')
SUPER_USER = types.SimpleNamespace(id='super', role='admin')


def load_rule(admin_chat_access):
    parsed = ast.parse(ROUTER.read_text(encoding='utf-8'))
    selected = [node for node in parsed.body if getattr(node, 'name', None) == '_may_change_others_chat']
    if len(selected) != 1:
        raise AssertionError(f'_may_change_others_chat is missing from {ROUTER}')

    async def is_super_user(user):
        return user.id == SUPER_USER.id

    namespace = {'ENABLE_ADMIN_CHAT_ACCESS': admin_chat_access, 'is_super_user': is_super_user}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(ROUTER), 'exec'), namespace)
    return namespace['_may_change_others_chat']


class ChatWriteAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_super_user_changes_someone_elses_chat(self):
        rule = load_rule(True)
        self.assertTrue(await rule(SUPER_USER))
        self.assertFalse(await rule(ADMIN))

    async def test_nobody_does_while_admins_may_not_see_chats(self):
        rule = load_rule(False)
        self.assertFalse(await rule(SUPER_USER))

    def test_message_events_go_only_from_the_owner(self):
        source = ROUTER.read_text(encoding='utf-8')
        route = source[source.index('async def send_chat_message_event_by_id('):]
        route = route[: route.index('\n@router.')]
        self.assertIn('if chat.user_id != user.id:', route)
        self.assertNotIn("user.role != 'admin'", route)


if __name__ == '__main__':
    unittest.main()
