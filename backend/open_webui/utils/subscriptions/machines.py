"""Where the Claude Code and Codex CLIs run.

A machine is either the computer running this Buddy server ("This server") or
a Buddy Runner reached over HTTP: another computer, such as the administrator's
PC, or a container. Providers start the CLIs through a machine, so the same
code works in both places. Sign-ins, repositories, and terminal commands all
live on the machine.
"""

import os
import socket
import sys
import tempfile
from pathlib import Path

from open_webui.utils.subscriptions.discovery import find_tool
from open_webui.utils.subscriptions.process import (
    ChildProcess,
    run_command,
    subscription_env,
)

LOCAL_MACHINE_ID = 'local'
TEMP_FILE_TOKEN = '{temp:%s}'


def temp_file_arg(name: str) -> str:
    """Placeholder for a temporary file in process arguments; see start_process."""
    return TEMP_FILE_TOKEN % name


def _write_temp_files(temp_files: dict[str, str] | None, directory: str | None = None) -> dict[str, str]:
    paths = {}
    for name, content in (temp_files or {}).items():
        handle = tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', suffix='.md', prefix=f'buddy-{name}-', dir=directory, delete=False
        )
        with handle:
            handle.write(content)
        paths[name] = handle.name
    return paths


def _substitute_temp_files(args: list[str], paths: dict[str, str]) -> list[str]:
    substituted = []
    for arg in args:
        for name, path in paths.items():
            arg = arg.replace(temp_file_arg(name), path)
        substituted.append(arg)
    return substituted


def start_local_process(
    args: list[str],
    cwd: str,
    extra_env: dict[str, str] | None = None,
    temp_files: dict[str, str] | None = None,
) -> ChildProcess:
    """Start a CLI on this computer."""
    paths = _write_temp_files(temp_files)
    try:
        return ChildProcess(
            _substitute_temp_files(args, paths),
            cwd,
            subscription_env(extra_env),
            cleanup_paths=list(paths.values()),
        )
    except OSError:
        for path in paths.values():
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


class LocalMachine:
    """The computer running this Buddy server."""

    id = LOCAL_MACHINE_ID
    name = 'This server'

    def __init__(self, state_dir: Path):
        self._state_dir = state_dir

    def _directory(self, name: str) -> str:
        path = self._state_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    async def info(self) -> dict:
        return {
            'platform': sys.platform,
            'hostname': socket.gethostname(),
            'chat_dir': self._directory('chat'),
            'default_workspace': self._directory('workspace'),
        }

    async def chat_dir(self) -> str:
        """An empty folder for chat-only turns, sign-in, and status checks."""
        return self._directory('chat')

    async def default_workspace(self) -> str:
        return self._directory('workspace')

    async def find_tool(self, tool: str, configured_path: str = '') -> str | None:
        return find_tool(tool, configured_path)

    async def path_exists(self, path: str, kind: str) -> bool:
        if kind == 'dir':
            return Path(path).is_dir()
        return Path(path).is_file()

    async def run(self, args: list[str], cwd: str, timeout: float, extra_env: dict | None = None):
        return await run_command(args, cwd, subscription_env(extra_env), timeout)

    async def start_process(
        self,
        args: list[str],
        cwd: str,
        extra_env: dict[str, str] | None = None,
        temp_files: dict[str, str] | None = None,
    ) -> ChildProcess:
        """Start a CLI. ``temp_file_arg(name)`` in args becomes the path of ``temp_files[name]``."""
        return start_local_process(args, cwd, extra_env, temp_files)

    async def close(self) -> None:
        pass
