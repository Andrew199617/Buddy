"""Tests for the release backup service (docker/buddy/backup/buddy_backup.py).

Run from the repository root:
    python -m unittest discover -s local/tests -p test_buddy_backup_service.py -v

Or inside the backup image, which has the same Python and SQLite as the service:
    docker run --rm --entrypoint python -v "$PWD:/repo:ro" buddy-backup:local -m unittest discover -s /repo/local/tests -p test_buddy_backup_service.py

They use temporary files and a fake p4; nothing talks to Perforce or Docker.
"""

import importlib.util
import os
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'docker' / 'buddy' / 'backup' / 'buddy_backup.py'
_spec = importlib.util.spec_from_file_location('buddy_backup', SCRIPT)
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

PACIFIC = 'America/Los_Angeles'


def utc(*parts):
    return datetime(*parts, tzinfo=UTC)


class ScheduleTest(unittest.TestCase):
    def test_after_todays_slot(self):
        # 2026-10-10 10:00 PDT (17:00 UTC): the last slot is today 03:00 PDT (10:00 UTC), the next is tomorrow.
        last_slot, next_slot = bb.schedule_slots(utc(2026, 10, 10, 17, 0), '03:00', PACIFIC)
        self.assertEqual(last_slot, utc(2026, 10, 10, 10, 0))
        self.assertEqual(next_slot, utc(2026, 10, 11, 10, 0))

    def test_before_todays_slot(self):
        # 01:00 PDT (08:00 UTC): before today's slot, so the last slot was yesterday.
        last_slot, next_slot = bb.schedule_slots(utc(2026, 10, 10, 8, 0), '03:00', PACIFIC)
        self.assertEqual(last_slot, utc(2026, 10, 9, 10, 0))
        self.assertEqual(next_slot, utc(2026, 10, 10, 10, 0))

    def test_exactly_at_slot_counts_as_last(self):
        last_slot, next_slot = bb.schedule_slots(utc(2026, 10, 10, 10, 0), '03:00', PACIFIC)
        self.assertEqual(last_slot, utc(2026, 10, 10, 10, 0))
        self.assertEqual(next_slot, utc(2026, 10, 11, 10, 0))

    def test_dst_end_keeps_wall_clock(self):
        # DST ends 2026-11-01: 03:00 PST is 11:00 UTC, an hour later in UTC than the night before.
        last_slot, next_slot = bb.schedule_slots(utc(2026, 10, 31, 12, 0), '03:00', PACIFIC)
        self.assertEqual(last_slot, utc(2026, 10, 31, 10, 0))
        self.assertEqual(next_slot, utc(2026, 11, 1, 11, 0))

    def test_dst_start_keeps_wall_clock(self):
        # DST starts 2026-03-08: 03:00 PDT is 10:00 UTC, an hour earlier in UTC than the night before.
        last_slot, next_slot = bb.schedule_slots(utc(2026, 3, 7, 12, 0), '03:00', PACIFIC)
        self.assertEqual(last_slot, utc(2026, 3, 7, 11, 0))
        self.assertEqual(next_slot, utc(2026, 3, 8, 10, 0))

    def test_rejects_bad_time(self):
        with self.assertRaises(ValueError):
            bb.schedule_slots(utc(2026, 10, 10, 17, 0), '25:00', PACIFIC)


class TimeFormatTest(unittest.TestCase):
    def test_fixed_width_and_round_trip(self):
        whole_second = bb.format_time(utc(2026, 10, 10, 3, 0, 0))
        fractional = bb.format_time(utc(2026, 10, 10, 3, 0, 0, 123456))
        self.assertEqual(whole_second, '2026-10-10T03:00:00.000Z')
        self.assertEqual(fractional, '2026-10-10T03:00:00.123Z')
        self.assertEqual(bb.parse_time(fractional), utc(2026, 10, 10, 3, 0, 0, 123000))


class EnvValueTest(unittest.TestCase):
    def test_strips_one_pair_of_quotes(self):
        os.environ['BUDDY_BACKUP_TEST_VALUE'] = '"//Backups/Buddy"'
        self.assertEqual(bb.env_value('BUDDY_BACKUP_TEST_VALUE'), '//Backups/Buddy')
        os.environ['BUDDY_BACKUP_TEST_VALUE'] = 'plain'
        self.assertEqual(bb.env_value('BUDDY_BACKUP_TEST_VALUE'), 'plain')
        del os.environ['BUDDY_BACKUP_TEST_VALUE']


class StatusStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = bb.StatusStore(os.path.join(self.temporary.name, 'status', 'status.db'))
        self.store.ensure_schema()
        self.slot = utc(2026, 10, 10, 10, 0)

    def tearDown(self):
        self.temporary.cleanup()

    def insert_run(self, status, trigger, created_at, finished_at=None):
        run_id = f'run-{created_at.isoformat()}-{status}'
        finished_text = None
        if finished_at is not None:
            finished_text = bb.format_time(finished_at)
        with self.store.transaction() as connection:
            connection.execute(
                'INSERT INTO backup_runs (id, status, trigger, created_at, finished_at) VALUES (?, ?, ?, ?, ?)',
                (run_id, status, trigger, bb.format_time(created_at), finished_text),
            )
        return run_id

    def run_row(self, run_id):
        with closing(self.store.connect()) as connection:
            return dict(connection.execute('SELECT * FROM backup_runs WHERE id = ?', (run_id,)).fetchone())

    def test_due_when_nothing_ran_since_the_slot(self):
        self.insert_run('succeeded', 'scheduled', self.slot - timedelta(days=1))
        self.assertTrue(self.store.scheduled_run_due(self.slot, self.slot + timedelta(hours=6)))

    def test_not_due_after_a_success_or_while_one_is_in_flight(self):
        for status in ('succeeded', 'running', 'queued'):
            with self.subTest(status=status):
                run_id = self.insert_run(status, 'manual', self.slot + timedelta(minutes=5))
                self.assertFalse(self.store.scheduled_run_due(self.slot, self.slot + timedelta(hours=1)))
                with self.store.transaction() as connection:
                    connection.execute('DELETE FROM backup_runs WHERE id = ?', (run_id,))

    def test_failed_scheduled_run_retries_after_an_hour(self):
        failed_at = self.slot + timedelta(minutes=1)
        self.insert_run('failed', 'scheduled', self.slot, finished_at=failed_at)
        self.assertFalse(self.store.scheduled_run_due(self.slot, failed_at + timedelta(minutes=59)))
        self.assertTrue(self.store.scheduled_run_due(self.slot, failed_at + timedelta(hours=1)))

    def test_starts_the_scheduled_run_when_due(self):
        now = self.slot + timedelta(hours=4)
        run_id = self.store.start_scheduled_run_if_due(self.slot, now)
        row = self.run_row(run_id)
        self.assertEqual((row['status'], row['trigger']), ('running', 'scheduled'))
        self.assertIsNone(self.store.start_scheduled_run_if_due(self.slot, now + timedelta(seconds=30)))

    def test_queued_run_goes_before_the_scheduled_run(self):
        # Queued before the slot, so it does not count as tonight's backup, but it still goes first.
        queued = self.insert_run('queued', 'manual', self.slot - timedelta(minutes=5))
        now = self.slot + timedelta(minutes=1)
        self.assertIsNone(self.store.start_scheduled_run_if_due(self.slot, now))
        self.assertEqual(self.store.claim_queued_run(now), queued)

    def test_claims_the_oldest_queued_run_once(self):
        older = self.insert_run('queued', 'manual', self.slot)
        newer = self.insert_run('queued', 'manual', self.slot + timedelta(seconds=1))
        now = self.slot + timedelta(minutes=1)
        self.assertEqual(self.store.claim_queued_run(now), older)
        self.assertEqual(self.store.claim_queued_run(now), newer)
        self.assertIsNone(self.store.claim_queued_run(now))
        self.assertEqual(self.run_row(older)['status'], 'running')
        self.assertEqual(self.run_row(older)['started_at'], bb.format_time(now))

    def test_start_run_refuses_while_another_is_running(self):
        self.store.start_run('manual', self.slot)
        with self.assertRaises(bb.BackupError):
            self.store.start_run('scheduled', self.slot)

    def test_restart_fails_interrupted_runs(self):
        run_id = self.store.start_run('scheduled', self.slot)
        self.store.fail_interrupted_runs(self.slot + timedelta(minutes=2))
        row = self.run_row(run_id)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('Interrupted', row['error'])

    def test_finish_run_records_result(self):
        run_id = self.store.start_run('manual', self.slot)
        sizes = {bb.DATABASE_NAME: 100, bb.ARCHIVE_NAME: 200, bb.ENV_NAME: 30}
        result = bb.RunResult(ok=True, log=['line'] * 3, changelist=42, sizes=sizes, db_revision='abc123')
        self.store.finish_run(run_id, result, self.slot + timedelta(minutes=1))
        row = self.run_row(run_id)
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual(row['changelist'], 42)
        self.assertEqual((row['db_bytes'], row['data_bytes'], row['env_bytes']), (100, 200, 30))
        self.assertEqual(row['db_revision'], 'abc123')
        self.assertEqual(row['log'], 'line\nline\nline')

    def test_heartbeat_keeps_one_row(self):
        settings = bb.Settings(depot='//Backups/Buddy-test', p4_port='ssl:example:1666', timezone=PACIFIC)
        self.store.heartbeat(settings, self.slot, self.slot)
        self.store.heartbeat(settings, self.slot + timedelta(days=1), self.slot + timedelta(seconds=30))
        with closing(self.store.connect()) as connection:
            rows = connection.execute('SELECT * FROM backup_scheduler').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['depot_path'], '//Backups/Buddy-test')
        self.assertEqual(rows[0]['next_run_at'], bb.format_time(self.slot + timedelta(days=1)))


def create_wal_database(path, rows):
    """A WAL-mode database whose rows are still only in the -wal file; the returned connection keeps it there."""
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.execute('CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)')
    connection.executemany('INSERT INTO item (name) VALUES (?)', [(f'item {n}',) for n in range(rows)])
    connection.commit()
    return connection


def count_items(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute('SELECT count(*) FROM item').fetchone()[0]


class SnapshotTest(unittest.TestCase):
    def test_copies_rows_still_in_the_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'live.db')
            writer = create_wal_database(source, rows=50)
            try:
                self.assertGreater(os.path.getsize(source + '-wal'), 0)
                copy = os.path.join(directory, 'copy.db')
                bb.snapshot_sqlite(source, copy)
                self.assertEqual(count_items(copy), 50)
                self.assertFalse(os.path.exists(copy + '-wal'))
            finally:
                writer.close()


class DataArchiveTest(unittest.TestCase):
    def test_archive_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = os.path.join(directory, 'data')
            files = {
                'uploads/photo.png': b'png',
                'static/favicon.png': b'icon',
                'cache/image/generations/picture.png': b'generated',
                'cache/embedding/models/model.bin': b'model',
                'cache/whisper/models/base.pt': b'model',
                'cache/tiktoken/cl100k': b'model',
                'local-patches.log': b'log',
                'webui.db': b'not opened by the archive',
                'webui.db-wal': b'wal',
                'webui.db-shm': b'shm',
            }
            for name, content in files.items():
                path = Path(data_dir, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            os.makedirs(os.path.join(data_dir, 'vector_db'))
            writer = create_wal_database(os.path.join(data_dir, 'vector_db', 'chroma.sqlite3'), rows=7)
            scratch_dir = os.path.join(directory, 'scratch')
            os.makedirs(scratch_dir)
            archive_path = os.path.join(directory, 'data.tar.gz')
            log = []
            try:
                bb.build_data_archive(data_dir, archive_path, scratch_dir, log)
            finally:
                writer.close()

            with tarfile.open(archive_path) as archive:
                names = set(archive.getnames())
                extract_dir = os.path.join(directory, 'extracted')
                archive.extractall(extract_dir, filter='data')

            for kept in ('uploads/photo.png', 'static/favicon.png', 'cache/image/generations/picture.png',
                         'local-patches.log', 'vector_db/chroma.sqlite3'):
                self.assertIn(kept, names)
            for left_out in ('webui.db', 'webui.db-wal', 'webui.db-shm', 'cache/embedding', 'cache/whisper',
                             'cache/tiktoken', 'vector_db/chroma.sqlite3-wal', 'vector_db/chroma.sqlite3-shm'):
                self.assertNotIn(left_out, names)
            self.assertFalse(any(name.startswith('cache/embedding/') for name in names))
            self.assertEqual(count_items(os.path.join(extract_dir, 'vector_db', 'chroma.sqlite3')), 7)
            self.assertEqual(os.listdir(scratch_dir), [])
            self.assertIn('vector_db/chroma.sqlite3: SQLite copied with the backup API', log)


class EnvFileTest(unittest.TestCase):
    def write_env(self, directory, text):
        path = os.path.join(directory, 'source.env')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return path

    def test_reports_key_names_only(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_env(
                directory,
                '# comment\nWEBUI_SECRET_KEY="s3cret-value"\nexport BUDDY_PORT=8082\nEMPTY=\n',
            )
            self.assertEqual(
                bb.env_file_keys(source), {'WEBUI_SECRET_KEY': True, 'BUDDY_PORT': True, 'EMPTY': False}
            )
            log = []
            bb.copy_env_file(source, os.path.join(directory, 'buddy.env'), log)
            self.assertIn('WEBUI_SECRET_KEY', log[0])
            self.assertNotIn('s3cret-value', '\n'.join(log))

    def test_requires_the_secret_key(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_env(directory, 'WEBUI_SECRET_KEY=\nBUDDY_PORT=8082\n')
            with self.assertRaises(bb.BackupError):
                bb.copy_env_file(source, os.path.join(directory, 'buddy.env'), [])

    def test_missing_mount_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(bb.BackupError):
                bb.copy_env_file(os.path.join(directory, 'absent.env'), os.path.join(directory, 'buddy.env'), [])


class FakePerforce(bb.Perforce):
    """Records p4 commands and answers fstat from a dict of depot file -> head action."""

    def __init__(self, head_actions, fail_submit=False):
        settings = bb.Settings(depot='//Backups/Buddy-test', p4_port='ssl:example:1666', p4_user='owner',
                               p4_client='buddy-backup-test')
        super().__init__(settings, log=[])
        self.head_actions = head_actions
        self.fail_submit = fail_submit
        self.commands = []

    def run(self, args, **kwargs):
        self.commands.append(args)
        if args[:2] == ['-ztag', 'fstat']:
            action = self.head_actions.get(args[-1])
            if action is None:
                return ''
            return f'... depotFile {args[-1]}\n... headAction {action}\n'
        if args[:2] == ['-ztag', 'submit']:
            if self.fail_submit:
                raise bb.BackupError('p4 submit exited 1: out of space')
            return '... change 41\n... submittedChange 42\n'
        return ''


class PerforceSubmitTest(unittest.TestCase):
    def opened(self, perforce):
        return [(args[0], os.path.basename(args[-1])) for args in perforce.commands if args[0] in ('add', 'edit')]

    def test_first_run_adds_and_later_runs_edit(self):
        perforce = FakePerforce({
            '//Backups/Buddy-test/webui.db': 'edit',
            '//Backups/Buddy-test/data.tar.gz': 'add',
            '//Backups/Buddy-test/buddy.env': 'delete',
        })
        change = perforce.submit('/work/ws', bb.BACKUP_FILES, 'Buddy backup')
        self.assertEqual(change, 42)
        self.assertEqual(
            self.opened(perforce), [('edit', 'webui.db'), ('edit', 'data.tar.gz'), ('add', 'buddy.env')]
        )
        for args in perforce.commands:
            if args[0] in ('add', 'edit'):
                self.assertEqual(args[1:3], ['-t', 'binary+S7'])

    def test_client_spec_maps_only_the_backup_path(self):
        perforce = FakePerforce({})
        spec = perforce.client_spec('/work/ws')
        self.assertIn('\t//Backups/Buddy-test/... //buddy-backup-test/...\n', spec)
        self.assertIn('SubmitOptions: submitunchanged\n', spec)
        self.assertIn('Host:\n', spec)

    def test_failed_submit_reverts(self):
        perforce = FakePerforce({}, fail_submit=True)
        with self.assertRaises(bb.BackupError):
            perforce.submit('/work/ws', bb.BACKUP_FILES, 'Buddy backup')
        self.assertEqual(perforce.commands[-1], ['revert', '-k', '//buddy-backup-test/...'])

    def test_flush_runs_before_files_are_opened(self):
        perforce = FakePerforce({})
        perforce.submit('/work/ws', bb.BACKUP_FILES, 'Buddy backup')
        verbs = [args[0] for args in perforce.commands]
        self.assertLess(verbs.index('flush'), verbs.index('add'))
        self.assertEqual(perforce.commands[0], ['client', '-i'])


if __name__ == '__main__':
    unittest.main()
