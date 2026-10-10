"""Runner credential storage tests using disposable files and synthetic metadata.

POSIX metadata validation runs on every host. Native POSIX permission and
descriptor traversal tests skip on Windows; native Windows tests still exercise
key creation, reuse, links, types, and competing creation.
"""

import errno
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.utils.subscriptions import runner


OWNER_ID = 1000
FIXTURE_KEY = 'disposable-test-provider-key'


def metadata(mode, *, owner=OWNER_ID, links=1, attributes=0):
    return SimpleNamespace(
        st_mode=mode, st_uid=owner, st_nlink=links,
        st_file_attributes=attributes,
    )


def write_fixture(path, text=FIXTURE_KEY, *, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(text)


class RunnerKeyMetadataTests(unittest.TestCase):
    """Synthetic POSIX ownership/mode cases also run on Windows."""

    def test_synthetic_trusted_ancestors_and_sticky_tmp_are_accepted(self):
        for owner, mode in ((OWNER_ID, 0o755), (0, 0o755), (0, 0o1777), (OWNER_ID, 0o1777)):
            with self.subTest(owner=owner, mode=oct(mode)):
                runner._validate_key_directory(
                    metadata(stat.S_IFDIR | mode, owner=owner), owner_id=OWNER_ID,
                )

    def test_synthetic_unsafe_ancestor_owner_write_type_and_reparse_are_rejected(self):
        cases = {
            'unknown owner': metadata(stat.S_IFDIR | 0o755, owner=2000),
            'unknown sticky owner': metadata(stat.S_IFDIR | 0o1777, owner=2000),
            'shared writable': metadata(stat.S_IFDIR | 0o777, owner=0),
            'group writable': metadata(stat.S_IFDIR | 0o775),
            'regular file': metadata(stat.S_IFREG | 0o700),
            'symlink': metadata(stat.S_IFLNK | 0o700),
            'reparse point': metadata(stat.S_IFDIR | 0o700, attributes=0x400),
        }
        for name, information in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner._validate_key_directory(information, owner_id=OWNER_ID)

    def test_synthetic_private_state_requires_current_owner_and_exact_0700(self):
        runner._validate_key_directory(
            metadata(stat.S_IFDIR | 0o700), owner_id=OWNER_ID, private=True,
        )
        cases = {
            'root-owned state': metadata(stat.S_IFDIR | 0o700, owner=0),
            'other-owned state': metadata(stat.S_IFDIR | 0o700, owner=2000),
            'public state': metadata(stat.S_IFDIR | 0o755),
            'group state': metadata(stat.S_IFDIR | 0o750),
            'read-only state': metadata(stat.S_IFDIR | 0o500),
            'special mode': metadata(stat.S_IFDIR | 0o1700),
        }
        for name, information in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner._validate_key_directory(information, owner_id=OWNER_ID, private=True)

    def test_synthetic_private_regular_key_is_accepted(self):
        runner._validate_key_file(metadata(stat.S_IFREG | 0o600), owner_id=OWNER_ID)

    def test_synthetic_key_rejects_unsafe_owner_mode_type_links_and_reparse(self):
        cases = {
            'root-owned key': metadata(stat.S_IFREG | 0o600, owner=0),
            'other-owned key': metadata(stat.S_IFREG | 0o600, owner=2000),
            'public key': metadata(stat.S_IFREG | 0o644),
            'group key': metadata(stat.S_IFREG | 0o660),
            'read-only key': metadata(stat.S_IFREG | 0o400),
            'special mode': metadata(stat.S_IFREG | 0o4600),
            'directory': metadata(stat.S_IFDIR | 0o600),
            'fifo': metadata(stat.S_IFIFO | 0o600),
            'socket': metadata(stat.S_IFSOCK | 0o600),
            'device': metadata(stat.S_IFCHR | 0o600),
            'symlink': metadata(stat.S_IFLNK | 0o600),
            'hardlink': metadata(stat.S_IFREG | 0o600, links=2),
            'unlinked': metadata(stat.S_IFREG | 0o600, links=0),
            'reparse point': metadata(stat.S_IFREG | 0o600, attributes=0x400),
        }
        for name, information in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner._validate_key_file(information, owner_id=OWNER_ID)


class TemporaryKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='buddy-runner-key-test-')
        self.base = Path(self.temporary.name)
        self.state = self.base / 'state'
        self.key_file = self.state / 'key'

    def tearDown(self):
        self.temporary.cleanup()

    def prepare_state(self):
        self.state.mkdir(mode=0o700)

    def create_symlink(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'This host cannot create disposable symlink fixtures: {error}')


class RunnerKeyFileTests(TemporaryKeyTests):
    def test_native_creates_and_reuses_one_key_without_overwrite(self):
        created = runner.load_or_create_key(self.key_file)
        self.assertTrue(created)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), created)
        before = self.key_file.stat()
        self.assertEqual(runner.load_or_create_key(self.key_file), created)
        after = self.key_file.stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_native_reuses_fixture_key_unchanged(self):
        self.prepare_state()
        write_fixture(self.key_file)
        self.assertEqual(runner.load_or_create_key(self.key_file), FIXTURE_KEY)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_native_empty_key_is_rejected_without_regeneration(self):
        self.prepare_state()
        write_fixture(self.key_file, '')
        with self.assertRaises(ValueError):
            runner.load_or_create_key(self.key_file)
        self.assertEqual(self.key_file.read_bytes(), b'')

    def test_native_oversized_key_is_rejected_without_truncation_or_regeneration(self):
        self.prepare_state()
        for index, suffix in enumerate((' ' * 4097, 'x' * 4097)):
            with self.subTest(suffix='whitespace' if index == 0 else 'content'):
                key_file = self.state / f'key-{index}'
                fixture = FIXTURE_KEY + suffix
                write_fixture(key_file, fixture)
                with self.assertRaises(ValueError):
                    runner.load_or_create_key(key_file)
                self.assertEqual(key_file.read_text(encoding='utf-8'), fixture)

    def test_native_directory_key_is_rejected_without_overwrite(self):
        self.prepare_state()
        self.key_file.mkdir(mode=0o700)
        with self.assertRaises((ValueError, OSError)):
            runner.load_or_create_key(self.key_file)
        self.assertTrue(self.key_file.is_dir())

    def test_native_hardlinked_key_is_rejected_without_read_or_overwrite(self):
        self.prepare_state()
        target = self.base / 'fixture-key'
        write_fixture(target)
        try:
            os.link(target, self.key_file)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'This host cannot create disposable hardlink fixtures: {error}')
        self.assertEqual(self.key_file.stat().st_nlink, 2)
        with self.assertRaises((ValueError, OSError)):
            runner.load_or_create_key(self.key_file)
        self.assertEqual(target.read_text(encoding='utf-8'), FIXTURE_KEY)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_native_symlink_key_is_rejected_without_touching_target(self):
        self.prepare_state()
        target = self.base / 'fixture-key'
        write_fixture(target)
        self.create_symlink(self.key_file, target)
        with self.assertRaises((ValueError, OSError)):
            runner.load_or_create_key(self.key_file)
        self.assertTrue(self.key_file.is_symlink())
        self.assertEqual(target.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_native_symlink_state_ancestor_is_rejected(self):
        target = self.base / 'private-target'
        target.mkdir(mode=0o700)
        write_fixture(target / 'key')
        self.create_symlink(self.state, target, directory=True)
        with self.assertRaises((ValueError, OSError)):
            runner.load_or_create_key(self.key_file)
        self.assertEqual((target / 'key').read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_native_competing_exclusive_creation_reuses_valid_winner(self):
        self.prepare_state()
        original_open = os.open
        competed = False

        def competing_open(path, flags, mode=0o777, **options):
            nonlocal competed
            if not competed and flags & os.O_EXCL and Path(path).name == self.key_file.name:
                competed = True
                descriptor = original_open(path, flags, mode, **options)
                with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                    handle.write(FIXTURE_KEY)
                raise FileExistsError(errno.EEXIST, 'Disposable competing key creation', str(path))
            return original_open(path, flags, mode, **options)

        with patch.object(runner.os, 'open', side_effect=competing_open):
            self.assertEqual(runner.load_or_create_key(self.key_file), FIXTURE_KEY)
        self.assertTrue(competed)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_native_explicit_state_is_prepared_with_custom_key_parent(self):
        custom = self.base / 'custom'
        custom.mkdir(mode=0o755)
        key_file = custom / 'provider-key'
        write_fixture(key_file)
        self.assertEqual(
            runner.load_or_create_key(key_file, state_dir=self.state), FIXTURE_KEY,
        )
        self.assertTrue(self.state.is_dir())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction semantics are unavailable on POSIX')
    def test_windows_junction_state_is_rejected_without_touching_target(self):
        target = self.base / 'private-target'
        target.mkdir(mode=0o700)
        target_key = target / 'key'
        write_fixture(target_key)
        subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(self.state), str(target)],
            check=True, capture_output=True,
        )
        try:
            with self.assertRaises(ValueError):
                runner.load_or_create_key(self.key_file)
            self.assertEqual(target_key.read_text(encoding='utf-8'), FIXTURE_KEY)
        finally:
            # Remove only the disposable junction; never recurse into its target.
            self.state.rmdir()

    @unittest.skipUnless(os.name == 'nt', 'Windows no-delete sharing semantics are unavailable on POSIX')
    def test_windows_pins_prevent_parent_and_ancestor_rename_during_key_creation(self):
        self.prepare_state()
        original_open = os.open
        attempted = False
        moved_state = self.base / 'moved-state'
        moved_base = self.base.with_name(self.base.name + '-moved')

        def pinned_open(path, flags, mode=0o777, **options):
            nonlocal attempted
            if not attempted and flags & os.O_EXCL and Path(path).name == self.key_file.name:
                attempted = True
                for source, destination in ((self.state, moved_state), (self.base, moved_base)):
                    with self.subTest(source=source.name), self.assertRaises(PermissionError):
                        try:
                            source.rename(destination)
                        finally:
                            if destination.exists() and not source.exists():
                                destination.rename(source)
            return original_open(path, flags, mode, **options)

        with patch.object(runner.os, 'open', side_effect=pinned_open):
            created = runner.load_or_create_key(self.key_file)
        self.assertTrue(attempted)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), created)
        self.assertFalse(moved_state.exists())
        self.assertFalse(moved_base.exists())


@unittest.skipUnless(os.name == 'posix', 'Native POSIX ownership and mode semantics are unavailable on Windows')
class PosixRunnerKeyTests(TemporaryKeyTests):
    def test_posix_new_directories_and_key_are_private_under_permissive_umask(self):
        key_file = self.base / 'new-parent' / 'private-state' / 'key'
        previous = os.umask(0)
        try:
            runner.load_or_create_key(key_file)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(key_file.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(key_file.parent.parent.stat().st_mode), 0o700)

    def test_posix_existing_permissive_state_is_rejected_unchanged(self):
        self.prepare_state()
        write_fixture(self.key_file)
        self.state.chmod(0o755)
        with self.assertRaises(ValueError):
            runner.load_or_create_key(self.key_file)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o755)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_posix_existing_permissive_key_is_rejected_before_read_and_unchanged(self):
        self.prepare_state()
        write_fixture(self.key_file)
        self.key_file.chmod(0o644)
        with patch.object(runner.os, 'fdopen', side_effect=AssertionError('Invalid key metadata must not be read')):
            with self.assertRaises(ValueError):
                runner.load_or_create_key(self.key_file)
        self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o644)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_posix_shared_writable_ancestor_is_rejected_unchanged(self):
        shared = self.base / 'shared'
        shared.mkdir(mode=0o700)
        state = shared / 'private-state'
        state.mkdir(mode=0o700)
        key_file = state / 'key'
        write_fixture(key_file)
        shared.chmod(0o777)
        with self.assertRaises(ValueError):
            runner.load_or_create_key(key_file)
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o777)
        self.assertEqual(key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_posix_fifo_key_is_rejected_without_blocking(self):
        self.prepare_state()
        os.mkfifo(self.key_file, 0o600)
        with self.assertRaises(ValueError):
            runner.load_or_create_key(self.key_file)
        self.assertTrue(stat.S_ISFIFO(self.key_file.stat().st_mode))

    def test_posix_custom_key_parent_may_be_0755_but_state_stays_private(self):
        custom = self.base / 'custom'
        custom.mkdir(mode=0o700)
        custom.chmod(0o755)
        key_file = custom / 'provider-key'
        write_fixture(key_file)
        self.assertEqual(runner.load_or_create_key(key_file, state_dir=self.state), FIXTURE_KEY)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(custom.stat().st_mode), 0o755)

    def test_posix_custom_key_does_not_bypass_unsafe_state(self):
        self.prepare_state()
        self.state.chmod(0o755)
        custom = self.base / 'custom'
        custom.mkdir(mode=0o700)
        key_file = custom / 'provider-key'
        write_fixture(key_file)
        with self.assertRaises(ValueError):
            runner.load_or_create_key(key_file, state_dir=self.state)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o755)
        self.assertEqual(key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_posix_competing_permissive_key_is_rejected_without_overwrite(self):
        self.prepare_state()
        original_open = os.open
        competed = False

        def competing_open(path, flags, mode=0o777, **options):
            nonlocal competed
            if not competed and flags & os.O_EXCL and Path(path).name == self.key_file.name:
                competed = True
                descriptor = original_open(path, flags, 0o600, **options)
                os.fchmod(descriptor, 0o644)
                with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                    handle.write(FIXTURE_KEY)
                raise FileExistsError(errno.EEXIST, 'Disposable unsafe competing key creation', str(path))
            return original_open(path, flags, mode, **options)

        with patch.object(runner.os, 'open', side_effect=competing_open):
            with self.assertRaises(ValueError):
                runner.load_or_create_key(self.key_file)
        self.assertTrue(competed)
        self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o644)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)


if __name__ == '__main__':
    unittest.main()
