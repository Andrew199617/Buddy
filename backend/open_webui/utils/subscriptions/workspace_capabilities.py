"""Temporary browser pairing and explicit directory grants for Buddy Runner.

This API never accepts the privileged Runner key or provider credentials. Grants
are supplied by the host operator at startup; a browser path grants nothing.
Command execution is a separate opt-in and uses the host account, not a sandbox.
Write access is not contained either: git, editors and build tools run commands
named in project files, so a write grant can lead to execution on the host.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import socket
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from open_webui.utils.subscriptions.pinned_paths import PinnedPath, PinnedPathError
from buddy_runner.process import ChildProcess, ProcessClosedError, subscription_env

FILE_BYTES = 1024 * 1024
BODY_BYTES = 2 * 1024 * 1024
OUTPUT_BYTES = 256 * 1024
MAX_WORKSPACES = 64
MAX_TERMINALS = 8
MAX_TERMINAL_HISTORY = 64
MAX_SESSIONS = 64
MAX_DIRECTORY_ENTRIES = 5000
# Crockford base32 leaves out I, L, O and U, so a code copied by eye or typed on
# a phone is unambiguous. Five groups of four symbols carry 100 random bits.
PAIRING_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
PAIRING_GROUPS = 5
PAIRING_GROUP_LENGTH = 4
CLEANUP_UNCONFIRMED = 'Command cleanup could not be confirmed. Stop the Runner on the host.'
log = logging.getLogger(__name__)


class CapabilityError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _fail(status: int, message: str):
    raise CapabilityError(status, message)


async def _cancel_and_wait(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _run_cleanup_batch(cleanups: list, pending_message: str) -> None:
    """Run cleanups together. A failure is logged and the next sweep retries it."""
    results = await asyncio.gather(*cleanups, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            log.error(pending_message)


def _now_ms() -> float:
    return time.time() * 1000


def _duration(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a positive duration in milliseconds.')
    if value <= 0 or value > 2147483647:
        raise ValueError(f'{name} must be a positive duration in milliseconds.')
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _new_pairing_code() -> str:
    """A one-use code such as 7K3M-Q9TD-2XHA-PW4N-B8RE."""
    groups = []
    for _ in range(PAIRING_GROUPS):
        groups.append(''.join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_GROUP_LENGTH)))
    return '-'.join(groups)


def _normalize_pairing_code(value: str) -> str:
    """Ignore case, spaces and dashes, and read O, I and L as the digits they resemble."""
    compact = re.sub(r'[\s-]', '', value).upper()
    return compact.translate(str.maketrans('OIL', '011'))


def _content_version(text: str) -> str:
    """Identifies the file contents a client opened, so a save cannot silently replace newer edits."""
    return _digest(text)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_link(value: os.stat_result) -> bool:
    # Windows junctions and other reparse points need the same treatment as links.
    reparse = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & reparse)


def _entry_kind(entry: os.DirEntry) -> str | None:
    """'directory' or 'file' for a listed entry the API could open, otherwise None.

    Links, junctions and other reparse points, special files and multiply
    linked files would be refused when opened, so listings leave them out.
    """
    value = entry.stat(follow_symlinks=False)
    if _is_link(value):
        return None
    if stat.S_ISDIR(value.st_mode):
        return 'directory'
    if not stat.S_ISREG(value.st_mode):
        return None
    if sys.platform == 'win32':
        # DirEntry.stat() reports no link count on Windows.
        value = os.stat(entry.path, follow_symlinks=False)
    if value.st_nlink > 1:
        return None
    return 'file'


def _no_link_ancestors(absolute: Path) -> None:
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if _is_link(current.lstat()):
            _fail(403, 'Symbolic links and junctions cannot be granted or followed.')


def _relative_path(value) -> str:
    if not isinstance(value, str):
        _fail(400, 'A relative path is required.')
    if value in ('', '.'):
        return ''
    if len(value) > 4096 or re.search(r'[\\:\x00-\x1f\x7f<>"|?*]', value) or value.startswith('/'):
        _fail(403, 'Only safe, forward-slash relative paths are allowed.')
    for component in value.split('/'):
        if not component or component in ('.', '..') or component.endswith((' ', '.')):
            _fail(403, 'Path traversal and ambiguous path segments are not allowed.')
        if re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', component, re.IGNORECASE):
            _fail(403, 'Reserved device paths are not allowed.')
    return value


def _exact_origin(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in ('http', 'https')
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and (port is None or 0 <= port <= 65535)
        and value == f'{parsed.scheme}://{parsed.netloc}'
    )


@dataclass
class _Grant:
    id: str
    name: str
    path: Path
    requested_path: Path
    identity: tuple[int, int]
    write: bool = False
    execute: bool = False

    def public(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'path': str(self.path),
            'capabilities': {'read': True, 'write': self.write, 'execute': self.execute},
        }


@dataclass
class _Session:
    key: str
    origin: str | None
    expires_at: float


@dataclass
class _Workspace:
    id: str
    name: str
    host_id: str
    grant_id: str
    path: Path
    relative: str
    identity: tuple[int, int]
    owner: str

    def public(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'hostId': self.host_id,
            'grantId': self.grant_id,
            'path': str(self.path),
        }


@dataclass
class _Terminal:
    id: str
    workspace_id: str
    owner: str
    status: str = 'ready'
    exit_code: int | None = None
    last_used: float = field(default_factory=_now_ms)
    child: ChildProcess | None = None
    task: asyncio.Task | None = None
    cleanup_error: str | None = None
    stopping: bool = False
    # A command's reader thread appends here too, so the retained tail is locked.
    _output: bytearray = field(default_factory=bytearray, repr=False, compare=False)
    _output_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def append(self, text: str) -> None:
        data = text.encode('utf-8', 'replace')
        with self._output_lock:
            self._output.extend(data)
            excess = len(self._output) - OUTPUT_BYTES
            if excess > 0:
                del self._output[:excess]

    def clear_output(self) -> None:
        with self._output_lock:
            self._output.clear()

    def record_cleanup_failure(self, error: BaseException) -> None:
        # New commands stay refused until a later stop confirms cleanup.
        self.stopping = True
        self.cleanup_error = str(error)

    def public(self) -> dict:
        with self._output_lock:
            output = bytes(self._output)
        return {
            'id': self.id,
            'workspaceId': self.workspace_id,
            'status': self.status,
            'output': output.decode('utf-8', 'ignore'),
            'exitCode': self.exit_code,
        }


class _TerminalProcess(ChildProcess):
    """A workspace command whose reader thread writes straight into its terminal.

    ChildProcess pauses a process whose unread output reaches its queue budget:
    its reader thread stops reading, so the pipe fills and the CLI waits. That
    keeps provider JSON streams whole. A terminal keeps only its newest output,
    so a fast command should keep running even while the event loop is busy:
    its text goes into the terminal's bounded tail, and only the end of output
    is queued for the event loop.
    """

    def __init__(self, terminal: _Terminal, *args, **kwargs):
        # Set before ChildProcess.__init__ starts the reader threads.
        self._terminal = terminal
        super().__init__(*args, **kwargs)

    def _post(self, item: str | None) -> None:
        if item is None:
            super()._post(None)
            return
        self._terminal.append(item)


class WorkspaceCapabilities:
    """A memory-only host instance; construct and register before serving requests."""

    def __init__(
        self,
        roots: list[dict] | None = None,
        origins: list[str] | None = None,
        host_name: str | None = None,
        allow_no_origin: bool = False,
        token_ttl_ms: float = 30 * 60 * 1000,
        pairing_ttl_ms: float = 5 * 60 * 1000,
        command_timeout_ms: float = 60 * 1000,
        session_idle_ms: float = 10 * 60 * 1000,
    ):
        self.token_ttl_ms = _duration(token_ttl_ms, 'token_ttl_ms')
        self.pairing_ttl_ms = _duration(pairing_ttl_ms, 'pairing_ttl_ms')
        self.command_timeout_ms = _duration(command_timeout_ms, 'command_timeout_ms')
        self.session_idle_ms = _duration(session_idle_ms, 'session_idle_ms')
        self.origins = set(origins or [])
        if any(not _exact_origin(origin) for origin in self.origins):
            raise ValueError('Origins must be exact HTTP(S) origins without a path or credentials.')
        self.allow_no_origin = allow_no_origin is True
        name = socket.gethostname() if host_name is None else host_name
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('Host name must be nonempty text.')
        self.host = {'id': str(uuid.uuid4()), 'name': name, 'platform': sys.platform, 'version': '0.1.0'}
        self._closing = False
        self.issue_pairing_code()
        self._grants: dict[str, _Grant] = {}
        self._sessions: dict[str, _Session] = {}
        self._pending_sessions: dict[str, _Session] = {}
        self._pending_grants: set[str] = set()
        self._workspaces: dict[str, _Workspace] = {}
        self._terminals: dict[str, _Terminal] = {}
        self._sweeper: asyncio.Task | None = None
        self._close_lock = asyncio.Lock()
        for specification in roots or []:
            grant = self._make_grant(specification)
            self._grants[grant.id] = grant

    def _make_grant(self, specification: dict) -> _Grant:
        if not isinstance(specification, dict) or not isinstance(specification.get('path'), str):
            raise ValueError('Each grant must name an absolute project-directory path.')
        requested = Path(specification['path'])
        if not requested.is_absolute():
            raise ValueError('Each grant must name an absolute project-directory path.')
        requested = Path(os.path.abspath(requested))
        with PinnedPath(requested) as pinned:
            canonical = pinned.path
            value = pinned.stat
        if not stat.S_ISDIR(value.st_mode) or _is_link(value):
            raise ValueError('Each grant must be a real directory.')
        if _same_path(canonical, Path(canonical.anchor)):
            raise ValueError('Grant a project directory, not an entire filesystem volume.')
        name = specification.get('name', canonical.name)
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('Grant names must be nonempty text.')
        return _Grant(
            id=str(uuid.uuid4()), name=name, path=canonical, requested_path=requested,
            identity=_identity(value), write=specification.get('write') is True,
            execute=specification.get('execute') is True,
        )

    def _root_problem(self, grant: _Grant) -> str | None:
        """Why this grant's root cannot be opened now, or None when it can."""
        try:
            with PinnedPath(grant.requested_path, expected_root=grant.identity):
                return None
        except PinnedPathError:
            return 'The granted directory was replaced or is no longer a real directory. Grant it again on the host.'
        except (FileNotFoundError, NotADirectoryError):
            return 'The granted directory no longer exists.'
        except OSError:
            return 'The host account cannot open the granted directory.'

    def _pin(self, grant: _Grant, input_path, *, file: bool = False, writable: bool = False):
        relative = _relative_path(input_path)
        # PinnedPath is internal: the API validates ADS, device names, controls,
        # separators and traversal before opening any native handle.
        pinned = PinnedPath(
            grant.requested_path, relative, expected_root=grant.identity,
            file=file, writable=writable,
        )
        return pinned, relative

    def _resolve(self, grant: _Grant, input_path) -> tuple[str, Path, os.stat_result]:
        pinned, relative = self._pin(grant, input_path)
        with pinned:
            return relative, pinned.path, pinned.stat

    def _open_file(self, grant: _Grant, input_path, writable: bool):
        pinned, relative = self._pin(grant, input_path, file=True, writable=writable)
        try:
            if pinned.stat.st_size > FILE_BYTES:
                _fail(413, 'Files are limited to 1 MiB.')
            return pinned, relative
        except BaseException:
            pinned.close()
            raise

    async def _read_in_thread(self, pinned: PinnedPath) -> str:
        return await self._in_worker(self._read_text, pinned.descriptor)

    async def _in_worker(self, function, *args):
        """Run blocking file work off the event loop while the caller keeps its handles open."""
        operation = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Cancellation cannot release/recycle the descriptor while a worker
            # still owns it. Repeated cancellation also waits for that ownership.
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if not operation.cancelled():
                operation.exception()
            raise

    def _read_text(self, descriptor: int) -> str:
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = bytearray()
        while len(data) <= FILE_BYTES:
            chunk = os.read(descriptor, min(65536, FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > FILE_BYTES:
            _fail(413, 'Files are limited to 1 MiB.')
        try:
            text = data.decode('utf-8', 'strict')
        except UnicodeDecodeError:
            _fail(415, 'Only UTF-8 text files can be accessed.')
        if '\0' in text:
            _fail(415, 'Only UTF-8 text files can be accessed.')
        return text

    def _validate_host(self, request: web.Request) -> None:
        transport = request.transport
        local = transport.get_extra_info('sockname') if transport else None
        peer = transport.get_extra_info('peername') if transport else None
        if not local or not peer or peer[0] not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
            _fail(403, 'This foundation accepts loopback connections only.')
        allowed = {f'127.0.0.1:{local[1]}', f'localhost:{local[1]}', f'[::1]:{local[1]}'}
        supplied = request.headers.getall('Host', [])
        if len(supplied) != 1 or supplied[0].lower() not in allowed:
            _fail(403, 'The Host header must identify this loopback execution host.')

    def _origin(self, request: web.Request, allow_missing: bool = False) -> str | None:
        origins = request.headers.getall('Origin', [])
        if not origins and (allow_missing or self.allow_no_origin):
            return None
        if len(origins) != 1 or origins[0] not in self.origins:
            _fail(403, 'This browser origin is not allowed.')
        return origins[0]

    def _authenticate(self, request: web.Request, origin: str | None) -> _Session:
        values = request.headers.getall('Authorization', [])
        if len(values) != 1 or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}', values[0]):
            _fail(401, 'Pair this browser with the execution host first.')
        session = self._sessions.get(_digest(values[0][7:]))
        if not session:
            _fail(401, 'The companion session is invalid or revoked.')
        if _now_ms() >= session.expires_at:
            _fail(401, 'The companion session expired. Ask the host operator for a fresh pairing code.')
        if session.origin != origin:
            _fail(403, 'This session belongs to a different browser origin.')
        return session

    def _active(self, session: _Session, grant: _Grant | None = None) -> None:
        if session.key not in self._sessions or _now_ms() >= session.expires_at:
            _fail(401, 'The companion session is invalid, expired, or revoked.')
        if grant and grant.id not in self._grants:
            _fail(403, 'The directory grant was revoked.')
        if self._closing:
            _fail(503, 'The execution host is shutting down.')

    def _grant(self, grant_id) -> _Grant:
        grant = self._grants.get(grant_id) if isinstance(grant_id, str) else None
        if not grant:
            _fail(403, 'Choose a directory grant approved on this host.')
        return grant

    def _workspace(self, workspace_id, session: _Session) -> _Workspace:
        workspace = self._workspaces.get(workspace_id) if isinstance(workspace_id, str) else None
        if not workspace or workspace.owner != session.key:
            _fail(404, 'Workspace not found in this session.')
        return workspace

    async def _terminal(self, terminal_id, session: _Session, allow_cleanup: bool = False) -> _Terminal:
        terminal = self._terminals.get(terminal_id)
        if not terminal or terminal.owner != session.key:
            _fail(404, 'Terminal not found in this session.')
        if terminal.cleanup_error and not allow_cleanup:
            _fail(503, CLEANUP_UNCONFIRMED)
        if terminal.status != 'closed' and _now_ms() - terminal.last_used >= self.session_idle_ms:
            await self._stop_terminal(terminal)
            _fail(410, 'The idle terminal session expired.')
        terminal.last_used = _now_ms()
        return terminal

    async def _body(self, request: web.Request) -> dict:
        if request.content_type != 'application/json':
            _fail(400, 'Provide a JSON request body.')
        if request.content_length and request.content_length > BODY_BYTES:
            _fail(413, 'Request bodies are limited to 2 MiB.')
        data = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > BODY_BYTES:
                        _fail(413, 'Request bodies are limited to 2 MiB.')
            body = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            _fail(400, 'Provide a valid JSON object.')
        except TimeoutError:
            _fail(408, 'The request body timed out.')
        if not isinstance(body, dict):
            _fail(400, 'Provide a valid JSON object.')
        return body

    async def _pair(self, request: web.Request, origin: str | None) -> dict:
        if self._pairing_used or _now_ms() >= self._pairing_expires_at or self._pairing_attempts >= 8:
            _fail(403, 'Pairing is unavailable. Ask the host operator for a fresh code.')
        body = await self._body(request)
        self._pairing_attempts += 1
        if self._pairing_attempts > 8:
            _fail(403, 'Pairing is unavailable. Ask the host operator for a fresh code.')
        code = body.get('code')
        if not isinstance(code, str) or len(code) > 100:
            _fail(403, 'Invalid pairing code.')
        if not hmac.compare_digest(_digest(_normalize_pairing_code(code)), self._pairing_digest):
            _fail(403, 'Invalid pairing code.')
        if self._pairing_used or self._closing or _now_ms() >= self._pairing_expires_at:
            _fail(403, 'Pairing is unavailable. Ask the host operator for a fresh code.')
        if len(self._sessions) + len(self._pending_sessions) >= MAX_SESSIONS:
            _fail(429, 'The browser session limit was reached. Disconnect an existing session or wait for its expiry.')
        self._pairing_used = True
        token = secrets.token_urlsafe(32)
        session = _Session(_digest(token), origin, _now_ms() + self.token_ttl_ms)
        self._sessions[session.key] = session
        return {'token': token, 'expiresAt': session.expires_at, 'host': self.host}

    def issue_pairing_code(self) -> dict:
        """Host-only refresh; current sessions and execution remain unchanged."""
        if self._closing:
            _fail(503, 'The execution host is shutting down.')
        self.pairing_code = _new_pairing_code()
        self._pairing_digest = _digest(_normalize_pairing_code(self.pairing_code))
        self._pairing_expires_at = _now_ms() + self.pairing_ttl_ms
        self._pairing_attempts = 0
        self._pairing_used = False
        return {'code': self.pairing_code, 'expiresAt': self._pairing_expires_at, 'host': self.host}

    async def _list_directory(self, grant: _Grant, input_path) -> dict:
        pinned, relative = self._pin(grant, input_path)
        with pinned:
            entries = await self._in_worker(self._list_entries, pinned, relative)
        entries.sort(key=lambda entry: (entry['type'], entry['name']))
        return {'path': relative, 'entries': entries}

    def _list_entries(self, pinned: PinnedPath, relative: str) -> list[dict]:
        # Listing grants nothing: opening an entry later pins its full path again.
        # So entries are classified from the pinned directory instead of
        # re-pinning every ancestor for each one.
        entries = []
        with pinned.scandir() as listing:
            for index, entry in enumerate(listing):
                if index >= MAX_DIRECTORY_ENTRIES:
                    _fail(413, 'This directory has too many entries.')
                child_relative = f'{relative}/{entry.name}' if relative else entry.name
                try:
                    _relative_path(child_relative)
                    kind = _entry_kind(entry)
                except (CapabilityError, OSError):
                    continue
                if kind is not None:
                    entries.append({'name': entry.name, 'path': child_relative, 'type': kind})
        return entries

    async def _file(self, request: web.Request, session: _Session) -> dict:
        if request.method == 'GET':
            grant = self._grant(request.query.get('grantId'))
            pinned, relative = self._open_file(grant, request.query.get('path'), False)
            try:
                content = await self._read_in_thread(pinned)
                pinned.current_stat()
                self._active(session, grant)
                return {'path': relative, 'content': content, 'version': _content_version(content)}
            finally:
                pinned.close()
        body = await self._body(request)
        grant = self._grant(body.get('grantId'))
        if not grant.write:
            _fail(403, 'File writing was not approved for this grant.')
        content = body.get('content')
        if not isinstance(content, str) or '\0' in content:
            _fail(400, 'Provide UTF-8 text content.')
        try:
            data = content.encode('utf-8')
        except UnicodeEncodeError:
            _fail(400, 'Provide UTF-8 text content.')
        if len(data) > FILE_BYTES:
            _fail(413, 'Files are limited to 1 MiB.')
        pinned, relative = self._open_file(grant, body.get('path'), True)
        try:
            current = await self._read_in_thread(pinned)
            pinned.current_stat()
            self._active(session, grant)
            # A save made from an older copy would silently undo edits made on
            # the host since it was opened, unless the user chose to overwrite.
            if body.get('overwrite') is not True and body.get('baseVersion') != _content_version(current):
                _fail(409, 'This file changed on the host after it was opened. Reload it, or overwrite the host copy.')
            # No await between the final permission check and the replacement.
            self._replace_contents(pinned, data)
            return {'path': relative, 'content': content, 'version': _content_version(content)}
        finally:
            pinned.close()

    def _replace_contents(self, pinned: PinnedPath, data: bytes) -> None:
        """Save through one atomic rename, so any failure keeps the previous contents."""
        try:
            pinned.replace_file(data)
        except PermissionError:
            _fail(403, 'The host could not replace this file. Another program may have it open, or the host account cannot write to its folder. The previous contents were kept.')
        except OSError:
            _fail(500, 'The host could not save this file. The previous contents were kept.')

    def _validate_command(self, body: dict) -> None:
        executable = body.get('executable')
        args = body.get('args')
        if not isinstance(executable, str) or not executable or len(executable) > 4096 or '\0' in executable:
            _fail(400, 'Provide a valid executable path or name.')
        if not isinstance(args, list) or len(args) > 100:
            _fail(400, 'Provide at most 100 string arguments.')
        if any(not isinstance(arg, str) or len(arg) > 16384 or '\0' in arg for arg in args):
            _fail(400, 'Command arguments must be strings without null bytes.')
        if sum(len(arg.encode('utf-8')) for arg in [executable, *args]) > 65536:
            _fail(400, 'The command is too large.')

    async def _collect_output(self, terminal: _Terminal, child: ChildProcess) -> None:
        # A _TerminalProcess appends its text from the reader thread, so this
        # mostly waits for the end of output.
        try:
            while True:
                text = await child.read_text()
                if text is None:
                    return
                terminal.append(text)
        except ProcessClosedError as error:
            terminal.append(f'\nCommand output stopped: {error}\n')
            await child.close()

    async def _finish_command(self, terminal: _Terminal, child: ChildProcess) -> None:
        reader = asyncio.create_task(self._collect_output(terminal, child))
        timed_out = False
        try:
            terminal.exit_code = await child.wait(self.command_timeout_ms / 1000)
        except TimeoutError:
            terminal.append('\nCommand time limit reached.\n')
            terminal.stopping = True
            timed_out = True
        finally:
            try:
                await child.close()
                try:
                    await asyncio.wait_for(reader, 2)
                except TimeoutError:
                    await _cancel_and_wait(reader)
                if terminal.exit_code is None:
                    terminal.exit_code = child.returncode
                terminal.child = None
                if timed_out or terminal.stopping or terminal.status == 'closed':
                    terminal.status = 'closed'
                else:
                    terminal.status = 'exited'
            except (OSError, TimeoutError, ProcessClosedError) as error:
                terminal.record_cleanup_failure(error)
                terminal.append(f'\n{CLEANUP_UNCONFIRMED}\n')
                await _cancel_and_wait(reader)

    async def _run_command(self, terminal: _Terminal, workspace: _Workspace, body: dict, session: _Session) -> dict:
        self._validate_command(body)
        if terminal.stopping or terminal.status == 'closed':
            _fail(410, 'The terminal session is closed.')
        if terminal.child:
            _fail(409, 'A command is already running in this terminal.')
        grant = self._grant(workspace.grant_id)
        if not grant.execute:
            _fail(403, 'Host execution was not approved for this grant.')
        pinned, _ = self._pin(grant, workspace.relative)
        try:
            with pinned:
                if _identity(pinned.stat) != workspace.identity:
                    _fail(403, 'The workspace directory changed. Select it again.')
                self._active(session, grant)
                terminal.clear_output()
                terminal.exit_code = None
                child = _TerminalProcess(
                    terminal, [body['executable'], *body['args']], str(pinned.path), subscription_env(),
                    merge_stderr=True, cwd_fd=pinned.directory_fd,
                )
        except OSError as error:
            terminal.append(f'Cannot start command: {error}\n')
            terminal.status = 'exited'
            terminal.exit_code = 1
            return terminal.public()
        terminal.child = child
        terminal.status = 'running'
        child.close_stdin()
        terminal.task = asyncio.create_task(self._finish_command(terminal, child))
        return terminal.public()

    async def _stop_terminal(self, terminal: _Terminal) -> None:
        # Close admission before awaiting native disposal. Snapshot ownership so
        # natural completion cannot replace the task a DELETE is joining.
        terminal.stopping = True
        child = terminal.child
        task = terminal.task
        if child:
            await self._confirm_child_closed(terminal, child)
        if task and task is not asyncio.current_task():
            await asyncio.shield(task)
        # A finishing command may have reported an earlier cleanup failure while
        # this stop was joining it. The final native disposal owns confirmation.
        if child:
            await self._confirm_child_closed(terminal, child)
        elif terminal.cleanup_error:
            _fail(503, CLEANUP_UNCONFIRMED)
        if terminal.child is child:
            terminal.child = None
        terminal.status = 'closed'

    async def _confirm_child_closed(self, terminal: _Terminal, child: ChildProcess) -> None:
        """Close a command's process tree, or record why cleanup is unconfirmed and fail."""
        try:
            await child.close()
        except (OSError, TimeoutError, ProcessClosedError) as error:
            terminal.record_cleanup_failure(error)
            _fail(503, CLEANUP_UNCONFIRMED)
        terminal.cleanup_error = None

    async def _revoke_session(self, session: _Session) -> None:
        self._sessions.pop(session.key, None)
        self._pending_sessions[session.key] = session
        owned = [terminal for terminal in self._terminals.values() if terminal.owner == session.key]
        await self._stop_all(owned)
        self._terminals = {key: terminal for key, terminal in self._terminals.items() if terminal.owner != session.key}
        self._workspaces = {key: workspace for key, workspace in self._workspaces.items() if workspace.owner != session.key}
        self._pending_sessions.pop(session.key, None)

    async def _stop_all(self, terminals: list[_Terminal]) -> None:
        results = await asyncio.gather(*(self._stop_terminal(terminal) for terminal in terminals), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def revoke_grant(self, grant_id: str) -> bool:
        """Host-operator control; no browser or provider path can create a grant."""
        if not self._grants.pop(grant_id, None) and grant_id not in self._pending_grants:
            return False
        self._pending_grants.add(grant_id)
        affected = {workspace.id for workspace in self._workspaces.values() if workspace.grant_id == grant_id}
        terminals = [terminal for terminal in self._terminals.values() if terminal.workspace_id in affected]
        await self._stop_all(terminals)
        self._terminals = {key: terminal for key, terminal in self._terminals.items() if terminal.workspace_id not in affected}
        self._workspaces = {key: workspace for key, workspace in self._workspaces.items() if key not in affected}
        self._pending_grants.discard(grant_id)
        return True

    def list_grants(self) -> list[dict]:
        """Public metadata for the separately authenticated host-operator control.

        Each root is checked on its own. A missing or replaced root marks only
        its grant unavailable, so the other grants stay usable and the operator
        can still see the broken grant's id to revoke it.
        """
        listed = []
        for grant in self._grants.values():
            problem = self._root_problem(grant)
            metadata = grant.public()
            metadata['available'] = problem is None
            metadata['unavailableReason'] = problem
            listed.append(metadata)
        return listed

    async def _dispatch(self, request: web.Request, origin: str | None) -> dict:
        path = request.path
        method = request.method
        if method == 'OPTIONS':
            requested_method = request.headers.get('Access-Control-Request-Method')
            if requested_method and requested_method not in ('GET', 'POST', 'PUT', 'DELETE'):
                _fail(403, 'This CORS method is not allowed.')
            headers = request.headers.get('Access-Control-Request-Headers', '').lower().split(',')
            if any(header.strip() not in ('', 'authorization', 'content-type') for header in headers):
                _fail(403, 'This CORS header is not allowed.')
            return {'ok': True}
        if method == 'GET' and path == '/v1/host':
            return {'host': self.host}
        if method == 'GET' and path == '/v1/capabilities':
            return {
                'protocol': 'buddy-workspaces', 'version': 1, 'host': self.host,
                'capabilities': {'directories': True, 'files': True, 'workspaces': True, 'terminals': True, 'pty': False},
                'permissions': {'requiresPairing': True, 'grants': 'host-approved', 'commands': 'opt-in-os-account'},
            }
        if method == 'POST' and path == '/v1/pair':
            return await self._pair(request, origin)
        session = self._authenticate(request, origin)
        if method == 'DELETE' and path == '/v1/session':
            await self._revoke_session(session)
            return {'revoked': True}
        if method == 'GET' and path == '/v1/grants':
            return {'grants': self.list_grants()}
        if method == 'GET' and path == '/v1/directories':
            grant = self._grant(request.query.get('grantId'))
            return await self._list_directory(grant, request.query.get('path', ''))
        if path == '/v1/files' and method in ('GET', 'PUT'):
            return await self._file(request, session)
        if method == 'POST' and path == '/v1/workspaces':
            body = await self._body(request)
            grant = self._grant(body.get('grantId'))
            relative, absolute, value = self._resolve(grant, body.get('path'))
            if not stat.S_ISDIR(value.st_mode):
                _fail(400, 'A workspace must be a real directory.')
            self._active(session, grant)
            for workspace in self._workspaces.values():
                if (workspace.owner == session.key and workspace.grant_id == grant.id
                        and workspace.relative == relative and workspace.identity == _identity(value)):
                    return {'workspace': workspace.public()}
            if len(self._workspaces) >= MAX_WORKSPACES:
                _fail(429, 'The workspace limit was reached.')
            workspace = _Workspace(
                id=str(uuid.uuid4()), name=absolute.name, host_id=self.host['id'], grant_id=grant.id,
                path=absolute, relative=relative, identity=_identity(value), owner=session.key,
            )
            self._workspaces[workspace.id] = workspace
            return {'workspace': workspace.public()}
        if method == 'GET' and path == '/v1/workspaces':
            return {'workspaces': [workspace.public() for workspace in self._workspaces.values() if workspace.owner == session.key]}
        if method == 'POST' and path == '/v1/terminals':
            body = await self._body(request)
            workspace = self._workspace(body.get('workspaceId'), session)
            grant = self._grant(workspace.grant_id)
            if not grant.execute:
                _fail(403, 'Host execution was not approved for this grant.')
            self._resolve(grant, workspace.relative)
            self._active(session, grant)
            if len(self._terminals) >= MAX_TERMINAL_HISTORY:
                _fail(429, 'The command session history limit was reached. Stop and remove finished command sessions, or disconnect to clear them.')
            if sum(terminal.status != 'closed' for terminal in self._terminals.values()) >= MAX_TERMINALS:
                _fail(429, 'The terminal session limit was reached.')
            terminal = _Terminal(str(uuid.uuid4()), workspace.id, session.key)
            self._terminals[terminal.id] = terminal
            return {'terminal': terminal.public()}
        match = re.fullmatch(r'/v1/terminals/([a-f0-9-]+)(/commands)?', path)
        if match:
            terminal = await self._terminal(match[1], session, allow_cleanup=method == 'DELETE' and not match[2])
            if method == 'GET' and not match[2]:
                return {'terminal': terminal.public()}
            if method == 'DELETE' and not match[2]:
                await self._stop_terminal(terminal)
                # Only a confirmed stop gets here. Removing the session keeps
                # stopped ones from filling the shared history limit.
                self._terminals.pop(terminal.id, None)
                return {'terminal': terminal.public()}
            if method == 'POST' and match[2]:
                body = await self._body(request)
                workspace = self._workspace(terminal.workspace_id, session)
                return {'terminal': await self._run_command(terminal, workspace, body, session)}
        _fail(404, 'Companion endpoint not found.')

    async def handle(self, request: web.Request) -> web.Response:
        origin = None
        try:
            self._validate_host(request)
            if self._closing:
                _fail(503, 'The execution host is shutting down.')
            is_host = request.method == 'GET' and request.path == '/v1/host'
            origin = self._origin(request, allow_missing=is_host)
            body = await self._dispatch(request, origin)
            response = web.json_response(body)
        except CapabilityError as error:
            response = web.json_response({'error': str(error)}, status=error.status)
        except (FileNotFoundError, NotADirectoryError):
            response = web.json_response({'error': 'The requested file or directory does not exist.'}, status=404)
        except PermissionError:
            response = web.json_response({'error': 'The host account cannot access this file or directory.'}, status=403)
        except (UnicodeError, ValueError, TypeError):
            response = web.json_response({'error': 'Invalid request.'}, status=400)
        except OSError:
            # Still a JSON reply with CORS headers, so the page can show it.
            response = web.json_response({'error': 'The host could not complete this request.'}, status=500)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Vary'] = 'Origin'
        if origin:
            response.headers['Access-Control-Allow-Origin'] = origin
        if request.method == 'OPTIONS' and response.status == 200:
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE'
            response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
        return response

    async def _start(self, app: web.Application) -> None:
        self._sweeper = asyncio.create_task(self._sweep())

    async def _sweep(self) -> None:
        interval = max(10, min(1000, self.token_ttl_ms, self.session_idle_ms)) / 1000
        while not self._closing:
            await asyncio.sleep(interval)
            now = _now_ms()
            expired = [session for session in self._sessions.values() if now >= session.expires_at]
            sessions = {session.key: session for session in [*expired, *self._pending_sessions.values()]}
            await _run_cleanup_batch(
                [self._revoke_session(session) for session in sessions.values()],
                'Workspace session cleanup is pending; host disposal will be retried.',
            )
            await _run_cleanup_batch(
                [self.revoke_grant(grant_id) for grant_id in list(self._pending_grants)],
                'Workspace grant cleanup is pending; host disposal will be retried.',
            )
            idle = [terminal for terminal in self._terminals.values() if terminal.status != 'closed' and (terminal.stopping or now - terminal.last_used >= self.session_idle_ms)]
            await _run_cleanup_batch(
                [self._stop_terminal(terminal) for terminal in idle],
                'Workspace terminal cleanup is pending; host disposal will be retried.',
            )

    async def _shutdown(self, app: web.Application) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._close_lock:
            self._closing = True
            if self._sweeper:
                self._sweeper.cancel()
                await asyncio.gather(self._sweeper, return_exceptions=True)
                self._sweeper = None
            await self._stop_all(list(self._terminals.values()))
            self._sessions.clear()
            self._pending_sessions.clear()
            self._pending_grants.clear()
            self._workspaces.clear()
            self._terminals.clear()
            self._grants.clear()

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get('/v1/host', self.handle)
        app.router.add_get('/v1/capabilities', self.handle)
        app.router.add_post('/v1/pair', self.handle)
        app.router.add_delete('/v1/session', self.handle)
        app.router.add_get('/v1/grants', self.handle)
        app.router.add_get('/v1/directories', self.handle)
        app.router.add_get('/v1/files', self.handle)
        app.router.add_put('/v1/files', self.handle)
        app.router.add_post('/v1/workspaces', self.handle)
        app.router.add_get('/v1/workspaces', self.handle)
        app.router.add_post('/v1/terminals', self.handle)
        app.router.add_get('/v1/terminals/{terminal_id}', self.handle)
        app.router.add_delete('/v1/terminals/{terminal_id}', self.handle)
        app.router.add_post('/v1/terminals/{terminal_id}/commands', self.handle)
        app.router.add_route('*', '/v1/{tail:.*}', self.handle)
        app.on_startup.append(self._start)
        app.on_shutdown.append(self._shutdown)
