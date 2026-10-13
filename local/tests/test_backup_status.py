"""Tests for the admin backup status API (backend/open_webui/utils/backups.py and routers/backups.py).

Run from the repository root:
    python -m unittest discover -s local/tests -p test_backup_status.py -v

The status file is created with the backup service's own StatusStore (docker/buddy/backup/buddy_backup.py), so these
tests also check that both sides agree on the tables and columns. The router runs in a FastAPI TestClient with the
admin check replaced; nothing imports Buddy's database or configuration.
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils import backups  # noqa: E402


def load_module(name, path, extra_modules=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, extra_modules or {}):
        spec.loader.exec_module(module)
    return module


service = load_module('buddy_backup', ROOT / 'docker' / 'buddy' / 'backup' / 'buddy_backup.py')

NOW = datetime(2026, 10, 10, 17, 0, tzinfo=UTC)


class StatusFileTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temporary.name, 'status.db')
        self.store = service.StatusStore(self.path)
        self.store.ensure_schema()
        self.settings = service.Settings(
            depot='//Backups/Buddy', p4_port='ssl:example:1666', schedule_time='03:00', timezone='America/Los_Angeles'
        )

    def tearDown(self):
        self.temporary.cleanup()

    def heartbeat(self, at):
        self.store.heartbeat(self.settings, NOW + timedelta(hours=17), at)

    def finished_run(self, ok, created_at, error=None, changelist=None):
        run_id = self.store.start_run('scheduled', created_at)
        sizes = {}
        if ok:
            sizes = {service.DATABASE_NAME: 1000, service.ARCHIVE_NAME: 2000, service.ENV_NAME: 30}
        result = service.RunResult(
            ok=ok, log=[f'log of {run_id}'], changelist=changelist, sizes=sizes, error=error, db_revision='rev1'
        )
        self.store.finish_run(run_id, result, created_at + timedelta(seconds=40))
        return run_id


class OverviewTest(StatusFileTestCase):
    def test_not_configured(self):
        overview = backups.backup_overview('', NOW)
        self.assertFalse(overview['configured'])
        self.assertFalse(overview['available'])

    def test_service_not_started(self):
        overview = backups.backup_overview(os.path.join(self.temporary.name, 'absent.db'), NOW)
        self.assertTrue(overview['configured'])
        self.assertFalse(overview['available'])
        self.assertIn('not started', overview['reason'])
        self.assertFalse(os.path.exists(os.path.join(self.temporary.name, 'absent.db')))

    def test_healthy(self):
        self.heartbeat(NOW - timedelta(seconds=20))
        run_id = self.finished_run(True, NOW - timedelta(hours=7), changelist=301)
        overview = backups.backup_overview(self.path, NOW)
        self.assertTrue(overview['available'])
        self.assertTrue(overview['scheduler']['online'])
        self.assertFalse(overview['stale'])
        self.assertFalse(overview['failing'])
        self.assertIsNone(overview['active_run'])
        self.assertEqual(overview['last_success']['id'], run_id)
        self.assertEqual(overview['last_success']['changelist'], 301)
        self.assertEqual(overview['last_success']['db_bytes'], 1000)
        self.assertEqual(overview['last_success']['duration_seconds'], 40.0)
        self.assertEqual(overview['schedule']['depot_path'], '//Backups/Buddy')
        self.assertEqual(overview['schedule']['timezone'], 'America/Los_Angeles')
        self.assertNotIn('log', overview['runs'][0])

    def test_heartbeat_older_than_five_minutes_is_offline(self):
        self.heartbeat(NOW - timedelta(minutes=5, seconds=1))
        self.assertFalse(backups.backup_overview(self.path, NOW)['scheduler']['online'])

    def test_stale_without_a_success_in_26_hours(self):
        self.heartbeat(NOW)
        self.finished_run(True, NOW - timedelta(hours=27))
        self.assertTrue(backups.backup_overview(self.path, NOW)['stale'])

    def test_stale_when_nothing_ever_succeeded(self):
        self.heartbeat(NOW)
        self.assertTrue(backups.backup_overview(self.path, NOW)['stale'])

    def test_latest_failure_is_reported_until_a_later_success(self):
        self.heartbeat(NOW)
        self.finished_run(True, NOW - timedelta(hours=20))
        self.finished_run(False, NOW - timedelta(hours=2), error='p4 submit exited 1: no permission')
        overview = backups.backup_overview(self.path, NOW)
        self.assertTrue(overview['failing'])
        self.assertEqual(overview['latest_run']['error'], 'p4 submit exited 1: no permission')

        self.finished_run(True, NOW - timedelta(hours=1))
        self.assertFalse(backups.backup_overview(self.path, NOW)['failing'])

    def test_runs_are_newest_first_and_limited(self):
        self.heartbeat(NOW)
        for hours_ago in range(backups.RUNS_LIMIT + 5, 0, -1):
            self.finished_run(True, NOW - timedelta(hours=hours_ago))
        runs = backups.backup_overview(self.path, NOW)['runs']
        self.assertEqual(len(runs), backups.RUNS_LIMIT)
        self.assertGreater(runs[0]['created_at'], runs[1]['created_at'])


class QueueTest(StatusFileTestCase):
    def test_queues_once_and_the_service_claims_it(self):
        queued = backups.queue_backup(self.path, 'Owner', NOW)
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['trigger'], 'manual')
        self.assertEqual(queued['requested_by'], 'Owner')
        with self.assertRaises(backups.BackupAlreadyActive) as raised:
            backups.queue_backup(self.path, 'Owner', NOW)
        self.assertEqual(raised.exception.run['id'], queued['id'])

        self.assertEqual(self.store.claim_queued_run(NOW + timedelta(seconds=10)), queued['id'])
        self.assertEqual(backups.backup_overview(self.path, NOW)['active_run']['status'], 'running')
        with self.assertRaises(backups.BackupAlreadyActive):
            backups.queue_backup(self.path, 'Owner', NOW)

    def test_queue_requires_the_service(self):
        with self.assertRaises(backups.BackupServiceUnavailable):
            backups.queue_backup(os.path.join(self.temporary.name, 'absent.db'), 'Owner', NOW)

    def test_run_log(self):
        run_id = self.finished_run(False, NOW - timedelta(hours=1), error='boom')
        self.assertEqual(backups.backup_run_log(self.path, run_id), f'log of {run_id}')
        self.assertIsNone(backups.backup_run_log(self.path, 'missing'))


def fake_admin():
    return types.SimpleNamespace(id='admin-1', name='Owner', email='owner@example.com', role='admin')


class RouterTest(StatusFileTestCase):
    def setUp(self):
        super().setUp()
        auth_stub = types.ModuleType('open_webui.utils.auth')
        auth_stub.get_admin_user = lambda: None
        router_module = load_module(
            'buddy_backups_router',
            ROOT / 'backend' / 'open_webui' / 'routers' / 'backups.py',
            {'open_webui.utils.auth': auth_stub},
        )
        app = FastAPI()
        app.include_router(router_module.router, prefix='/api/v1/backups')
        app.dependency_overrides[auth_stub.get_admin_user] = fake_admin
        self.client = TestClient(app)
        environment = patch.dict(os.environ, {backups.STATUS_DB_ENV: self.path})
        environment.start()
        self.addCleanup(environment.stop)

    def test_overview(self):
        self.heartbeat(datetime.now(UTC))
        response = self.client.get('/api/v1/backups/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['scheduler']['online'])

    def test_run_now_queues_then_conflicts(self):
        first = self.client.post('/api/v1/backups/run')
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()['requested_by'], 'Owner')
        second = self.client.post('/api/v1/backups/run')
        self.assertEqual(second.status_code, 409)
        self.assertIn('already queued or running', second.json()['detail'])

    def test_run_now_without_the_service(self):
        with patch.dict(os.environ, {backups.STATUS_DB_ENV: ''}):
            response = self.client.post('/api/v1/backups/run')
        self.assertEqual(response.status_code, 503)

    def test_run_log(self):
        run_id = self.finished_run(True, NOW)
        self.assertEqual(self.client.get(f'/api/v1/backups/runs/{run_id}/log').json()['log'], f'log of {run_id}')
        self.assertEqual(self.client.get('/api/v1/backups/runs/missing/log').status_code, 404)


if __name__ == '__main__':
    unittest.main()
