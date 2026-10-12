"""Connecting a computer: enrollment, the Runner's hello, removal and policy.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_enrollment.py -v

The service runs with in-memory config (see test_subscription_machines.py)
and the real connection endpoint runs against a stand-in WebSocket.
"""

import ast
import asyncio
import json
import logging
import time
import types
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from test_subscription_machines import OWNER, ROOT, FakeUsers, MemoryConfig, load_service

from buddy_runner import protocol
from open_webui.utils.subscriptions.tunnel_hub import RunnerConnection

MEMBER = 'member-1'
MEMBER_USER = types.SimpleNamespace(id=MEMBER, role='user')
RUNNER_INFO = {
    'chat_dir': 'C:\\runner\\chat',
    'default_workspace': 'C:\\runner\\workspace',
    'host': {'id': 'instance-a', 'name': 'Member PC', 'platform': 'win32', 'version': '1'},
    'capabilities': {'providerExecution': True, 'terminals': True},
}


def load_runner_router(service, users):
    """Execute the real connection endpoint with the service under test."""
    path = ROOT / 'backend' / 'open_webui' / 'routers' / 'runners.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    wanted = ('EnrollForm', '_StarletteSocket', '_refuse', '_read_hello', '_valid_info',
              '_start_cleanup_retry', 'runner_connect')
    nodes = [node for node in tree.body if getattr(node, 'name', None) in wanted]
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = types.ModuleType('runner_router_under_test')
    from pydantic import BaseModel
    module.__dict__.update({
        'asyncio': asyncio,
        'json': json,
        'log': logging.getLogger(__name__),
        'BaseModel': BaseModel,
        'Optional': Optional,
        'WebSocket': object,
        'protocol': protocol,
        'UVICORN_WORKERS': 1,
        'Users': users,
        'service': service,
        'HUB': service.HUB,
        'RunnerConnection': RunnerConnection,
        '_background_tasks': set(),
    })
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])), str(path), 'exec'),
         module.__dict__)
    return module


class FakeWebSocket:
    def __init__(self, hello, headers=None):
        self.headers = headers or {}
        self.incoming = asyncio.Queue()
        if hello is not None:
            self.incoming.put_nowait(json.dumps(hello) if isinstance(hello, dict) else hello)
        self.sent = []
        self.closed_with = None

    async def accept(self):
        pass

    async def receive_text(self):
        text = await self.incoming.get()
        if text is None:
            raise ConnectionResetError('closed')
        return text

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=''):
        if self.closed_with is None:
            self.closed_with = (code, reason)
        self.incoming.put_nowait(None)


class RunnerEnrollmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.service = load_service(self.config, ROOT / 'unused-offline-enrollment-data')
        self.users = FakeUsers({MEMBER: MEMBER_USER})
        self.service.Users = self.users
        self.router = load_runner_router(self.service, self.users)

    async def enroll(self, user=MEMBER_USER, name='Member PC'):
        token = (await self.service.create_enrollment(user))['token']
        return await self.service.redeem_enrollment(token, name, RUNNER_INFO['host'])

    def hello(self, enrollment, credential=None, version=protocol.PROTOCOL_VERSION, instance='instance-a'):
        return {
            't': 'hello', 'protocol': version, 'machine_id': enrollment['machine_id'],
            'credential': credential or enrollment['credential'],
            'runner': {'version': 1, 'instance_id': instance}, 'info': RUNNER_INFO,
        }

    async def connect(self, hello, headers=None):
        websocket = FakeWebSocket(hello, headers)
        task = asyncio.create_task(self.router.runner_connect(websocket))
        self.addAsyncCleanup(self.finish, websocket, task)
        for _ in range(50):
            if websocket.sent or websocket.closed_with or task.done():
                break
            await asyncio.sleep(0.01)
        return websocket, task

    async def finish(self, websocket, task):
        websocket.incoming.put_nowait(None)
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)

    def machines(self):
        return self.config.values.get('subscriptions.machines', [])

    async def test_enrollment_creates_a_machine_that_keeps_only_a_digest_of_its_credential(self):
        enrollment = await self.enroll()
        self.assertTrue(enrollment['credential'].startswith('bdr1_'))
        [machine] = self.machines()
        self.assertEqual(machine['user_id'], MEMBER)
        self.assertEqual(machine['kind'], 'runner')
        self.assertEqual(machine['name'], 'Member PC')
        self.assertNotIn(enrollment['credential'], json.dumps(self.config.values))
        public = await self.service.describe_machines(MEMBER)
        self.assertEqual([entry['id'] for entry in public], [enrollment['machine_id']])
        self.assertFalse(public[0]['online'])
        self.assertNotIn('credential_hash', public[0])

    async def test_a_token_works_once_and_expires(self):
        token = (await self.service.create_enrollment(MEMBER_USER))['token']
        await self.service.redeem_enrollment(token, None, None)
        with self.assertRaisesRegex(self.service.SubscriptionError, 'expired'):
            await self.service.redeem_enrollment(token, None, None)
        token = (await self.service.create_enrollment(MEMBER_USER))['token']
        with patch.object(self.service.time, 'monotonic', return_value=time.monotonic() + 3600):
            with self.assertRaisesRegex(self.service.SubscriptionError, 'expired'):
                await self.service.redeem_enrollment(token, None, None)

    async def test_users_the_policy_excludes_cannot_enroll(self):
        await self.service.save_policy({'user_machines': 'admins'})
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.create_enrollment(MEMBER_USER)

    async def test_a_valid_hello_connects_and_records_the_computer(self):
        enrollment = await self.enroll()
        websocket, task = await self.connect(self.hello(enrollment))
        self.assertEqual(websocket.sent[0]['t'], 'welcome')
        self.assertEqual(websocket.sent[0]['machine']['id'], enrollment['machine_id'])
        self.assertTrue(self.service.HUB.is_online(enrollment['machine_id']))
        [machine] = self.machines()
        self.assertEqual(machine['host']['name'], 'Member PC')
        self.assertIn('last_seen_at', machine)
        [public] = await self.service.describe_machines(MEMBER)
        self.assertTrue(public['online'])
        tunnel_machine = await self.service.get_machine(MEMBER, enrollment['machine_id'])
        self.assertEqual(await tunnel_machine.chat_dir(), RUNNER_INFO['chat_dir'])

    async def test_a_wrong_credential_or_version_or_browser_is_refused(self):
        enrollment = await self.enroll()
        websocket, _ = await self.connect(self.hello(enrollment, credential='bdr1_wrong'))
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_UNKNOWN_MACHINE)
        websocket, _ = await self.connect(self.hello(enrollment, version=99))
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_UNSUPPORTED_VERSION)
        websocket, _ = await self.connect(self.hello(enrollment), headers={'origin': 'https://evil.example'})
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_NOT_ALLOWED)
        websocket, _ = await self.connect('not json')
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_BAD_HELLO)
        self.assertFalse(self.service.HUB.is_online(enrollment['machine_id']))

    async def test_removing_the_computer_disconnects_it_for_good(self):
        enrollment = await self.enroll()
        websocket, task = await self.connect(self.hello(enrollment))
        await self.service.delete_machine(MEMBER, enrollment['machine_id'])
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_UNKNOWN_MACHINE)
        self.assertFalse(self.service.HUB.is_online(enrollment['machine_id']))
        websocket, _ = await self.connect(self.hello(enrollment))
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_UNKNOWN_MACHINE)

    async def test_a_policy_change_disconnects_users_it_no_longer_allows(self):
        enrollment = await self.enroll()
        websocket, task = await self.connect(self.hello(enrollment))
        await self.service.save_policy({'user_machines': 'admins'})
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_NOT_ALLOWED)
        websocket, _ = await self.connect(self.hello(enrollment))
        self.assertEqual(websocket.closed_with[0], protocol.CLOSE_NOT_ALLOWED)

    async def test_connecting_again_replaces_the_older_connection(self):
        enrollment = await self.enroll()
        first, _ = await self.connect(self.hello(enrollment))
        second, _ = await self.connect(self.hello(enrollment, instance='instance-b'))
        self.assertEqual(first.closed_with[0], protocol.CLOSE_REPLACED)
        self.assertEqual(self.service.HUB.connection(enrollment['machine_id']).instance_id, 'instance-b')

    async def test_renaming_changes_only_the_name(self):
        enrollment = await self.enroll()
        await self.service.rename_machine(MEMBER, enrollment['machine_id'], '  Studio PC  ')
        [machine] = self.machines()
        self.assertEqual(machine['name'], 'Studio PC')
        with self.assertRaises(self.service.SubscriptionError):
            await self.service.rename_machine(OWNER, enrollment['machine_id'], 'Not yours')

    async def test_config_export_leaves_out_the_credential_digest_and_import_keeps_it(self):
        enrollment = await self.enroll()
        exported = self.service.redact_runner_keys(dict(self.config.values))
        self.assertNotIn('credential_hash', json.dumps(exported))
        restored = await self.service.keep_runner_keys(exported)
        [machine] = restored['subscriptions.machines']
        self.assertEqual(machine['credential_hash'], self.machines()[0]['credential_hash'])
        self.assertEqual(machine['id'], enrollment['machine_id'])


if __name__ == '__main__':
    unittest.main()
