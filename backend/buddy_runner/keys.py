"""The Runner's private files: its key today, and its server connection.

These files let whoever reads them run commands as this OS user, so they are
created owner-only and refused if anything about their location is unsafe:
links, shared folders, or other owners. Paths are pinned while they are
checked, so a folder cannot be swapped between the check and the read.
"""

import errno
import os
import secrets
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from buddy_runner import windows_key_acl
from buddy_runner.pinned_paths import PinnedPath, PinnedPathError


def _validate_key_directory(info, *, owner_id=None, private=False) -> None:
    if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Runner key directories must be real directories, without symbolic links or junctions.')
    if owner_id is None:
        # Windows access is controlled by its owner and DACL, which
        # windows_key_acl checks instead of POSIX mode bits.
        return
    if private:
        if info.st_uid != owner_id:
            raise ValueError('The Runner state directory must be owned by the current OS user.')
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('The Runner state directory must have mode 0700. Verify its ownership, then use chmod 700 before starting.')
        return
    if info.st_uid not in (owner_id, 0):
        raise ValueError('Runner key ancestors must be owned by the current OS user or root.')
    shared_writes = info.st_mode & 0o022
    sticky = info.st_mode & stat.S_ISVTX
    if shared_writes and not sticky:
        raise ValueError('Runner key ancestors must not allow group or other users to replace paths. Choose a protected key location.')


def _validate_key_file(info, *, owner_id=None) -> None:
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('The Runner key must be a regular file, without symbolic links or junctions.')
    if info.st_nlink != 1:
        raise ValueError('The Runner key must not have hard links.')
    if owner_id is not None:
        if info.st_uid != owner_id:
            raise ValueError('The Runner key must be owned by the current OS user.')
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('The Runner key must have mode 0600. Verify its ownership, then use chmod 600 before starting.')


@contextmanager
def _open_posix_key_directory(path: Path, *, owner_id: int, private: bool):
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ValueError('This host cannot safely open Runner key directories without following links.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    with ExitStack() as resources:
        descriptor = os.open(path.anchor, flags)
        resources.callback(os.close, descriptor)
        _validate_key_directory(os.fstat(descriptor), owner_id=owner_id)
        components = path.parts[1:]
        for index, component in enumerate(components):
            created = False
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
            resources.callback(os.close, child)
            info = os.fstat(child)
            if created:
                # Only adjust a new, descriptor-owned directory. Existing
                # credentials and directories are never silently repaired.
                if info.st_uid != owner_id or not stat.S_ISDIR(info.st_mode):
                    raise ValueError('The new Runner directory could not be verified as owned by the current OS user.')
                os.fchmod(child, 0o700)
                info = os.fstat(child)
            _validate_key_directory(
                info, owner_id=owner_id, private=private and index == len(components) - 1
            )
            descriptor = child
        if not components and private:
            _validate_key_directory(os.fstat(descriptor), owner_id=owner_id, private=True)
        yield descriptor


@contextmanager
def _pin_windows_key_directory(path: Path, *, private: bool):
    missing = []
    existing = path
    while True:
        try:
            existing.lstat()
            break
        except FileNotFoundError:
            if existing.parent == existing:
                raise
            missing.append(existing.name)
            existing = existing.parent
    with ExitStack() as resources:
        pinned = resources.enter_context(PinnedPath(existing))
        for name in reversed(missing):
            existing = existing / name
            try:
                # Owner-only from the start, whatever the parent allows.
                windows_key_acl.create_private_directory(existing)
            except FileExistsError:
                pass
            # Keep the existing ancestors pinned while each child is created
            # and opened. Windows pins refuse reparse points and path swaps.
            pinned = resources.enter_context(PinnedPath(existing))
        _validate_key_directory(pinned.stat)
        # The pin keeps this folder in place while its access is read.
        if private:
            windows_key_acl.check_private(path, 'state folder', directory=True)
        else:
            windows_key_acl.check_key_folder(path)
        yield pinned


def _read_key_descriptor(descriptor: int, *, owner_id=None) -> str:
    _validate_key_file(os.fstat(descriptor), owner_id=owner_id)
    with os.fdopen(os.dup(descriptor), 'r', encoding='utf-8') as handle:
        content = handle.read(4097)
    _validate_key_file(os.fstat(descriptor), owner_id=owner_id)
    if len(content) > 4096:
        raise ValueError('The Runner key file is too large. Choose a key of at most 4096 characters.')
    key = content.strip()
    if not key:
        raise ValueError('The Runner key file is empty. Create a new key before starting.')
    return key


def _write_new_key(descriptor: int, *, owner_id=None) -> str:
    info = os.fstat(descriptor)
    _validate_key_file(info)
    if owner_id is not None:
        if info.st_uid != owner_id:
            raise ValueError('The new Runner key could not be verified as owned by the current OS user.')
        os.fchmod(descriptor, 0o600)
    _validate_key_file(os.fstat(descriptor), owner_id=owner_id)
    key = secrets.token_urlsafe(32)
    with os.fdopen(os.dup(descriptor), 'w', encoding='utf-8') as handle:
        handle.write(key)
        handle.flush()
        os.fsync(handle.fileno())
    _validate_key_file(os.fstat(descriptor), owner_id=owner_id)
    return key


def load_or_create_key(key_file: Path, *, state_dir: Path | None = None) -> str:
    key_file = Path(os.path.abspath(key_file))
    state_dir = Path(os.path.abspath(state_dir if state_dir is not None else key_file.parent))
    try:
        with ExitStack() as resources:
            if os.name == 'posix':
                owner_id = os.geteuid()
                state = resources.enter_context(_open_posix_key_directory(state_dir, owner_id=owner_id, private=True))
                parent = state
                if key_file.parent != state_dir:
                    parent = resources.enter_context(_open_posix_key_directory(key_file.parent, owner_id=owner_id, private=False))
                flags = os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
                try:
                    descriptor = os.open(key_file.name, os.O_RDONLY | flags, dir_fd=parent)
                except FileNotFoundError:
                    try:
                        descriptor = os.open(key_file.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | flags, 0o600, dir_fd=parent)
                    except FileExistsError:
                        descriptor = os.open(key_file.name, os.O_RDONLY | flags, dir_fd=parent)
                    else:
                        resources.callback(os.close, descriptor)
                        return _write_new_key(descriptor, owner_id=owner_id)
                resources.callback(os.close, descriptor)
                return _read_key_descriptor(descriptor, owner_id=owner_id)
            resources.enter_context(_pin_windows_key_directory(state_dir, private=True))
            if key_file.parent != state_dir:
                resources.enter_context(_pin_windows_key_directory(key_file.parent, private=False))
            try:
                pinned = resources.enter_context(PinnedPath(key_file.parent, key_file.name, file=True))
            except FileNotFoundError:
                try:
                    descriptor = windows_key_acl.create_private_file(key_file)
                except FileExistsError:
                    pinned = resources.enter_context(PinnedPath(key_file.parent, key_file.name, file=True))
                else:
                    resources.callback(os.close, descriptor)
                    windows_key_acl.check_private(key_file, 'key', directory=False, descriptor=descriptor)
                    return _write_new_key(descriptor)
            windows_key_acl.check_private(key_file, 'key', directory=False, descriptor=pinned.descriptor)
            return _read_key_descriptor(pinned.descriptor)
    except PinnedPathError as error:
        raise ValueError(f'The Runner key path is unsafe: {error}') from error
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError('Runner key paths must not contain symbolic links or non-directory ancestors.') from error
        raise
