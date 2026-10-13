"""Verify moved Buddy data points stored uploads at the container's data folder."""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path, PurePosixPath

LOCAL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = LOCAL_DIR / 'update_upload_paths.py'
spec = importlib.util.spec_from_file_location('buddy_upload_paths_under_test', SCRIPT)
UPLOAD_PATHS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(UPLOAD_PATHS)

OLD_DATA_DIR = r'C:\Users\Owner\Buddy\open-webui-data'
CONTAINER_DATA_DIR = PurePosixPath('/app/backend/data')


class UpdateUploadPathsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name) / 'data'
        (self.data_dir / 'uploads').mkdir(parents=True)
        self.database = self.data_dir / 'webui.db'
        rows = [
            ('photo', OLD_DATA_DIR + r'\uploads\abc_photo.png'),
            ('other-case', r'c:\users\owner\BUDDY\open-webui-data\Uploads\def_notes.txt'),
            ('forward-slashes', 'C:/Users/Owner/Buddy/open-webui-data/uploads/ghi_report.pdf'),
            ('elsewhere', r'C:\Users\Owner\Documents\outside.txt'),
            ('cloud', 's3://bucket/uploads/jkl_cloud.png'),
            ('empty', None),
        ]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('CREATE TABLE file (id TEXT, path TEXT)')
            connection.executemany('INSERT INTO file VALUES (?, ?)', rows)
            connection.commit()

    def stored_paths(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return dict(connection.execute('SELECT id, path FROM file').fetchall())

    def test_dry_run_reports_changes_without_writing(self):
        before = self.stored_paths()
        database_bytes = self.database.read_bytes()
        report = UPLOAD_PATHS.update_upload_paths(self.database, OLD_DATA_DIR, CONTAINER_DATA_DIR, dry_run=True)
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['matching_paths'], 3)
        self.assertEqual(report['other_paths'], 3)
        self.assertEqual(report['examples'][0], {
            'id': 'photo',
            'old': OLD_DATA_DIR + r'\uploads\abc_photo.png',
            'new': '/app/backend/data/uploads/abc_photo.png',
        })
        self.assertEqual(self.stored_paths(), before)
        self.assertEqual(self.database.read_bytes(), database_bytes)

    def test_windows_upload_paths_move_to_the_container_uploads_folder(self):
        report = UPLOAD_PATHS.update_upload_paths(self.database, OLD_DATA_DIR, CONTAINER_DATA_DIR, dry_run=False)
        self.assertFalse(report['dry_run'])
        self.assertEqual(report['matching_paths'], 3)
        self.assertEqual(self.stored_paths(), {
            'photo': '/app/backend/data/uploads/abc_photo.png',
            'other-case': '/app/backend/data/uploads/def_notes.txt',
            'forward-slashes': '/app/backend/data/uploads/ghi_report.pdf',
            'elsewhere': r'C:\Users\Owner\Documents\outside.txt',
            'cloud': 's3://bucket/uploads/jkl_cloud.png',
            'empty': None,
        })

    def test_running_again_changes_nothing(self):
        UPLOAD_PATHS.update_upload_paths(self.database, OLD_DATA_DIR, CONTAINER_DATA_DIR, dry_run=False)
        after_first_run = self.stored_paths()
        report = UPLOAD_PATHS.update_upload_paths(self.database, OLD_DATA_DIR, CONTAINER_DATA_DIR, dry_run=False)
        self.assertEqual(report['matching_paths'], 0)
        self.assertEqual(self.stored_paths(), after_first_run)

    def test_report_counts_uploads_missing_from_the_new_folder(self):
        (self.data_dir / 'uploads' / 'abc_photo.png').write_bytes(b'image')
        (self.data_dir / 'uploads' / 'ghi_report.pdf').write_bytes(b'report')
        report = UPLOAD_PATHS.update_upload_paths(self.database, OLD_DATA_DIR, self.data_dir, dry_run=True)
        self.assertEqual(report['missing_files'], 1)
        self.assertEqual(report['missing_file_ids'], ['other-case'])

    def test_old_data_folder_must_be_a_full_windows_path(self):
        before = self.stored_paths()
        for old_data_dir in ('open-webui-data', '/app/backend/data'):
            with self.subTest(old_data_dir=old_data_dir):
                with self.assertRaises(ValueError):
                    UPLOAD_PATHS.update_upload_paths(self.database, old_data_dir, CONTAINER_DATA_DIR, dry_run=False)
        self.assertEqual(self.stored_paths(), before)

    def test_missing_database_is_reported_and_never_created(self):
        missing = self.data_dir / 'missing' / 'webui.db'
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(FileNotFoundError):
                    UPLOAD_PATHS.update_upload_paths(missing, OLD_DATA_DIR, CONTAINER_DATA_DIR, dry_run=dry_run)
        self.assertFalse(missing.exists())

    def test_command_line_dry_run_prints_the_report(self):
        result = subprocess.run(
            [sys.executable, '-I', str(SCRIPT), '--old-data-dir', OLD_DATA_DIR,
             '--data-dir', str(self.data_dir), '--dry-run'],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['matching_paths'], 3)
        self.assertEqual(report['missing_files'], 3)
        self.assertEqual(self.stored_paths()['photo'], OLD_DATA_DIR + r'\uploads\abc_photo.png')

    def test_command_line_rejects_a_relative_old_data_folder(self):
        result = subprocess.run(
            [sys.executable, '-I', str(SCRIPT), '--old-data-dir', 'open-webui-data', '--data-dir', str(self.data_dir)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('full Windows path', result.stderr)


if __name__ == '__main__':
    unittest.main()
