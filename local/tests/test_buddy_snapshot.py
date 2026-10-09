"""Verify parallel Buddy snapshots preserve source data and pause copied jobs."""

import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('buddy_snapshot_under_test', LOCAL_DIR / 'snapshot_buddy_data.py')
SNAPSHOT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SNAPSHOT)


class BuddySnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'source'
        self.destination = self.root / 'buddy'
        self.source.mkdir()
        self.connection = sqlite3.connect(self.source / 'webui.db')
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript(
            'CREATE TABLE user (id TEXT);'
            'CREATE TABLE chat (id TEXT);'
            'CREATE TABLE chat_message (id TEXT);'
            'CREATE TABLE automation (id TEXT, is_active INTEGER, next_run_at INTEGER);'
            'CREATE TABLE file (id TEXT, path TEXT);'
            "INSERT INTO user VALUES ('user');"
            "INSERT INTO chat VALUES ('chat');"
            "INSERT INTO chat_message VALUES ('message');"
            "INSERT INTO automation VALUES ('job', 1, 123);"
        )
        uploads = self.source / 'uploads'
        uploads.mkdir()
        self.upload = uploads / 'attachment.txt'
        self.upload.write_text('attachment content', encoding='utf-8')
        self.connection.execute('INSERT INTO file VALUES (?, ?)', ('file', str(self.upload)))
        self.connection.commit()
        vector_dir = self.source / 'vector_db'
        vector_dir.mkdir()
        with closing(sqlite3.connect(vector_dir / 'chroma.sqlite3')) as vector_connection:
            vector_connection.execute('CREATE TABLE vectors (id TEXT)')
            vector_connection.execute("INSERT INTO vectors VALUES ('vector')")
            vector_connection.commit()

    def test_live_wal_snapshot_preserves_data_and_only_pauses_copied_job(self):
        report = SNAPSHOT.create_snapshot(self.source, self.destination)
        self.assertEqual(report['users'], 1)
        self.assertEqual(report['chats'], 1)
        self.assertEqual(report['messages'], 1)
        self.assertEqual(report['copied_automations_paused'], 1)
        self.assertFalse(report['cache_copied'])
        self.assertEqual(self.connection.execute('SELECT is_active, next_run_at FROM automation').fetchone(), (1, 123))
        with closing(sqlite3.connect(self.destination / 'webui.db')) as copied_connection:
            self.assertEqual(copied_connection.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(copied_connection.execute('SELECT is_active, next_run_at FROM automation').fetchone(), (0, None))
            copied_path = copied_connection.execute('SELECT path FROM file').fetchone()[0]
            self.assertEqual(copied_path, str(self.destination / 'uploads' / 'attachment.txt'))
        self.assertEqual(Path(copied_path).read_text(encoding='utf-8'), 'attachment content')
        with closing(sqlite3.connect(self.destination / 'vector_db' / 'chroma.sqlite3')) as vector_connection:
            self.assertEqual(vector_connection.execute('SELECT id FROM vectors').fetchone()[0], 'vector')

    def test_existing_destination_is_never_overwritten(self):
        self.destination.mkdir()
        sentinel = self.destination / 'keep.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            SNAPSHOT.create_snapshot(self.source, self.destination)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')

    def test_snapshot_cannot_be_created_inside_source_data(self):
        with self.assertRaises(ValueError):
            SNAPSHOT.create_snapshot(self.source, self.source / 'nested-copy')
        self.assertFalse((self.source / 'nested-copy').exists())


if __name__ == '__main__':
    unittest.main()
