"""Find the Claude Code and Codex executables on the computer running this code."""

import os
import shutil
import sys
from pathlib import Path

TOOL_CLAUDE = 'claude'
TOOL_CODEX = 'codex'


def _desktop_bundle_version(path: Path) -> list[int]:
    version_name = path.parent.parent.name
    numbers = []
    for piece in version_name.split('.'):
        if piece.isdigit():
            numbers.append(int(piece))
        else:
            numbers.append(0)
    return numbers


def _desktop_bundle_dirs() -> list[Path]:
    """Folders where the Claude desktop app keeps its bundled Claude Code CLI.

    The Windows app is an MSIX package, so its %APPDATA%\\Claude folder is
    virtualized: only processes inside the package see it there. Everything
    else finds the same files under %LOCALAPPDATA%\\Packages\\Claude_*.
    """
    home = Path.home()
    appdata = Path(os.environ.get('APPDATA') or home / 'AppData' / 'Roaming')
    local_appdata = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    directories = [appdata / 'Claude' / 'claude-code']
    for package in local_appdata.glob('Packages/Claude_*'):
        directories.append(package / 'LocalCache' / 'Roaming' / 'Claude' / 'claude-code')
    return directories


def _newest_desktop_bundle() -> str | None:
    if sys.platform != 'win32' and not os.environ.get('APPDATA'):
        return None
    bundles = []
    for directory in _desktop_bundle_dirs():
        bundles.extend(directory.glob('*/*/claude.exe'))
    bundles = [binary for binary in bundles if binary.is_file()]
    if not bundles:
        return None
    bundles.sort(key=_desktop_bundle_version)
    return str(bundles[-1])


def find_claude_cli(configured_path: str = '') -> str | None:
    if configured_path:
        if os.path.isfile(configured_path):
            return configured_path
        return None

    on_path = shutil.which('claude')
    if on_path and Path(on_path).suffix.lower() in ('', '.exe'):
        return on_path

    executable_name = 'claude.exe' if sys.platform == 'win32' else 'claude'
    native_install = Path.home() / '.local' / 'bin' / executable_name
    if native_install.is_file():
        return str(native_install)

    bundled = _newest_desktop_bundle()
    if bundled:
        return bundled

    # An npm install provides claude.cmd on Windows; it works, just more slowly.
    return on_path


def _npm_vendor_binary(bin_dir: Path) -> str | None:
    """npm installs a codex.cmd shim; the native binary sits in node_modules."""
    executable_name = 'codex.exe' if sys.platform == 'win32' else 'codex'
    package_dir = bin_dir / 'node_modules' / '@openai' / 'codex'
    patterns = [
        f'node_modules/@openai/codex-*/vendor/*/bin/{executable_name}',
        f'vendor/*/codex/{executable_name}',
    ]
    for pattern in patterns:
        matches = sorted(binary for binary in package_dir.glob(pattern) if binary.is_file())
        if matches:
            return str(matches[0])
    return None


def find_codex_cli(configured_path: str = '') -> str | None:
    if configured_path:
        if os.path.isfile(configured_path):
            return configured_path
        return None

    on_path = shutil.which('codex')
    if not on_path:
        return None
    if Path(on_path).suffix.lower() == '.exe':
        return on_path
    vendor_binary = _npm_vendor_binary(Path(on_path).parent)
    if vendor_binary:
        return vendor_binary
    return on_path


def find_tool(tool: str, configured_path: str = '') -> str | None:
    if tool == TOOL_CLAUDE:
        return find_claude_cli(configured_path)
    if tool == TOOL_CODEX:
        return find_codex_cli(configured_path)
    raise ValueError(f'Unknown tool: {tool}')
