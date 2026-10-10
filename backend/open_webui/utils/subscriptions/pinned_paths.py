"""Native handles that keep granted path traversal attached to real directories.

POSIX descendants open relative to directory descriptors. Windows holds every
ancestor without delete/write sharing, so names and reparse metadata cannot be
swapped while a file, listing, or command startup uses the approved path.
"""

import os
import errno
import stat
import sys
from pathlib import Path


class PinnedPathError(PermissionError):
    """The requested path cannot be safely pinned inside a directory grant."""


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _check_directory(value: os.stat_result) -> None:
    if not stat.S_ISDIR(value.st_mode):
        raise PinnedPathError('The requested path must be a real directory.')


def _check_regular_file(value: os.stat_result) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise PinnedPathError('The requested path must be an existing regular file.')
    if value.st_nlink > 1:
        raise PinnedPathError('Hard-linked files cannot be accessed through a directory grant.')


def _open_at(component, flags, parent=None):
    try:
        return os.open(component, flags, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise PinnedPathError('Symbolic links and non-directory ancestors cannot be followed.') from error
        raise


if sys.platform == 'win32':
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileInformation(ctypes.Structure):
        _fields_ = [
            ('attributes', wintypes.DWORD),
            ('creation_time', wintypes.FILETIME),
            ('access_time', wintypes.FILETIME),
            ('write_time', wintypes.FILETIME),
            ('volume_serial', wintypes.DWORD),
            ('size_high', wintypes.DWORD),
            ('size_low', wintypes.DWORD),
            ('links', wintypes.DWORD),
            ('index_high', wintypes.DWORD),
            ('index_low', wintypes.DWORD),
        ]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel.GetFileType.argtypes = [wintypes.HANDLE]
    _kernel.GetFileType.restype = wintypes.DWORD
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _READ_ATTRIBUTES = 0x80
    _LIST_DIRECTORY = 1
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _SHARE_READ = 1
    _SHARE_WRITE = 2
    _OPEN_EXISTING = 3
    _BACKUP_SEMANTICS = 0x02000000
    _OPEN_REPARSE_POINT = 0x00200000
    _REPARSE_POINT = 0x400
    _DIRECTORY = 0x10
    _FILE_TYPE_DISK = 1

    def _normal_windows_path(value: str) -> str:
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return os.path.normcase(os.path.normpath(value))

    def _final_windows_path(handle: int) -> str:
        length = _kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not length:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = _kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not copied or copied >= len(buffer):
            raise PinnedPathError('The host could not verify the opened directory or file path.')
        return _normal_windows_path(buffer.value)

    def _windows_information(handle: int, expected: Path, directory: bool):
        information = _FileInformation()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        if _kernel.GetFileType(handle) != _FILE_TYPE_DISK or information.attributes & _REPARSE_POINT:
            raise PinnedPathError('Symbolic links, junctions, and device files cannot be followed.')
        if bool(information.attributes & _DIRECTORY) != directory:
            raise PinnedPathError('The requested path has the wrong file type.')
        if _final_windows_path(handle) != _normal_windows_path(str(expected)):
            raise PinnedPathError('The opened path does not match the approved directory grant.')
        if not directory and information.links > 1:
            raise PinnedPathError('Hard-linked files cannot be accessed through a directory grant.')
        return information


class PinnedPath:
    """Own every handle until close(); file descriptors never outlive their guard."""

    def __init__(
        self,
        root: Path,
        relative: str = '',
        *,
        expected_root: tuple[int, int] | None = None,
        file: bool = False,
        writable: bool = False,
    ):
        self.root = Path(os.path.abspath(root))
        self.relative = relative
        self.path = self.root.joinpath(*relative.split('/')) if relative else self.root
        self.descriptor: int | None = None
        self.directory_fd: int | None = None
        self.stat: os.stat_result | None = None
        self.root_stat: os.stat_result | None = None
        self._descriptors: list[int] = []
        self._handles: list[int] = []
        self._closed = False
        try:
            if not self.root.is_absolute():
                raise PinnedPathError('A grant must name an absolute directory.')
            components = relative.split('/') if relative else []
            if any(not component or component in ('.', '..') or '/' in component or '\\' in component for component in components):
                raise PinnedPathError('Only safe relative paths can be pinned.')
            if file and not components:
                raise PinnedPathError('The requested path must be an existing regular file.')
            if sys.platform == 'win32':
                self._open_windows(components, expected_root, file, writable)
            else:
                self._open_posix(components, expected_root, file, writable)
        except BaseException:
            self.close()
            raise

    def _check_root_identity(self, expected_root) -> None:
        _check_directory(self.root_stat)
        if expected_root is not None and _identity(self.root_stat) != expected_root:
            raise PinnedPathError('The granted directory changed. Grant it again on the host.')

    def _open_posix(self, components, expected_root, file: bool, writable: bool) -> None:
        if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
            raise PinnedPathError('This host does not support safe directory-handle traversal.')
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
        descriptor = _open_at(self.root.anchor, directory_flags)
        self._descriptors.append(descriptor)
        for component in self.root.parts[1:]:
            descriptor = _open_at(component, directory_flags, descriptor)
            self._descriptors.append(descriptor)
            _check_directory(os.fstat(descriptor))
        self.root_stat = os.fstat(descriptor)
        self._check_root_identity(expected_root)
        directories = components[:-1] if file else components
        for component in directories:
            descriptor = _open_at(component, directory_flags, descriptor)
            self._descriptors.append(descriptor)
            _check_directory(os.fstat(descriptor))
        if file:
            flags = os.O_RDWR if writable else os.O_RDONLY
            flags |= os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
            descriptor = _open_at(components[-1], flags, descriptor)
            self._descriptors.append(descriptor)
            self.stat = os.fstat(descriptor)
            _check_regular_file(self.stat)
        else:
            self.stat = os.fstat(descriptor)
            _check_directory(self.stat)
            self.directory_fd = descriptor
        self.descriptor = descriptor

    def _windows_open(self, absolute: Path, *, directory: bool, writable: bool = False) -> int:
        # Attribute-only opens do not participate in Windows data-sharing
        # checks. LIST_DIRECTORY makes the no-write/no-delete sharing effective.
        access = _READ_ATTRIBUTES | _LIST_DIRECTORY if directory else _GENERIC_READ
        if writable:
            access |= _GENERIC_WRITE
        # Directories deny both rename/deletion and in-place reparse writes.
        sharing = _SHARE_READ if directory else _SHARE_READ | _SHARE_WRITE
        handle = _kernel.CreateFileW(
            str(absolute), access, sharing, None, _OPEN_EXISTING,
            _OPEN_REPARSE_POINT | _BACKUP_SEMANTICS, None,
        )
        if handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handles.append(handle)
        _windows_information(handle, absolute, directory)
        return handle

    def _open_windows(self, components, expected_root, file: bool, writable: bool) -> None:
        current = Path(self.root.anchor)
        self._windows_open(current, directory=True)
        for component in self.root.parts[1:]:
            current = current / component
            self._windows_open(current, directory=True)
        # Paths cannot be redirected while the complete readonly ancestor chain
        # is held. Use Python's native stat representation for grant identities.
        self.root_stat = os.stat(current, follow_symlinks=False)
        self._check_root_identity(expected_root)
        directories = components[:-1] if file else components
        for component in directories:
            current = current / component
            self._windows_open(current, directory=True)
        if file:
            current = current / components[-1]
            handle = self._windows_open(current, directory=False, writable=writable)
            flags = getattr(os, 'O_BINARY', 0) | (os.O_RDWR if writable else os.O_RDONLY)
            descriptor = msvcrt.open_osfhandle(handle, flags)
            # open_osfhandle transfers ownership; never also CloseHandle it.
            self._handles.pop()
            self._descriptors.append(descriptor)
            os.set_inheritable(descriptor, False)
            self.descriptor = descriptor
            self.stat = os.fstat(descriptor)
            _check_regular_file(self.stat)
        else:
            self.stat = os.stat(current, follow_symlinks=False)
            _check_directory(self.stat)

    def scandir(self):
        if self._closed or self.directory_fd is None and sys.platform != 'win32':
            raise PinnedPathError('The pinned directory is closed or unavailable.')
        target = self.path if sys.platform == 'win32' else self.directory_fd
        return os.scandir(target)

    def current_stat(self) -> os.stat_result:
        if self._closed:
            raise PinnedPathError('The pinned file or directory is closed.')
        if self.descriptor is not None:
            value = os.fstat(self.descriptor)
        else:
            _windows_information(self._handles[-1], self.path, directory=True)
            value = os.stat(self.path, follow_symlinks=False)
        if stat.S_ISREG(value.st_mode):
            _check_regular_file(value)
        return value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in reversed(self._descriptors):
            os.close(descriptor)
        self._descriptors.clear()
        if sys.platform == 'win32':
            for handle in reversed(self._handles):
                _kernel.CloseHandle(handle)
        self._handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
