"""Folder listings and suggestions from a computer, checked before Buddy uses them.

Anyone can run a modified Runner, so its replies are untrusted: each name is
checked, every child path is built here from the folder that was listed, and
suggested paths go through the same rules as typed ones.
"""

from pathlib import PurePosixPath, PureWindowsPath

from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.project_paths import clean_project_path, is_windows_path

MAX_LISTED_FOLDERS = 1000
MAX_ROOTS = 64
MAX_SUGGESTIONS = 50
MAX_NAME_CHARS = 255
ROOT_KINDS = ('drive', 'remote_drive', 'removable', 'place')
SUGGESTION_SOURCES = ('claude', 'codex')
UNUSABLE_REPLY = "The computer sent a folder list Buddy can't use. Update the Buddy app on it."


def clean_browse_path(path, platform: str | None) -> str:
    """A folder to list: like a project folder path, but a whole drive is allowed."""
    if not isinstance(path, str):
        raise SubscriptionError("Enter the folder's full path.")
    return clean_project_path(path, platform, allow_drive_root=True)


def _usable_name(name) -> bool:
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME_CHARS:
        return False
    if name in ('.', '..') or '/' in name or '\\' in name:
        return False
    return not any(ord(character) < 32 for character in name)


def _child_path(folder: str, name: str, platform: str | None) -> str:
    if is_windows_path(folder, platform):
        return str(PureWindowsPath(folder) / name)
    return str(PurePosixPath(folder) / name)


def _parent_path(folder: str, platform: str | None) -> str | None:
    """The folder above, or None at a drive or at /."""
    if is_windows_path(folder, platform):
        path = PureWindowsPath(folder)
    else:
        path = PurePosixPath(folder)
    if path.parent == path:
        return None
    return str(path.parent)


def _selectable(path: str, platform: str | None) -> bool:
    try:
        clean_project_path(path, platform)
    except SubscriptionError:
        return False
    return True


def checked_listing(reply, listed_path: str, platform: str | None) -> dict:
    """The folders inside listed_path, with paths built here, never taken from the reply."""
    if not isinstance(reply, dict) or not isinstance(reply.get('entries'), list):
        raise SubscriptionError(UNUSABLE_REPLY)
    entries = []
    for entry in reply['entries'][:MAX_LISTED_FOLDERS]:
        if not isinstance(entry, dict) or not _usable_name(entry.get('name')):
            continue
        path = _child_path(listed_path, entry['name'], platform)
        entries.append({
            'name': entry['name'],
            'path': path,
            'hidden': entry.get('hidden') is True,
            'link': entry.get('link') is True,
            'selectable': _selectable(path, platform),
        })
    return {
        'path': listed_path,
        'parent': _parent_path(listed_path, platform),
        'roots': [],
        'entries': entries,
        'truncated': reply.get('truncated') is True or len(reply['entries']) > MAX_LISTED_FOLDERS,
    }


def checked_roots(reply, platform: str | None) -> dict:
    """Where browsing starts. These paths come from the computer, so each must be a clean folder path."""
    if not isinstance(reply, dict) or not isinstance(reply.get('roots'), list):
        raise SubscriptionError(UNUSABLE_REPLY)
    roots = []
    for root in reply['roots'][:MAX_ROOTS]:
        if not isinstance(root, dict) or root.get('kind') not in ROOT_KINDS:
            continue
        name = root.get('name')
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or any(ord(character) < 32 for character in name):
            continue
        try:
            path = clean_browse_path(root.get('path'), platform)
        except SubscriptionError:
            continue
        roots.append({'name': name, 'path': path, 'kind': root['kind']})
    return {'path': '', 'parent': None, 'roots': roots, 'entries': [], 'truncated': False}


def _sources(value) -> list[str]:
    if not isinstance(value, list):
        return []
    sources = []
    for source in value:
        if source in SUGGESTION_SOURCES and source not in sources:
            sources.append(source)
    return sources


def _last_used(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def checked_suggestions(reply, platform: str | None) -> list[dict]:
    """Folders Claude Code and Codex worked in, each a clean project folder path."""
    if not isinstance(reply, dict) or not isinstance(reply.get('folders'), list):
        raise SubscriptionError(UNUSABLE_REPLY)
    suggestions = []
    seen = set()
    for folder in reply['folders'][:MAX_SUGGESTIONS]:
        if not isinstance(folder, dict):
            continue
        sources = _sources(folder.get('sources'))
        if not sources:
            continue
        try:
            path = clean_project_path(folder.get('path') or '', platform)
        except SubscriptionError:
            continue
        key = path
        if is_windows_path(path, platform):
            key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append({'path': path, 'sources': sources, 'last_used': _last_used(folder.get('last_used'))})
    return suggestions
