"""Where a project folder may be: the path rules for one computer's platform.

Pure rules with no storage, shared by project folder choices and folder listings.
"""

import re
from pathlib import PurePosixPath, PureWindowsPath

from open_webui.utils.subscriptions.events import SubscriptionError

MAX_PATH_CHARS = 1024
WINDOWS_EXAMPLE = 'C:\\Users\\you\\code\\project'
POSIX_EXAMPLE = '/home/you/code/project'
DRIVE_PREFIX = re.compile(r'^[A-Za-z]:')


def _not_absolute_message(platform: str | None) -> str:
    if platform == 'win32':
        return f'Enter a full path, like {WINDOWS_EXAMPLE}.'
    if platform:
        return f'Enter a full path, like {POSIX_EXAMPLE}.'
    return f'Enter a full path, like {WINDOWS_EXAMPLE} or {POSIX_EXAMPLE}.'


def _has_dot_parts(path: str) -> bool:
    return any(part in ('.', '..') for part in re.split(r'[\\/]', path))


def _clean_windows_path(path: str, platform: str | None, allow_drive_root: bool) -> str:
    # Windows reads / as \, so '/\server' is a network path too.
    path = path.replace('/', '\\')
    if path.startswith('\\\\'):
        raise SubscriptionError("Network paths aren't supported. Choose a folder on one of this computer's drives.")
    if _has_dot_parts(path):
        raise SubscriptionError('Use the folder\'s full path without . or .. parts.')
    # Windows drops a trailing dot or space, so 'C:\ .' would mean the whole drive.
    if any(part != part.rstrip('. ') for part in path.split('\\')):
        raise SubscriptionError("Folder names can't end with a dot or a space.")
    windows_path = PureWindowsPath(path)
    if not windows_path.drive or not windows_path.root:
        raise SubscriptionError(_not_absolute_message(platform))
    if not allow_drive_root and windows_path == PureWindowsPath(windows_path.anchor):
        raise SubscriptionError('Choose a folder, not a whole drive.')
    return str(windows_path)


def _clean_posix_path(path: str, platform: str | None, allow_drive_root: bool) -> str:
    if not path.startswith('/'):
        raise SubscriptionError(_not_absolute_message(platform))
    if _has_dot_parts(path):
        raise SubscriptionError('Use the folder\'s full path without . or .. parts.')
    posix_path = PurePosixPath(path)
    if str(posix_path) in ('/', '//'):
        if allow_drive_root:
            return '/'
        raise SubscriptionError('Choose a folder, not a whole drive.')
    return str(posix_path)


def clean_project_path(path: str, platform: str | None, allow_drive_root: bool = False) -> str:
    """The path in its tidy form for the computer's platform, or a plain reason it can't be used.

    platform is the computer's sys.platform, or None when Buddy doesn't know it yet.
    A whole drive is refused unless allow_drive_root, which browsing uses.
    """
    text = (path or '').strip()
    if not text:
        raise SubscriptionError("Enter the folder's full path.")
    if any(ord(character) < 32 for character in text):
        raise SubscriptionError("That path has characters Buddy can't use.")
    if len(text) > MAX_PATH_CHARS:
        raise SubscriptionError('That path is too long.')
    if platform == 'win32':
        return _clean_windows_path(text, platform, allow_drive_root)
    if platform:
        return _clean_posix_path(text, platform, allow_drive_root)
    # Unknown platform: a drive letter or a backslash at the start means Windows.
    if DRIVE_PREFIX.match(text) or '\\' in text[:2] or text.startswith('//'):
        return _clean_windows_path(text, platform, allow_drive_root)
    return _clean_posix_path(text, platform, allow_drive_root)


def is_windows_path(path: str, platform: str | None) -> bool:
    if platform == 'win32':
        return True
    if platform:
        return False
    return bool(DRIVE_PREFIX.match(path))
