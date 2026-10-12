"""Where each chat runs: the table that keeps it, its migration, and removal with the chat.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_run_targets.py -v

Production definitions run against in-memory and temporary SQLite databases;
nothing touches Buddy's database.
"""

import ast
import importlib.util
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend' / 'open_webui'
MIGRATION = BACKEND / 'migrations' / 'versions' / 'c3a7e5d29b14_add_chat_run_target_table.py'


@asynccontextmanager
async def isolated_db_context(db):
    if db is None:
        raise AssertionError('Tests must supply their isolated database session')
    yield db


def execute_definitions(path, names, namespace, class_name=None):
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    nodes = parsed.body
    if class_name:
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name).body
    selected = []
    for node in nodes:
        if getattr(node, 'name', None) in names:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(getattr(target, 'id', None) in names for target in node.targets):
            selected.append(node)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)


MODELS = {name: getattr(sa, name) for name in (
    'JSON', 'BigInteger', 'Boolean', 'Column', 'ForeignKey', 'Index', 'String', 'Text',
    'and_', 'bindparam', 'case', 'cast', 'delete', 'func', 'or_', 'select', 'text', 'update',
)}
MODELS.update(
    # Classes defined with no module name count as built-in types, which pydantic won't read attributes from.
    __name__='chat_run_targets_under_test',
    Base=declarative_base(), JSONCodec=json, AsyncSession=AsyncSession, BaseModel=BaseModel,
    ConfigDict=ConfigDict, IntegrityError=IntegrityError, Optional=__import__('typing').Optional,
    time=__import__('time'), get_async_db_context=isolated_db_context,
)
execute_definitions(BACKEND / 'models/chats.py', {'Chat'}, MODELS)
execute_definitions(BACKEND / 'models/chat_messages.py', {'ChatMessage'}, MODELS)
execute_definitions(BACKEND / 'models/automations.py', {'AutomationRun'}, MODELS)
execute_definitions(BACKEND / 'models/chat_run_targets.py', {
    'ChatRunTarget', 'ChatRunTargetModel', 'ChatRunTargetTable', 'FOLDER_PATH', 'FOLDER_AUTO', 'FOLDER_NONE',
    'SOURCE_COPY', 'RECENT_SCAN_LIMIT',
}, MODELS)
execute_definitions(BACKEND / 'models/chats.py', {'delete_chats_by_user_id_and_folder_id'}, MODELS, 'ChatTable')

ChatRunTarget = MODELS['ChatRunTarget']
ChatRunTargetModel = MODELS['ChatRunTargetModel']
# Annotations are strings here, resolved against the definitions' namespace.
ChatRunTargetModel.model_rebuild(_types_namespace=MODELS)
Chat = MODELS['Chat']
delete_folder_chats = MODELS['delete_chats_by_user_id_and_folder_id'].__get__(SimpleNamespace())


def target(chat_id, user_id='alice', created_at=1, **overrides):
    values = {
        'chat_id': chat_id, 'user_id': user_id, 'kind': 'computer', 'machine_id': 'pc-1',
        'machine_revision': 'rev-1', 'folder_mode': 'path', 'path': 'C:\\code\\site', 'access': 'full',
        'source': 'bar', 'created_at': created_at,
    }
    values.update(overrides)
    return ChatRunTargetModel(**values)


def chat_row(chat_id, user_id='alice', folder_id=None):
    return {
        'id': chat_id, 'user_id': user_id, 'title': 'Fixture chat', 'chat': {}, 'created_at': 1, 'updated_at': 1,
        'meta': {}, 'archived': False, 'pinned': False, 'folder_id': folder_id,
    }


class ChatRunTargetTableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(MODELS['Base'].metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.table = MODELS['ChatRunTargetTable']()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def test_a_chat_keeps_the_first_target_saved_for_it(self):
        first = await self.table.insert_if_absent(target('chat-1', access='full'), db=self.session)
        second = await self.table.insert_if_absent(target('chat-1', access='chat', kind='detached'), db=self.session)
        self.assertEqual(second, first)
        self.assertEqual((await self.table.get('chat-1', db=self.session)).access, 'full')
        self.assertIsNone(await self.table.get('chat-2', db=self.session))

    async def test_last_used_and_recent_folders_are_the_persons_own(self):
        await self.table.insert_if_absent(target('chat-1', created_at=1), db=self.session)
        await self.table.insert_if_absent(target('chat-2', created_at=2, path='C:\\code\\api'), db=self.session)
        await self.table.insert_if_absent(target('chat-3', created_at=3), db=self.session)
        await self.table.insert_if_absent(
            target('chat-4', created_at=4, kind='detached', machine_id=None, folder_mode='none', path=None),
            db=self.session,
        )
        await self.table.insert_if_absent(target('chat-5', user_id='bob', created_at=5), db=self.session)
        self.assertEqual((await self.table.last_for_user('alice', db=self.session)).chat_id, 'chat-4')
        recent = await self.table.recent_paths('alice', db=self.session)
        self.assertEqual([row.path for row in recent], ['C:\\code\\site', 'C:\\code\\api'])
        self.assertEqual(recent[0].chat_id, 'chat-3')
        self.assertIsNone(await self.table.last_for_user('carol', db=self.session))

    async def test_a_fork_copies_the_target_only_between_the_same_persons_chats(self):
        await self.table.insert_if_absent(target('chat-1'), db=self.session)
        copied = await self.table.copy_for_owner('chat-1', 'chat-2', 'alice', db=self.session)
        self.assertEqual(copied.source, 'copy')
        self.assertEqual(copied.path, 'C:\\code\\site')
        self.assertIsNone(await self.table.copy_for_owner('chat-1', 'chat-3', 'bob', db=self.session))
        self.assertIsNone(await self.table.get('chat-3', db=self.session))
        self.assertIsNone(await self.table.copy_for_owner('missing', 'chat-4', 'alice', db=self.session))

    async def test_deleting_a_folders_chats_deletes_their_targets(self):
        await self.session.execute(sa.insert(Chat), [
            chat_row('chat-1', folder_id='website'), chat_row('chat-2'), chat_row('chat-3', 'bob', 'website'),
        ])
        await self.session.commit()
        for chat_id, user_id in (('chat-1', 'alice'), ('chat-2', 'alice'), ('chat-3', 'bob')):
            await self.table.insert_if_absent(target(chat_id, user_id=user_id), db=self.session)
        self.assertTrue(await delete_folder_chats('alice', 'website', db=self.session))
        self.assertIsNone(await self.table.get('chat-1', db=self.session))
        self.assertIsNotNone(await self.table.get('chat-2', db=self.session))
        self.assertIsNotNone(await self.table.get('chat-3', db=self.session))


def load_migration():
    spec = importlib.util.spec_from_file_location('chat_run_target_migration', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChatRunTargetMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-run-target-migration-')
        self.addCleanup(directory.cleanup)
        self.engine = sa.create_engine(f'sqlite:///{Path(directory.name) / "fixture.db"}')
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE chat (id TEXT PRIMARY KEY)'))
        self.migration = load_migration()

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def test_upgrade_is_idempotent_and_indexes_recent_targets_per_person(self):
        self.run_step(self.migration.upgrade)
        self.run_step(self.migration.upgrade)
        inspector = sa.inspect(self.engine)
        self.assertIn('chat_run_target', inspector.get_table_names())
        indexes = {index['name']: index for index in inspector.get_indexes('chat_run_target')}
        self.assertEqual(indexes['idx_chat_run_target_user_created']['column_names'], ['user_id', 'created_at'])
        self.assertEqual(self.migration.down_revision, 'b6d1f0c3a9e4')

    def test_downgrade_removes_the_table(self):
        self.run_step(self.migration.upgrade)
        self.run_step(self.migration.downgrade)
        self.assertNotIn('chat_run_target', sa.inspect(self.engine).get_table_names())


if __name__ == '__main__':
    unittest.main()
