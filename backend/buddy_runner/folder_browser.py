"""Folder names on this computer, for choosing where a chat works.

Lists one level of folders: names only, never files, sizes, times or
contents. Errors carry fixed text, so an operating system message never
repeats a path back to Buddy.
"""

import os
import stat
import sys
import time
from pathlib import Path

MAX_SCANNED_ENTRIES = 5000
MAX_LISTED_FOLDERS = 1000
MAX_PATH_CHARS = 1024

_WINDOWS = sys.platform == 'win32'
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

# GetDriveTypeW answers.
_DRIVE_NO_ROOT_DIR = 1
_DRIVE_REMOVABLE = 2
_DRIVE_REMOTE = 4
_DRIVE_CDROM = 5


class FolderListingError(ValueError):
    """A listing Buddy can't have, with a message that names no path."""


def _drive_kind(drive_type: int) -> str:
    if drive_type == _DRIVE_REMOTE:
        return 'remote_drive'
    if drive_type in (_DRIVE_REMOVABLE, _DRIVE_CDROM):
        return 'removable'
    return 'drive'


def _windows_drives() -> list[dict]:
    """Drive letters, without touching the drives: a network drive can hang when opened."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f'{chr(ord("A") + index)}:\\'
        drive_type = kernel32.GetDriveTypeW(root)
        if drive_type == _DRIVE_NO_ROOT_DIR:
            continue
        drives.append({'name': root[:2], 'path': root, 'kind': _drive_kind(drive_type)})
    return drives


def _windows_places() -> list[dict]:
    home = Path.home()
    candidates = [
        ('Home', home),
        ('Desktop', home / 'Desktop'),
        ('Documents', home / 'Documents'),
        ('Downloads', home / 'Downloads'),
    ]
    onedrive = os.environ.get('OneDrive')
    if onedrive:
        candidates.append(('OneDrive', Path(onedrive)))
    places = []
    for name, path in candidates:
        if path.is_dir():
            places.append({'name': name, 'path': str(path), 'kind': 'place'})
    return places


def list_roots() -> list[dict]:
    """Where browsing starts: drives and common places on Windows, / and home elsewhere."""
    if _WINDOWS:
        return _windows_drives() + _windows_places()
    return [
        {'name': '/', 'path': '/', 'kind': 'drive'},
        {'name': 'Home', 'path': str(Path.home()), 'kind': 'place'},
    ]


def _checked_path(path) -> str:
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
        raise FolderListingError('Choose a folder by its full path.')
    if any(ord(character) < 32 for character in path):
        raise FolderListingError("That path has characters Buddy can't use.")
    if _WINDOWS:
        normalized = path.replace('/', '\\')
        # Network shares and device paths (\\server, \\?\, \\.\) are never listed.
        if normalized.startswith('\\\\'):
            raise FolderListingError("Network paths aren't listed. Choose a folder on one of this computer's drives.")
        if not os.path.isabs(normalized) or len(normalized) < 3 or normalized[1] != ':':
            raise FolderListingError('Choose a folder by its full path.')
        return normalized
    if not path.startswith('/'):
        raise FolderListingError('Choose a folder by its full path.')
    return path


def _is_hidden(name: str, details: os.stat_result) -> bool:
    if _WINDOWS:
        attributes = getattr(details, 'st_file_attributes', 0)
        return bool(attributes & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM))
    return name.startswith('.')


def _is_link(details: os.stat_result) -> bool:
    """Symlinks and junctions. OneDrive placeholders are reparse points too, but not links."""
    if stat.S_ISLNK(details.st_mode):
        return True
    return getattr(details, 'st_reparse_tag', 0) == _IO_REPARSE_TAG_MOUNT_POINT


def _is_folder(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=True)
    except OSError:
        return False


def list_folders(path, show_hidden: bool = False, prefix: str = '', deadline: float | None = None) -> dict:
    """The folders directly inside `path`, sorted by name.

    Stops at MAX_SCANNED_ENTRIES entries or at `deadline` (time.monotonic()),
    returning what it found with truncated set.
    """
    folder = _checked_path(path)
    wanted = (prefix or '').casefold()
    entries = []
    truncated = False
    scanned = 0
    try:
        with os.scandir(folder) as found:
            for entry in found:
                scanned += 1
                if scanned > MAX_SCANNED_ENTRIES or (deadline is not None and time.monotonic() > deadline):
                    truncated = True
                    break
                if wanted and not entry.name.casefold().startswith(wanted):
                    continue
                if not _is_folder(entry):
                    continue
                try:
                    details = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                hidden = _is_hidden(entry.name, details)
                if hidden and not show_hidden:
                    continue
                entries.append({'name': entry.name, 'hidden': hidden, 'link': _is_link(details)})
    except (FileNotFoundError, NotADirectoryError):
        raise FolderListingError("That folder doesn't exist.") from None
    except PermissionError:
        raise FolderListingError("This computer didn't let Buddy open that folder.") from None
    except OSError:
        raise FolderListingError("Buddy couldn't list that folder.") from None
    entries.sort(key=lambda listed: listed['name'].casefold())
    if len(entries) > MAX_LISTED_FOLDERS:
        entries = entries[:MAX_LISTED_FOLDERS]
        truncated = True
    return {'path': folder, 'entries': entries, 'truncated': truncated}
