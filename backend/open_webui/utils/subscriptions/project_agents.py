"""Custom agent files in a project folder on This server, for the composer's agent menu.

The chat page reads agents on computers connected with the Buddy app through the
relay. This server has no such connection, so Buddy reads its agent folders itself.
Only the agent folders inside the project folder are read: links that lead out of
the project, files that are too large and files that aren't UTF-8 text are passed
over. The chat page parses the files, as it does for the relay.
"""

import os
from pathlib import Path

# The same folders and file names as the chat page's agent-files.ts.
AGENT_FOLDERS = ('.github/agents', '.claude/agents', '.github/chatmodes')
LEGACY_CHAT_MODES = '.github/chatmodes'
MAX_AGENT_FILES = 50
MAX_AGENT_BYTES = 1024 * 1024


def is_agent_file(folder: str, file_name: str) -> bool:
    lower = file_name.lower()
    if folder == LEGACY_CHAT_MODES:
        return lower.endswith('.chatmode.md')
    return lower.endswith('.md')


def _agent_folder(root: Path, folder: str) -> Path | None:
    """The agent folder's real path, or None when it is missing or leads out of the project."""
    try:
        real = root.joinpath(*folder.split('/')).resolve(strict=True)
    except OSError:
        return None
    if not real.is_dir() or not real.is_relative_to(root):
        return None
    return real


def _agent_files_in(root: Path, folder: str) -> list[tuple[str, Path]]:
    directory = _agent_folder(root, folder)
    if directory is None:
        return []
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError:
        return []
    files = []
    for entry in entries:
        if not is_agent_file(folder, entry.name):
            continue
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            continue
        files.append((f'{folder}/{entry.name}', Path(entry.path)))
    return files


def _read_text(path: Path) -> str | None:
    try:
        with open(path, 'rb') as handle:
            data = handle.read(MAX_AGENT_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_AGENT_BYTES:
        return None
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None
    if '\0' in text:
        return None
    return text


def read_agent_files(project_path: str) -> dict:
    """The agent files in the project folder, each with its path inside the project.

    Blocking; call it from a worker thread. Raises FileNotFoundError when the
    project folder itself is gone.
    """
    root = Path(project_path).resolve(strict=True)
    if not root.is_dir():
        raise FileNotFoundError(project_path)
    found = []
    for folder in AGENT_FOLDERS:
        found.extend(_agent_files_in(root, folder))
    kept = found[:MAX_AGENT_FILES]
    files = []
    for relative, path in kept:
        content = _read_text(path)
        if content is not None:
            files.append({'path': relative, 'content': content})
    return {'files': files, 'left_out': len(found) - len(kept)}
