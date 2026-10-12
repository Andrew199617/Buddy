"""Start a CLI on this computer, with any temporary files its arguments name."""

import os
import tempfile

from buddy_runner.process import ChildProcess, subscription_env

TEMP_FILE_TOKEN = '{temp:%s}'


def temp_file_arg(name: str) -> str:
    """Placeholder for a temporary file in process arguments; see start_local_process."""
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
    temp_dir: str | None = None,
) -> ChildProcess:
    """Start a CLI on this computer; shared by the server's own machine and the Runner.

    ``temp_file_arg(name)`` in args becomes the path of a file holding
    ``temp_files[name]``, removed when the process is closed.
    """
    paths = _write_temp_files(temp_files, temp_dir)
    try:
        return ChildProcess(
            _substitute_temp_files(args, paths),
            cwd,
            subscription_env(extra_env),
            cleanup_paths=list(paths.values()),
        )
    except (OSError, ValueError, TypeError):
        for path in paths.values():
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
