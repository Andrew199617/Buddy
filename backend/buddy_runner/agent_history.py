"""Folders Claude Code and Codex have worked in on this computer, to suggest as project folders.

Only session files under fixed patterns are opened:
  Claude Code: <claude home>/projects/<project>/*.jsonl (or */subagents/*.jsonl)
  Codex:       <codex home>/sessions/YYYY/MM/DD/rollout-*.jsonl and archived_sessions/rollout-*.jsonl
Nothing at the top level of either home is ever opened, so their credential
files are never read. From each file only a top-level "cwd" string is taken,
and only when it names a folder that exists here. Transcript text, file names
and session ids never leave this module.
"""

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

MAX_SUGGESTIONS = 50
MAX_CLAUDE_PROJECTS = 200
CLAUDE_FILES_PER_PROJECT = 2
CLAUDE_MAX_LINES = 8
CLAUDE_MAX_BYTES = 1024 * 1024
MAX_CODEX_FILES = 1000
CODEX_MAX_BYTES = 256 * 1024
MAX_PATH_CHARS = 1024
SECONDS_PER_DAY = 86400

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class _Skipped(Exception):
    """A file that is not read: a link, a hard link, or one that changed while opening."""


def default_homes() -> tuple[Path, Path]:
    """Where Claude Code and Codex keep their history, as the CLIs themselves decide."""
    claude_home = os.environ.get('CLAUDE_CONFIG_DIR') or str(Path.home() / '.claude')
    codex_home = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    return Path(claude_home), Path(codex_home)


def default_exclusions(state_dir: Path) -> list[str]:
    """Folders that are Buddy's or the system's own, never a person's project."""
    excluded = [str(state_dir), tempfile.gettempdir()]
    appdata = os.environ.get('APPDATA')
    if sys.platform == 'win32' and appdata:
        # The Claude desktop app's scratch folders.
        excluded.append(str(Path(appdata) / 'Claude' / 'scratch-workspaces'))
    return excluded


def _is_link(details: os.stat_result) -> bool:
    if stat.S_ISLNK(details.st_mode):
        return True
    return bool(getattr(details, 'st_file_attributes', 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _open_under(home: Path, path: Path):
    """Opens a file below `home` for reading, refusing links on the way and hard links.

    The opened file must be the one that was checked, so a file swapped in
    meanwhile is not read.
    """
    current = home
    details = None
    for part in path.relative_to(home).parts:
        current = current / part
        details = os.lstat(current)
        if _is_link(details):
            raise _Skipped()
    if details is None or not stat.S_ISREG(details.st_mode) or details.st_nlink > 1:
        raise _Skipped()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
            raise _Skipped()
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def _claude_cwd(handle) -> str | None:
    """The first top-level cwd in the first few lines of a Claude Code session."""
    budget = CLAUDE_MAX_BYTES
    for _ in range(CLAUDE_MAX_LINES):
        line = handle.readline(budget + 1)
        if not line or len(line) > budget:
            return None
        budget -= len(line)
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get('cwd'), str):
            return entry['cwd']
    return None


def _codex_cwd(handle) -> str | None:
    """The cwd in a Codex session's first line, which describes the session."""
    line = handle.readline(CODEX_MAX_BYTES + 1)
    if not line or len(line) > CODEX_MAX_BYTES:
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get('type') != 'session_meta':
        return None
    payload = entry.get('payload')
    if isinstance(payload, dict) and isinstance(payload.get('cwd'), str):
        return payload['cwd']
    return None


def _entries(folder: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(folder) as found:
            return list(found)
    except OSError:
        return []


def _modified(entry: os.DirEntry) -> float:
    try:
        return entry.stat(follow_symlinks=False).st_mtime
    except OSError:
        return 0.0


def _newest(entries: list[os.DirEntry], limit: int) -> list[os.DirEntry]:
    return sorted(entries, key=_modified, reverse=True)[:limit]


def _claude_session_files(claude_home: Path) -> list[Path]:
    projects = [entry for entry in _entries(claude_home / 'projects') if entry.is_dir(follow_symlinks=False)]
    files = []
    for project in _newest(projects, MAX_CLAUDE_PROJECTS):
        sessions = [
            entry
            for entry in _entries(Path(project.path))
            if entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False)
        ]
        if not sessions:
            # A project whose only sessions are a subagent's still names its folder.
            for child in _entries(Path(project.path)):
                if child.is_dir(follow_symlinks=False):
                    sessions += [
                        entry
                        for entry in _entries(Path(child.path) / 'subagents')
                        if entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False)
                    ]
        files += [Path(entry.path) for entry in _newest(sessions, CLAUDE_FILES_PER_PROJECT)]
    return files


def _numbered_folders(folder: Path) -> list[Path]:
    """Year, month or day folders, newest first."""
    named = [entry for entry in _entries(folder) if entry.name.isdigit() and entry.is_dir(follow_symlinks=False)]
    return [Path(entry.path) for entry in sorted(named, key=lambda entry: entry.name, reverse=True)]


def _is_rollout(entry: os.DirEntry) -> bool:
    return entry.name.startswith('rollout-') and entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False)


def _codex_session_files(codex_home: Path) -> list[Path]:
    files = []
    for year in _numbered_folders(codex_home / 'sessions'):
        for month in _numbered_folders(year):
            for day in _numbered_folders(month):
                rollouts = [entry for entry in _entries(day) if _is_rollout(entry)]
                files += [Path(entry.path) for entry in _newest(rollouts, MAX_CODEX_FILES)]
                if len(files) >= MAX_CODEX_FILES:
                    return files[:MAX_CODEX_FILES]
    archived = [entry for entry in _entries(codex_home / 'archived_sessions') if _is_rollout(entry)]
    files += [Path(entry.path) for entry in _newest(archived, MAX_CODEX_FILES)]
    return files[:MAX_CODEX_FILES]


def _is_excluded(path: str, exclusions: list[str]) -> bool:
    candidate = os.path.normcase(os.path.normpath(path))
    for excluded in exclusions:
        root = os.path.normcase(os.path.normpath(excluded))
        if candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def _usable_folder(cwd: str, exclusions: list[str]) -> str | None:
    if not cwd or len(cwd) > MAX_PATH_CHARS or any(ord(character) < 32 for character in cwd):
        return None
    if not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return None
    if _is_excluded(cwd, exclusions):
        return None
    return os.path.normpath(cwd)


class _Collected:
    def __init__(self, exclusions: list[str]):
        self.exclusions = exclusions
        self.folders: dict[str, dict] = {}
        self.skipped = 0

    def add(self, cwd: str | None, source: str, modified: float) -> None:
        if cwd is None:
            return
        folder = _usable_folder(cwd, self.exclusions)
        if folder is None:
            return
        day = int(modified // SECONDS_PER_DAY * SECONDS_PER_DAY)
        key = os.path.normcase(folder)
        found = self.folders.get(key)
        if found is None:
            self.folders[key] = {'path': folder, 'sources': [source], 'last_used': day}
            return
        if source not in found['sources']:
            found['sources'].append(source)
        found['last_used'] = max(found['last_used'], day)


def _read_sessions(home: Path, files: list[Path], read_cwd, source: str, collected: _Collected, deadline: float) -> bool:
    """Adds each session's folder; returns False when the deadline ran out first."""
    for path in files:
        if time.monotonic() > deadline:
            return False
        try:
            with _open_under(home, path) as handle:
                cwd = read_cwd(handle)
            modified = os.lstat(path).st_mtime
        except (_Skipped, OSError, ValueError):
            collected.skipped += 1
            continue
        collected.add(cwd, source, modified)
    return True


def suggest_folders(claude_home: Path, codex_home: Path, exclusions: list[str], deadline: float) -> dict:
    """Folders Claude Code and Codex worked in, newest first, at most MAX_SUGGESTIONS.

    `deadline` is a time.monotonic() value; work stops there with truncated set.
    """
    collected = _Collected(exclusions)
    finished = _read_sessions(claude_home, _claude_session_files(claude_home), _claude_cwd, 'claude', collected, deadline)
    if finished:
        codex_files = _codex_session_files(codex_home)
        finished = _read_sessions(codex_home, codex_files, _codex_cwd, 'codex', collected, deadline)
    folders = sorted(collected.folders.values(), key=lambda folder: (-folder['last_used'], folder['path'].casefold()))
    return {
        'folders': folders[:MAX_SUGGESTIONS],
        'skipped': collected.skipped,
        'truncated': not finished or len(folders) > MAX_SUGGESTIONS,
    }
