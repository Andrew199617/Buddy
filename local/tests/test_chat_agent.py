"""A chat's custom agent and Ultra setting stay with the person who chose them.

A share link, and anyone but the owner who reads the chat, lists its Ultra workers
without what they were doing or found.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_agent.py -v
"""

import ast
import asyncio
import contextlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel
from sqlalchemy.orm import declarative_base

MODULE_PATH = Path(__file__).resolve().parents[2] / 'backend' / 'open_webui' / 'utils' / 'chat_agent.py'
spec = importlib.util.spec_from_file_location('chat_agent', MODULE_PATH)
chat_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat_agent)

AGENT = {
    'name': 'Reviewer',
    'description': 'Reviews changes',
    'path': '.github/agents/reviewer.agent.md',
    'instructions': 'Review the diff.',
}
ULTRA = {'on': True, 'worker_model': 'claude-code.sonnet', 'worker_effort': 'high'}
CHAT = {
    'title': 'Plan',
    'system': 'Be brief.',
    'params': {'system': 'Be brief.', 'temperature': 0.2},
    'history': {},
    'agent': AGENT,
    'ultra': ULTRA,
}

RUNNING_WORKER = {
    'id': 'task-1',
    'parent_id': None,
    'depth': 1,
    'label': 'Research the parser',
    'state': 'running',
    'model': 'Sonnet',
    'effort': 'high',
    'activity': 'Reading C:\\code\\site\\billing_secrets.py',
    'summary': None,
    'tokens': 1200,
    'started_at': 100,
    'ended_at': None,
}
FINISHED_SUB_WORKER = {
    'id': 'task-2',
    'parent_id': 'task-1',
    'depth': 2,
    'label': 'Check the tests',
    'state': 'done',
    'model': 'Sonnet',
    'effort': 'high',
    'activity': None,
    'summary': 'The tests in tests/test_private_pricing.py pass.',
    'tokens': 800,
    'started_at': 110,
    'ended_at': 150,
}
QUESTION = {'id': 'question', 'parentId': None, 'role': 'user', 'content': 'Fix the parser.'}
REPLY = {
    'id': 'reply',
    'parentId': 'question',
    'role': 'assistant',
    'content': 'Fixed.',
    'workers': {'items': [RUNNING_WORKER, FINISHED_SUB_WORKER], 'running': 1},
}
CHAT_WITH_WORKERS = {
    'title': 'Plan',
    'agent': AGENT,
    'history': {'currentId': 'reply', 'messages': {'question': QUESTION, 'reply': REPLY}},
    'messages': [QUESTION, REPLY],
}
# What a share link shows of REPLY's workers.
SHARED_WORKERS = {
    'items': [
        {
            'id': 'task-1',
            'parent_id': None,
            'depth': 1,
            'label': 'Research the parser',
            'state': 'running',
            'model': 'Sonnet',
            'effort': 'high',
            'tokens': 1200,
            'started_at': 100,
            'ended_at': None,
        },
        {
            'id': 'task-2',
            'parent_id': 'task-1',
            'depth': 2,
            'label': 'Check the tests',
            'state': 'done',
            'model': 'Sonnet',
            'effort': 'high',
            'tokens': 800,
            'started_at': 110,
            'ended_at': 150,
        },
    ],
    'running': 1,
}


class WithoutAgentTests(unittest.TestCase):
    def test_leaves_out_only_the_agent(self):
        shared = chat_agent.without_agent(CHAT)
        self.assertNotIn('agent', shared)
        self.assertEqual(shared, {key: value for key, value in CHAT.items() if key != 'agent'})
        self.assertEqual(CHAT['agent'], AGENT)

    def test_passes_through_contents_that_are_not_a_dict(self):
        self.assertIsNone(chat_agent.without_agent(None))


class ContentsForOwnerTests(unittest.TestCase):
    def test_a_copy_of_ones_own_chat_keeps_its_agent(self):
        self.assertEqual(chat_agent.contents_for_owner(CHAT, 'owner', 'owner'), CHAT)

    def test_a_copy_of_someone_elses_chat_starts_without_one(self):
        copied = chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertNotIn('agent', copied)
        self.assertEqual(copied['title'], 'Plan')

    def test_a_copy_of_someone_elses_chat_loses_its_hidden_system_prompt(self):
        # The page sends params.system as the system message, which a Full-access plan follows.
        copied = chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertNotIn('system', copied)
        self.assertEqual(copied['params'], {'temperature': 0.2})

    def test_a_chat_brought_in_from_a_file_starts_without_one(self):
        imported = chat_agent.contents_for_owner(CHAT, None, 'owner')
        self.assertNotIn('agent', imported)
        self.assertNotIn('system', imported)
        self.assertNotIn('system', imported['params'])

    def test_a_copy_of_ones_own_chat_keeps_its_system_prompt(self):
        own = chat_agent.contents_for_owner(CHAT, 'owner', 'owner')
        self.assertEqual(own['params']['system'], 'Be brief.')

    def test_the_original_chat_is_left_as_it_was(self):
        chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertEqual(CHAT['agent'], AGENT)
        self.assertEqual(CHAT['params'], {'system': 'Be brief.', 'temperature': 0.2})
        self.assertEqual(CHAT['system'], 'Be brief.')
        self.assertEqual(CHAT['ultra'], ULTRA)

    def test_a_copy_of_someone_elses_chat_loses_its_ultra_setting(self):
        # Ultra's workers would spend the new owner's plan, which they never chose.
        copied = chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertNotIn('ultra', copied)

    def test_a_chat_brought_in_from_a_file_loses_its_ultra_setting(self):
        imported = chat_agent.contents_for_owner(CHAT, None, 'owner')
        self.assertNotIn('ultra', imported)

    def test_a_copy_of_ones_own_chat_keeps_its_ultra_setting(self):
        own = chat_agent.contents_for_owner(CHAT, 'owner', 'owner')
        self.assertEqual(own['ultra'], ULTRA)

    def test_a_chat_without_an_agent_is_unchanged(self):
        plain = {'title': 'Plan'}
        self.assertEqual(chat_agent.contents_for_owner(plain, None, 'owner'), plain)

    def test_params_that_are_not_a_dict_are_left_alone(self):
        odd = {'title': 'Plan', 'params': None}
        self.assertEqual(chat_agent.contents_for_owner(odd, None, 'owner'), odd)


class ContentsForShareTests(unittest.TestCase):
    def test_a_share_lists_each_worker_by_label_state_model_and_effort(self):
        shared = chat_agent.contents_for_share(CHAT_WITH_WORKERS)
        self.assertEqual(shared['history']['messages']['reply']['workers'], SHARED_WORKERS)

    def test_the_shown_branch_lists_the_same_workers(self):
        shared = chat_agent.contents_for_share(CHAT_WITH_WORKERS)
        self.assertEqual(shared['messages'][1]['workers'], SHARED_WORKERS)

    def test_a_share_leaves_out_what_workers_were_doing_and_found(self):
        # Both can name private project details.
        snapshot = json.dumps(chat_agent.contents_for_share(CHAT_WITH_WORKERS))
        self.assertNotIn('billing_secrets', snapshot)
        self.assertNotIn('test_private_pricing', snapshot)
        self.assertNotIn('activity', snapshot)
        self.assertNotIn('summary', snapshot)

    def test_a_share_keeps_the_rest_of_each_message(self):
        shared = chat_agent.contents_for_share(CHAT_WITH_WORKERS)
        self.assertEqual(shared['history']['messages']['question'], QUESTION)
        reply = {key: value for key, value in shared['history']['messages']['reply'].items() if key != 'workers'}
        self.assertEqual(reply, {key: value for key, value in REPLY.items() if key != 'workers'})
        self.assertEqual(shared['history']['currentId'], 'reply')

    def test_a_share_leaves_out_the_agent(self):
        self.assertNotIn('agent', chat_agent.contents_for_share(CHAT_WITH_WORKERS))

    def test_the_original_chat_keeps_its_workers_details(self):
        chat_agent.contents_for_share(CHAT_WITH_WORKERS)
        self.assertEqual(REPLY['workers']['items'], [RUNNING_WORKER, FINISHED_SUB_WORKER])
        self.assertEqual(RUNNING_WORKER['activity'], 'Reading C:\\code\\site\\billing_secrets.py')
        self.assertIs(CHAT_WITH_WORKERS['history']['messages']['reply'], REPLY)

    def test_workers_that_are_not_a_list_of_workers_are_left_out(self):
        odd_reply = {'id': 'reply', 'role': 'assistant', 'workers': 'Reading billing_secrets.py'}
        shared = chat_agent.contents_for_share({'messages': [odd_reply]})
        self.assertNotIn('workers', shared['messages'][0])

    def test_entries_that_are_not_workers_are_left_out(self):
        reply = {'id': 'reply', 'workers': {'items': ['billing_secrets.py', RUNNING_WORKER], 'running': 2}}
        shared = chat_agent.contents_for_share({'messages': [reply]})
        self.assertEqual(shared['messages'][0]['workers'], {'items': [SHARED_WORKERS['items'][0]], 'running': 1})

    def test_a_chat_without_workers_only_loses_its_agent(self):
        self.assertEqual(chat_agent.contents_for_share(CHAT), chat_agent.without_agent(CHAT))

    def test_passes_through_contents_that_are_not_a_dict(self):
        self.assertIsNone(chat_agent.contents_for_share(None))



class ChatResponse(BaseModel):
    id: str
    user_id: str
    chat: dict


def load_chat_response_for():
    router = MODULE_PATH.parents[1] / 'routers' / 'chats.py'
    parsed = ast.parse(router.read_text(encoding='utf-8'))
    selected = [node for node in parsed.body if getattr(node, 'name', None) == '_chat_response_for']
    if len(selected) != 1:
        raise AssertionError(f'_chat_response_for is missing from {router}')
    namespace = {'ChatResponse': ChatResponse, 'contents_for_share': chat_agent.contents_for_share}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(router), 'exec'), namespace)
    return namespace['_chat_response_for']


class ChatResponseForTests(unittest.TestCase):
    def setUp(self):
        self.response_for = load_chat_response_for()
        self.chat = types.SimpleNamespace(id='chat-1', user_id='owner', chat=CHAT)

    def test_the_owner_sees_their_agent(self):
        owner = types.SimpleNamespace(id='owner')
        self.assertEqual(self.response_for(owner, self.chat).chat['agent'], AGENT)

    def test_anyone_else_including_an_administrator_does_not(self):
        admin = types.SimpleNamespace(id='admin', role='admin')
        response = self.response_for(admin, self.chat)
        self.assertNotIn('agent', response.chat)
        self.assertEqual(response.chat['title'], 'Plan')
        self.assertEqual(CHAT['agent'], AGENT)

    def test_the_owner_sees_what_their_workers_were_doing_and_found(self):
        owner = types.SimpleNamespace(id='owner')
        chat = types.SimpleNamespace(id='chat-1', user_id='owner', chat=CHAT_WITH_WORKERS)
        response = self.response_for(owner, chat)
        self.assertEqual(response.chat['history']['messages']['reply']['workers'], REPLY['workers'])

    def test_someone_the_chat_is_shared_with_reads_its_workers_as_the_share_shows_them(self):
        # A share grant also lets them read the chat itself, by the id its share carries.
        reader = types.SimpleNamespace(id='reader', role='user')
        chat = types.SimpleNamespace(id='chat-1', user_id='owner', chat=CHAT_WITH_WORKERS)
        response = self.response_for(reader, chat)
        self.assertEqual(response.chat['history']['messages']['reply']['workers'], SHARED_WORKERS)
        self.assertEqual(response.chat['messages'][1]['workers'], SHARED_WORKERS)
        self.assertNotIn('agent', response.chat)
        self.assertEqual(CHAT_WITH_WORKERS['history']['messages']['reply']['workers'], REPLY['workers'])


class SavedChat:
    """The chat row a share link is made from."""

    def __init__(self, chat):
        self.id = 'chat-1'
        self.title = 'Plan'
        self.chat = chat


class ShareSession:
    """The session calls SharedChatsTable makes, over rows kept by their class and id."""

    def __init__(self, rows):
        self.rows = rows

    async def get(self, row_class, row_id):
        return self.rows.get((row_class, row_id))

    def add(self, row):
        self.rows[(type(row), row.id)] = row

    async def commit(self):
        pass

    async def refresh(self, row):
        pass


@contextlib.asynccontextmanager
async def given_session(db):
    yield db


def load_shared_chats():
    stand_ins = {
        'open_webui.internal.db': types.SimpleNamespace(
            Base=declarative_base(),
            JSONField=None,
            get_async_db_context=given_session,
        ),
        'open_webui.utils.chat_agent': chat_agent,
    }
    path = MODULE_PATH.parents[1] / 'models' / 'shared_chats.py'
    spec = importlib.util.spec_from_file_location('shared_chats_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stand_ins):
        spec.loader.exec_module(module)
    return module


class ShareSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.shared_chats = load_shared_chats()
        self.session = ShareSession({(SavedChat, 'chat-1'): SavedChat(CHAT_WITH_WORKERS)})

    def run_with_saved_chats(self, coroutine_function, *args):
        # SharedChatsTable imports the chat table when it runs.
        with patch.dict(sys.modules, {'open_webui.models.chats': types.SimpleNamespace(Chat=SavedChat)}):
            return asyncio.run(coroutine_function(*args, db=self.session))

    def test_a_new_share_saves_its_workers_without_their_details(self):
        shared = self.run_with_saved_chats(self.shared_chats.SharedChats.create, 'chat-1', 'owner')
        self.assertEqual(shared.chat['history']['messages']['reply']['workers'], SHARED_WORKERS)
        self.assertEqual(shared.chat['messages'][1]['workers'], SHARED_WORKERS)
        self.assertNotIn('agent', shared.chat)

    def test_a_refreshed_share_saves_its_workers_without_their_details(self):
        earlier = self.shared_chats.SharedChat(
            id='share-1',
            chat_id='chat-1',
            user_id='owner',
            title='Plan',
            chat={},
            created_at=1,
            updated_at=1,
        )
        self.session.add(earlier)
        shared = self.run_with_saved_chats(self.shared_chats.SharedChats.update, 'share-1')
        self.assertEqual(shared.chat['history']['messages']['reply']['workers'], SHARED_WORKERS)
        self.assertNotIn('agent', shared.chat)


class ChatModel(BaseModel):
    id: str
    user_id: str
    title: str
    chat: dict
    created_at: int
    updated_at: int
    share_id: str | None = None


class SnapshotsFromBefore:
    """Shared chats with one snapshot saved with its workers' details, before shares left them out."""

    async def get_by_id(self, share_id, db=None):
        return types.SimpleNamespace(
            id=share_id,
            user_id='owner',
            title='Plan',
            chat=CHAT_WITH_WORKERS,
            created_at=1,
            updated_at=2,
        )


def load_get_chat_by_share_id():
    path = MODULE_PATH.parents[1] / 'models' / 'chats.py'
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    selected = []
    for node in parsed.body:
        if getattr(node, 'name', None) == 'ChatTable':
            selected = [method for method in node.body if getattr(method, 'name', None) == 'get_chat_by_share_id']
    if len(selected) != 1:
        raise AssertionError(f'ChatTable.get_chat_by_share_id is missing from {path}')
    # chats.py postpones its annotations, so its own future import comes along.
    future_imports = [node for node in parsed.body if isinstance(node, ast.ImportFrom) and node.module == '__future__']
    namespace = {'ChatModel': ChatModel, 'contents_for_share': chat_agent.contents_for_share}
    module = ast.fix_missing_locations(ast.Module(body=future_imports + selected, type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace['get_chat_by_share_id']


class ChatByShareIdTests(unittest.TestCase):
    def test_a_snapshot_from_before_is_shown_without_its_workers_details(self):
        get_chat_by_share_id = load_get_chat_by_share_id()
        stand_ins = {'open_webui.models.shared_chats': types.SimpleNamespace(SharedChats=SnapshotsFromBefore())}
        with patch.dict(sys.modules, stand_ins):
            chat = asyncio.run(get_chat_by_share_id(None, 'share-1'))
        self.assertIsNotNone(chat)
        self.assertEqual(chat.chat['history']['messages']['reply']['workers'], SHARED_WORKERS)
        self.assertNotIn('agent', chat.chat)


if __name__ == '__main__':
    unittest.main()
