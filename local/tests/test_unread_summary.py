"""Offline unread aggregation tests with SQLite, fake Redis and ASGI auth.

Production definitions are loaded without app configuration or database startup.
Every database is in-memory; no real accounts, Redis or live endpoints are used.
"""

import ast
import asyncio
import json
import logging
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend' / 'open_webui'


def execute_definitions(path, names, namespace, class_name=None):
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    nodes = parsed.body
    if class_name:
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name).body
    selected = [node for node in nodes if getattr(node, 'name', None) in names]
    if len(selected) != len(names):
        raise AssertionError(f'Missing production definitions in {path}: {names}')
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)


@asynccontextmanager
async def isolated_db_context(db):
    if db is None:
        raise AssertionError('Tests must supply their isolated database session')
    yield db


MODELS = {name: getattr(sa, name) for name in (
    'JSON', 'BigInteger', 'Boolean', 'Column', 'ForeignKey', 'Index', 'String', 'Text',
    'and_', 'bindparam', 'cast', 'func', 'or_', 'select', 'text',
)}
MODELS.update(Base=declarative_base(), JSONCodec=json, AsyncSession=AsyncSession,
              get_async_db_context=isolated_db_context)
execute_definitions(BACKEND / 'models/chats.py', {'Chat'}, MODELS)
execute_definitions(BACKEND / 'models/chat_messages.py', {'ChatMessage'}, MODELS)
execute_definitions(BACKEND / 'models/chats.py', {'get_unread_summary'}, MODELS, 'ChatTable')
Chats = SimpleNamespace(get_unread_summary=MODELS['get_unread_summary'].__get__(object()))
Chat = MODELS['Chat']
ChatMessage = MODELS['ChatMessage']

TASKS = {'REDIS_TASKS_KEY': 'test:tasks', 'REDIS_TASK_TTL': 300, 'tasks': {}, 'item_tasks': {}}
execute_definitions(BACKEND / 'tasks.py', {'get_active_task_item_ids'}, TASKS)


class FakeRedisPipeline:
    def __init__(self, owner):
        self.owner = owner
        self.keys = []

    def exists(self, key):
        self.keys.append(key)

    async def execute(self):
        self.owner.pipeline_sizes.append(len(self.keys))
        if self.owner.pipeline_error:
            raise self.owner.pipeline_error
        return [key in self.owner.live_keys for key in self.keys]


class FakeRedis:
    def __init__(self, registry, live_task_ids=()):
        self.registry = registry
        self.live_keys = {f'test:tasks:{task_id}' for task_id in live_task_ids}
        self.hash_reads = 0
        self.pipeline_sizes = []
        self.pipeline_error = None

    async def hgetall(self, key):
        if key != 'test:tasks':
            raise AssertionError(key)
        self.hash_reads += 1
        return self.registry

    def pipeline(self, transaction):
        if transaction is not False:
            raise AssertionError('RedisCluster-compatible nontransactional pipeline required')
        return FakeRedisPipeline(self)


def chat_row(chat_id, **overrides):
    values = {
        'id': chat_id, 'user_id': 'alice', 'title': 'private title',
        'chat': {'messages': [{'content': 'private history'}]},
        'created_at': 1, 'updated_at': 100, 'last_read_at': 10,
        'meta': {}, 'archived': False, 'pinned': False, 'folder_id': None,
    }
    values.update(overrides)
    return values


class UnreadDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        TASKS.update(REDIS_TASK_TTL=300, tasks={}, item_tasks={})
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(MODELS['Base'].metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.selects = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                self.selects.append((statement, parameters))

        sa.event.listen(self.engine.sync_engine, 'before_cursor_execute', capture)

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def seed(self, chats, messages=()):
        await self.session.execute(sa.insert(Chat), chats)
        if messages:
            await self.session.execute(sa.insert(ChatMessage), messages)
        await self.session.commit()

    async def summary(self, active=(), user_id='alice'):
        return await Chats.get_unread_summary(user_id, set(active), db=self.session)

    async def test_ownership_archive_internal_pinned_folder_and_old_history(self):
        await self.seed([
            chat_row('old-page', updated_at=20, created_at=1),
            chat_row('pinned', pinned=True),
            chat_row('folder', folder_id='old-folder'),
            chat_row('never-read-zero', updated_at=0, last_read_at=None),
            chat_row('foreign', user_id='bob'),
            chat_row('archived', archived=True),
            chat_row('internal', meta={'internal': True}),
            chat_row('read', last_read_at=100),
            chat_row('future-read', last_read_at=200),
        ])
        self.assertEqual(await self.summary(), {'count': 4, 'only_chat_id': None})
        self.assertEqual(await self.summary(user_id='bob'), {'count': 1, 'only_chat_id': 'foreign'})
        stored_folder = await self.session.get(Chat, 'folder')
        self.assertEqual(stored_folder.folder_id, 'old-folder')

    async def test_zero_and_sole_unread_id_are_exact(self):
        self.assertEqual(await self.summary(), {'count': 0, 'only_chat_id': None})
        await self.seed([chat_row('sole')])
        self.assertEqual(await self.summary(), {'count': 1, 'only_chat_id': 'sole'})
        await self.session.execute(sa.update(Chat).values(last_read_at=Chat.updated_at))
        await self.session.commit()
        self.assertEqual(await self.summary(), {'count': 0, 'only_chat_id': None})

    async def test_only_live_tasks_with_unfinished_assistants_suppress_unread(self):
        ids = ['running', 'stale-unfinished', 'finished', 'user-pending', 'unknown-done']
        await self.seed([chat_row(chat_id) for chat_id in ids], [
            {'id': 'a', 'chat_id': 'running', 'user_id': 'alice', 'role': 'assistant', 'done': False},
            {'id': 'b', 'chat_id': 'stale-unfinished', 'user_id': 'alice', 'role': 'assistant', 'done': False},
            {'id': 'c', 'chat_id': 'finished', 'user_id': 'alice', 'role': 'assistant', 'done': True},
            {'id': 'd', 'chat_id': 'user-pending', 'user_id': 'alice', 'role': 'user', 'done': False},
            {'id': 'e', 'chat_id': 'unknown-done', 'user_id': 'alice', 'role': 'assistant', 'done': None},
        ])
        active = ['running', 'finished', 'user-pending', 'unknown-done', 'foreign-task-item']
        self.assertEqual(await self.summary(active), {'count': 4, 'only_chat_id': None})
        await self.session.execute(sa.update(ChatMessage).where(ChatMessage.id == 'a').values(done=True))
        await self.session.commit()
        self.assertEqual(await self.summary(active), {'count': 5, 'only_chat_id': None})

    async def test_task_completion_before_registry_cleanup_restores_unread(self):
        await self.seed([chat_row('running')], [
            {'id': 'pending', 'chat_id': 'running', 'user_id': 'alice', 'role': 'assistant', 'done': False},
        ])
        release = asyncio.Event()
        live = asyncio.create_task(release.wait())
        TASKS['tasks']['task'] = live
        TASKS['item_tasks']['running'] = ['task']
        try:
            active = await TASKS['get_active_task_item_ids'](None)
            self.assertEqual(await self.summary(active), {'count': 0, 'only_chat_id': None})
            release.set()
            await live
            # Done callbacks can still be queued; stale local indexes cannot
            # hide a completed unread response while cleanup catches up.
            self.assertIn('task', TASKS['tasks'])
            active = await TASKS['get_active_task_item_ids'](None)
            self.assertEqual(await self.summary(active), {'count': 1, 'only_chat_id': 'running'})
        finally:
            live.cancel()
            await asyncio.gather(live, return_exceptions=True)

    async def test_large_read_history_and_active_registry_use_one_query_and_bounded_response(self):
        await self.seed([chat_row(f'read-{index}', last_read_at=100) for index in range(5000)] + [
            chat_row('sole'), chat_row('running'),
        ], [{'id': 'live', 'chat_id': 'running', 'user_id': 'alice', 'role': 'assistant', 'done': False}])
        active = {f'other-worker-{index}' for index in range(5000)} | {'running'}
        self.selects.clear()
        result = await self.summary(active)
        self.assertEqual(result, {'count': 1, 'only_chat_id': 'sole'})
        self.assertLess(len(json.dumps(result)), 100)
        self.assertEqual(len(self.selects), 1)
        statement, parameters = self.selects[0]
        self.assertIn('count(chat.id)', statement)
        self.assertIn('min(chat.id)', statement)
        self.assertIn('json_each', statement)
        self.assertNotIn('chat.title', statement)
        self.assertNotIn('chat.chat', statement)
        self.assertLess(len(parameters), 10)

    async def test_many_unread_chats_do_not_expand_response(self):
        await self.seed([chat_row(f'unread-{index}') for index in range(3000)])
        result = await self.summary()
        self.assertEqual(result, {'count': 3000, 'only_chat_id': None})
        self.assertLess(len(json.dumps(result)), 100)

    async def test_postgresql_query_compiles_with_single_active_registry_bind(self):
        class CaptureSession:
            statement = None

            async def connection(self):
                return SimpleNamespace(dialect=postgresql.dialect())

            async def execute(self, statement):
                self.statement = statement
                return SimpleNamespace(one=lambda: (2, 'not-returned'))

        session = CaptureSession()
        result = await Chats.get_unread_summary('alice', {'active', 'x"; SELECT secret'}, db=session)
        self.assertEqual(result, {'count': 2, 'only_chat_id': None})
        compiled = session.statement.compile(dialect=postgresql.dialect())
        self.assertIn('json_array_elements_text(CAST(', str(compiled))
        self.assertNotIn('SELECT secret', str(compiled))
        self.assertEqual(json.loads(compiled.params['active_chat_ids']), ['active', 'x"; SELECT secret'])
        self.assertLess(len(compiled.params), 10)

    async def test_asgi_requires_auth_scopes_owner_and_reports_lookup_failure(self):
        await self.seed([chat_row('alice-unread'), chat_row('bob-unread', user_id='bob')])

        async def verified_user(request: Request):
            if request.headers.get('authorization') != 'Bearer synthetic-alice':
                raise HTTPException(status_code=401, detail='Not authenticated')
            return SimpleNamespace(id='alice')

        async def isolated_session():
            yield self.session

        route_namespace = {
            'router': APIRouter(), 'BaseModel': BaseModel, 'Request': Request, 'AsyncSession': AsyncSession,
            'Depends': Depends, 'get_verified_user': verified_user, 'get_async_session': isolated_session,
            'get_active_task_item_ids': TASKS['get_active_task_item_ids'], 'Chats': Chats,
            'log': logging.getLogger('isolated-unread'), 'HTTPException': HTTPException, 'status': status,
            'ERROR_MESSAGES': SimpleNamespace(DEFAULT=lambda: 'Unavailable'),
        }
        execute_definitions(BACKEND / 'routers/chats.py', {
            'ChatUnreadSummaryResponse', 'get_session_user_unread_chat_summary',
        }, route_namespace)
        app = FastAPI()
        app.state.redis = FakeRedis({})
        app.include_router(route_namespace['router'], prefix='/api/v1/chats')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            denied = await client.get('/api/v1/chats/unread')
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(app.state.redis.hash_reads, 0)
            response = await client.get('/api/v1/chats/unread?user_id=bob', headers={
                'Authorization': 'Bearer synthetic-alice',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'count': 1, 'only_chat_id': 'alice-unread'})
            app.state.redis = FakeRedis({'task': 'alice-unread'}, ['task'])
            app.state.redis.pipeline_error = ConnectionError('Synthetic Redis failure')
            with self.assertLogs('isolated-unread', level='ERROR'):
                failed = await client.get('/api/v1/chats/unread', headers={
                    'Authorization': 'Bearer synthetic-alice',
                })
            self.assertEqual(failed.status_code, 503)
            self.assertNotIn('count', failed.json())


class ActiveRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        TASKS.update(REDIS_TASK_TTL=300, tasks={}, item_tasks={})

    async def test_local_registry_ignores_completed_cancelled_and_missing_tasks(self):
        release = asyncio.Event()
        live = asyncio.create_task(release.wait())
        done = asyncio.create_task(asyncio.sleep(0))
        cancelled = asyncio.create_task(asyncio.sleep(10))
        cancelled.cancel()
        await asyncio.gather(done, cancelled, return_exceptions=True)
        TASKS['tasks'].update(live=live, done=done, cancelled=cancelled)
        TASKS['item_tasks'].update(live=['live'], completed=['done'], cancelled=['cancelled'], missing=['gone'])
        try:
            self.assertEqual(await TASKS['get_active_task_item_ids'](None), {'live'})
            release.set()
            await live
            self.assertEqual(await TASKS['get_active_task_item_ids'](None), set())
        finally:
            live.cancel()
            await asyncio.gather(live, return_exceptions=True)

    async def test_redis_ttl_membership_batches_and_duplicate_chat_tasks(self):
        registry = {f'task-{index}'.encode(): f'chat-{index}'.encode() for index in range(600)}
        registry.update({b'stale': b'stale-chat', b'duplicate': b'chat-1', b'no-item': b''})
        redis = FakeRedis(registry, [f'task-{index}' for index in range(600)] + ['duplicate'])
        self.assertEqual(await TASKS['get_active_task_item_ids'](redis), {f'chat-{index}' for index in range(600)})
        self.assertEqual(redis.hash_reads, 1)
        self.assertEqual(redis.pipeline_sizes, [256, 256, 90])

    async def test_no_ttl_preserves_distributed_registry_semantics(self):
        TASKS['REDIS_TASK_TTL'] = 0
        redis = FakeRedis({'remote-live': 'chat', 'unassociated': ''})
        self.assertEqual(await TASKS['get_active_task_item_ids'](redis), {'chat'})
        self.assertEqual(redis.pipeline_sizes, [])

    async def test_registry_errors_propagate(self):
        redis = FakeRedis({'live': 'chat'}, ['live'])
        redis.pipeline_error = ConnectionError('Synthetic Redis failure')
        with self.assertRaises(ConnectionError):
            await TASKS['get_active_task_item_ids'](redis)


if __name__ == '__main__':
    unittest.main()
