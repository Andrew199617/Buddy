"""Runner credential storage tests using disposable files and synthetic metadata.

POSIX metadata and Windows DACL policy validation run on every host. Native
POSIX permission and descriptor traversal tests skip on Windows; native Windows
tests exercise key creation, reuse, links, types, competing creation, and
owner-only DACLs on disposable folders.
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
from buddy_runner import runner
from buddy_runner import windows_key_acl as acl


OWNER_ID = 1000
FIXTURE_KEY = 'disposable-test-provider-key'
# Synthetic account SIDs, and two well-known groups that are never trusted.
USER_SID = 'S-1-5-21-1000-1000-1000-1001'
OTHER_SID = 'S-1-5-21-1000-1000-1000-1002'
USERS = 'S-1-5-32-545'
AUTHENTICATED_USERS = 'S-1-5-11'
# Accounts in a domain that does not exist, so Windows cannot name them.
UNKNOWN_SID = 'S-1-5-21-1111111111-2222222222-3333333333-4444'
INHERITED_UNKNOWN_SID = 'S-1-5-21-1111111111-2222222222-3333333333-4445'
READ_ONLY = 0x120089
MODIFY = 0x1301bf


def metadata(mode, *, owner=OWNER_ID, links=1, attributes=0):
    return SimpleNamespace(
        st_mode=mode, st_uid=owner, st_nlink=links,
        st_file_attributes=attributes,
    )


def write_fixture(path, text=FIXTURE_KEY, *, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(text)


def allowed(sid, mask=acl.GENERIC_ALL):
    return acl.AccessEntry(sid, allowed=True, mask=mask)


def grant_access(path, entry):
    """Add one access entry to a disposable Windows fixture, such as *S-1-5-32-545:R."""
    subprocess.run(['icacls', str(path), '/grant', entry], check=True, capture_output=True)


def set_access_sddl(path, sddl):
    """Set the entries a disposable Windows fixture holds itself, including ones icacls cannot name."""
    script = (
        '$acl = Get-Acl -LiteralPath $env:FIXTURE_PATH; '
        "$acl.SetSecurityDescriptorSddlForm($env:FIXTURE_SDDL, 'Access'); "
        'Set-Acl -LiteralPath $env:FIXTURE_PATH -AclObject $acl'
    )
    environment = {**os.environ, 'FIXTURE_PATH': str(path), 'FIXTURE_SDDL': sddl}
    subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
        env=environment, check=True, capture_output=True,
    )


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


class WindowsKeyAccessPolicyTests(unittest.TestCase):
    """Synthetic Windows owner and DACL cases also run on POSIX."""

    def test_synthetic_owner_only_entries_are_private(self):
        security = acl.Security(USER_SID, [
            allowed(USER_SID), allowed(acl.SYSTEM), allowed(acl.ADMINISTRATORS),
            allowed(acl.OWNER_RIGHTS), allowed(acl.CREATOR_OWNER),
            acl.AccessEntry(OTHER_SID, allowed=False, mask=acl.GENERIC_ALL),
        ])
        self.assertTrue(acl.owner_is_trusted(security, USER_SID))
        self.assertEqual(acl.other_accounts(security, USER_SID, private=True), [])

    def test_synthetic_private_objects_refuse_any_other_account(self):
        cases = {
            'read-only group': (acl.Security(USER_SID, [allowed(USER_SID), allowed(USERS, READ_ONLY)]), [USERS]),
            'another account': (acl.Security(USER_SID, [allowed(OTHER_SID), allowed(OTHER_SID, READ_ONLY)]), [OTHER_SID]),
            'no DACL': (acl.Security(USER_SID, None), [acl.EVERYONE]),
            'unreadable entry type': (acl.Security(USER_SID, [allowed('')]), ['']),
        }
        for name, (security, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(acl.other_accounts(security, USER_SID, private=True), expected)

    def test_synthetic_key_folder_may_be_readable_but_not_changeable_by_others(self):
        readable = acl.Security(USER_SID, [allowed(USER_SID), allowed(USERS, READ_ONLY)])
        self.assertEqual(acl.other_accounts(readable, USER_SID, private=False), [])
        for mask in (acl.FILE_ADD_FILE, acl.FILE_DELETE_CHILD, acl.DELETE, acl.WRITE_DAC, acl.GENERIC_WRITE, MODIFY):
            with self.subTest(mask=hex(mask)):
                changeable = acl.Security(USER_SID, [allowed(USER_SID), allowed(AUTHENTICATED_USERS, mask)])
                self.assertEqual(acl.other_accounts(changeable, USER_SID, private=False), [AUTHENTICATED_USERS])

    def test_synthetic_owner_must_be_the_user_system_or_administrators(self):
        for owner in (USER_SID, acl.SYSTEM, acl.ADMINISTRATORS):
            with self.subTest(owner=owner):
                self.assertTrue(acl.owner_is_trusted(acl.Security(owner, []), USER_SID))
        for owner in (OTHER_SID, USERS, acl.OWNER_RIGHTS):
            with self.subTest(owner=owner):
                self.assertFalse(acl.owner_is_trusted(acl.Security(owner, []), USER_SID))

    def test_synthetic_messages_name_the_commands_that_fix_access(self):
        folder_path = Path('C:/Runner')
        folder = acl.access_message(folder_path, 'state folder', [USERS], USER_SID, directory=True, private=True)
        self.assertIn(
            f'running icacls "{folder_path}" /reset and then '
            f'icacls "{folder_path}" /inheritance:r /grant:r "*{USER_SID}:(OI)(CI)F" Then check',
            folder,
        )
        key_path = Path('C:/Runner/key')
        key = acl.access_message(key_path, 'key', [USERS], USER_SID, directory=False, private=True)
        self.assertIn(f'icacls "{key_path}" /inheritance:r /grant:r "*{USER_SID}:F" Then check', key)
        # icacls cannot name an entry for a deleted account, so no command lists the other accounts.
        self.assertNotIn(f'*{USERS}', folder)
        self.assertNotIn(f'*{USERS}', key)
        owner = acl.owner_message(Path('C:/Runner'), 'state folder', USERS, USER_SID)
        self.assertIn(f'/setowner *{USER_SID}', owner)


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
        original_create = getattr(runner.windows_key_acl, 'create_private_file', None)
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

        def competing_create(path):
            # Windows creates a new key through its owner-only DACL helper.
            nonlocal competed
            if not competed and Path(path).name == self.key_file.name:
                competed = True
                write_fixture(path)
                raise FileExistsError(errno.EEXIST, 'Disposable competing key creation', str(path))
            return original_create(path)

        if os.name == 'nt':
            competition = patch.object(runner.windows_key_acl, 'create_private_file', side_effect=competing_create)
        else:
            competition = patch.object(runner.os, 'open', side_effect=competing_open)
        with competition:
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
        original_create = runner.windows_key_acl.create_private_file
        attempted = False
        moved_state = self.base / 'moved-state'
        moved_base = self.base.with_name(self.base.name + '-moved')

        def pinned_create(path):
            nonlocal attempted
            if not attempted and Path(path).name == self.key_file.name:
                attempted = True
                for source, destination in ((self.state, moved_state), (self.base, moved_base)):
                    with self.subTest(source=source.name), self.assertRaises(PermissionError):
                        try:
                            source.rename(destination)
                        finally:
                            if destination.exists() and not source.exists():
                                destination.rename(source)
            return original_create(path)

        with patch.object(runner.windows_key_acl, 'create_private_file', side_effect=pinned_create):
            created = runner.load_or_create_key(self.key_file)
        self.assertTrue(attempted)
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), created)
        self.assertFalse(moved_state.exists())
        self.assertFalse(moved_base.exists())


@unittest.skipUnless(os.name == 'nt', 'Native Windows owner and DACL semantics are unavailable on POSIX')
class WindowsRunnerKeyAccessTests(TemporaryKeyTests):
    def entry_sids(self, path):
        return {entry.sid for entry in acl.read_security(path).entries}

    def run_suggested_fix(self, message):
        commands = message.split('Make it private to your account by running ')[1].split(' Then check')[0]
        # The commands are written for a Command Prompt or PowerShell; run them as typed.
        for command in commands.split(' and then '):
            subprocess.run(command, check=True, capture_output=True)

    def test_windows_new_state_and_key_are_private_under_a_shared_parent(self):
        shared = self.base / 'shared'
        shared.mkdir()
        grant_access(shared, f'*{AUTHENTICATED_USERS}:(OI)(CI)M')
        key_file = shared / 'new-parent' / 'state' / 'key'
        created = runner.load_or_create_key(key_file)
        self.assertEqual(runner.load_or_create_key(key_file), created)
        for path in (key_file.parent.parent, key_file.parent, key_file):
            with self.subTest(path=path.name):
                self.assertNotIn(AUTHENTICATED_USERS, self.entry_sids(path))

    def test_windows_shared_state_is_refused_unchanged_before_the_key_is_read(self):
        self.prepare_state()
        write_fixture(self.key_file)
        grant_access(self.state, f'*{USERS}:(OI)(CI)R')
        with patch.object(runner.os, 'fdopen', side_effect=AssertionError('A shared key must not be read')):
            with self.assertRaises(ValueError) as refused:
                runner.load_or_create_key(self.key_file)
        message = str(refused.exception)
        self.assertIn(f'state folder {self.state}', message)
        self.assertIn(USERS, self.entry_sids(self.state))
        self.assertEqual(self.key_file.read_text(encoding='utf-8'), FIXTURE_KEY)
        # The command in the message is enough to make the folder usable.
        self.run_suggested_fix(message)
        self.assertNotIn(USERS, self.entry_sids(self.state))
        self.assertEqual(runner.load_or_create_key(self.key_file), FIXTURE_KEY)

    def test_windows_key_shared_with_another_account_is_refused_before_read(self):
        self.prepare_state()
        write_fixture(self.key_file)
        grant_access(self.key_file, f'*{USERS}:R')
        with patch.object(runner.os, 'fdopen', side_effect=AssertionError('A shared key must not be read')):
            with self.assertRaises(ValueError) as refused:
                runner.load_or_create_key(self.key_file)
        message = str(refused.exception)
        self.assertIn(f'key {self.key_file}', message)
        self.assertNotIn('(OI)(CI)', message)
        self.assertIn(USERS, self.entry_sids(self.key_file))
        self.run_suggested_fix(message)
        self.assertEqual(runner.load_or_create_key(self.key_file), FIXTURE_KEY)

    def test_windows_key_folder_may_be_readable_but_not_changeable_by_others(self):
        custom = self.base / 'custom'
        custom.mkdir()
        grant_access(custom, f'*{USERS}:R')
        key_file = custom / 'provider-key'
        write_fixture(key_file)
        self.assertEqual(runner.load_or_create_key(key_file, state_dir=self.state), FIXTURE_KEY)
        grant_access(custom, f'*{AUTHENTICATED_USERS}:M')
        with self.assertRaises(ValueError) as refused:
            runner.load_or_create_key(key_file, state_dir=self.state)
        self.assertIn(f'key folder {custom} can be changed', str(refused.exception))
        self.assertEqual(key_file.read_text(encoding='utf-8'), FIXTURE_KEY)

    def test_windows_suggested_fix_works_when_entries_name_unknown_accounts(self):
        user_sid = acl.current_user_sid()
        # The state folder inherits an entry for an account Windows cannot
        # name, such as a deleted domain user, and also holds one itself.
        parent = self.base / 'parent'
        parent.mkdir()
        set_access_sddl(parent, f'D:P(A;OICI;FA;;;{user_sid})(A;OICI;FR;;;{INHERITED_UNKNOWN_SID})')
        state = parent / 'state'
        state.mkdir()
        set_access_sddl(state, f'D:(A;OICI;FA;;;{user_sid})(A;OICI;FR;;;{UNKNOWN_SID})')
        key_file = state / 'key'
        write_fixture(key_file)
        set_access_sddl(key_file, f'D:(A;;FA;;;{user_sid})(A;;FR;;;{UNKNOWN_SID})')
        self.assertEqual(self.entry_sids(state), {user_sid, UNKNOWN_SID, INHERITED_UNKNOWN_SID})

        with self.assertRaises(ValueError) as key_refused:
            acl.check_private(key_file, 'key', directory=False)
        self.run_suggested_fix(str(key_refused.exception))
        self.assertEqual(self.entry_sids(key_file), {user_sid})
        acl.check_private(key_file, 'key', directory=False)

        with self.assertRaises(ValueError) as state_refused:
            acl.check_private(state, 'state folder', directory=True)
        self.run_suggested_fix(str(state_refused.exception))
        self.assertEqual(self.entry_sids(state), {user_sid})
        acl.check_private(state, 'state folder', directory=True)
        self.assertEqual(runner.load_or_create_key(key_file), FIXTURE_KEY)


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
