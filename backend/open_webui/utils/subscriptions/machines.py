"""Where the Claude Code and Codex CLIs run.

A machine is either the computer running this Buddy server ("This server") or
a Buddy Runner reached over HTTP: another computer, such as the administrator's
PC, or a container. Providers start the CLIs through a machine, so the same
code works in both places. Sign-ins, repositories, and terminal commands all
live on the machine.
"""

import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import aiohttp

from open_webui.utils.subscriptions.discovery import find_tool
from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.runner_transport import (
    normalize_runner_url,
    runner_ssl_context,
    runner_trace_config,
)
from buddy_runner.process import (
    ChildProcess,
    MAX_STDERR_BYTES,
    OutputLimitError,
    ProcessClosedError,
    StreamedOutput,
    run_command,
    subscription_env,
)

log = logging.getLogger(__name__)

LOCAL_MACHINE_ID = 'local'
TEMP_FILE_TOKEN = '{temp:%s}'
MAX_WEBSOCKET_MESSAGE_BYTES = 32 * 1024 * 1024
# Unread stdout from a Runner process. Its WebSocket also carries heartbeats
# and the stop reply, which are only handled while it is read, so the output
# is buffered instead of pausing the Runner. A stalled reader collects about
# 100 lines a second from a streaming CLI; this covers a few minutes of that.
MAX_REMOTE_STDOUT_BYTES = 16 * 1024 * 1024
MAX_REMOTE_STDOUT_CHUNKS = 16384
# The Runner drops a connection whose message exceeds 32 MiB. JSON escapes a
# character into at most 12 bytes, so this many characters stay under 12 MiB.
MAX_STDIN_MESSAGE_CHARS = 1024 * 1024
# How long a stop waits for the Runner's reply on the process connection
# before it also asks over HTTP. A connection can go silent without closing,
# and the heartbeat takes up to 45 seconds to notice.
STOP_REPLY_WAIT_SECONDS = 2.0


class StartupCleanupUnconfirmedError(SubscriptionError):
    """A connected startup ended without proof that its host process stopped."""


class _StartRefusedError(Exception):
    """The Runner reported that a start created no process."""


async def _finish_cancelled_cleanup(operation: asyncio.Task) -> None:
    """Keep ownership of cleanup when the caller is cancelled repeatedly."""
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            continue
    operation.result()


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
    temp_dir: str | None = None,
) -> ChildProcess:
    """Start a CLI on this computer; shared by LocalMachine and the runner."""
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


class RemoteProcess(StreamedOutput):
    """A process on a Buddy Runner, with the same methods as ChildProcess."""

    def __init__(
        self, websocket: aiohttp.ClientWebSocketResponse, args: list[str], pid: int,
        *, process_id: str | None = None, machine: 'RemoteMachine | None' = None,
    ):
        super().__init__()
        self._output_limit_bytes = MAX_REMOTE_STDOUT_BYTES
        self._pending_chunk_limit = MAX_REMOTE_STDOUT_CHUNKS
        self.args = args
        self.pid = pid
        # Lets cleanup ask the Runner directly after the connection is lost.
        self.process_id = process_id
        self._machine = machine
        self.returncode: int | None = None
        self._websocket = websocket
        self._stderr = ''
        self._exited = asyncio.Event()
        self._confirmed_stopped = asyncio.Event()
        self._connection_error: ProcessClosedError | None = None
        self._eof_posted = False
        self._close_task: asyncio.Task | None = None
        self._disposal_started = False
        # Keeps each write's pieces together, as one local pipe write would.
        self._write_lock = asyncio.Lock()
        self._receiver = asyncio.create_task(self._receive())

    @property
    def cleanup_confirmed(self) -> bool:
        return (
            self._confirmed_stopped.is_set() and self._receiver.done()
            and self._websocket.closed
        )

    @property
    def disposal_started(self) -> bool:
        return self._disposal_started or self._exited.is_set()

    def _consume_chunk(self, chunk: str | None) -> None:
        if chunk is not None:
            self._queued_bytes -= len(chunk.encode('utf-8'))
            self._queued_chunks -= 1

    def _post_eof(self) -> None:
        if not self._eof_posted:
            self._eof_posted = True
            self._chunks.put_nowait(None)

    def _set_output_error(self, error: OutputLimitError) -> None:
        if self._output_error is None:
            self._output_error = error
            self._post_eof()
            self.kill()

    def _post_stdout(self, text: str) -> None:
        if self._output_error is not None:
            return
        size = len(text.encode('utf-8'))
        # Never wait for the reader here: while the WebSocket is not read,
        # heartbeat pongs and the stop reply wait too and the link drops.
        if not self._queue_has_room(size):
            self._set_output_error(OutputLimitError('The remote CLI stdout buffer limit was reached'))
            return
        self._queued_bytes += size
        self._queued_chunks += 1
        self._chunks.put_nowait(text)

    async def _receive(self) -> None:
        try:
            async for message in self._websocket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                event = json.loads(message.data)
                if not isinstance(event, dict):
                    raise ValueError('Runner process event must be an object')
                if event.get('type') == 'stdout':
                    data = event.get('data', '')
                    if not isinstance(data, str):
                        raise ValueError('Runner stdout must be text')
                    self._post_stdout(data)
                elif event.get('type') == 'stopped':
                    pid = event.get('pid')
                    if isinstance(pid, int) and not isinstance(pid, bool) and pid == self.pid:
                        self._confirmed_stopped.set()
                elif event.get('type') == 'exit':
                    code = event.get('code')
                    stderr = event.get('stderr') or ''
                    if isinstance(code, bool) or not isinstance(code, int) or not isinstance(stderr, str):
                        raise ValueError('Runner exit status is invalid')
                    self.returncode = code
                    self._stderr = stderr.encode('utf-8')[-MAX_STDERR_BYTES:].decode('utf-8', 'replace')
                    # Runner sends exit only after its native containment has
                    # closed, so a natural exit also confirms tree cleanup.
                    self._confirmed_stopped.set()
                    break
        except (aiohttp.ClientError, ValueError, ConnectionResetError) as error:
            log.debug('Runner process %s stream ended: %s', self.pid, error)
        finally:
            if self.returncode is None:
                # The stream ended without an exit status. Mark it inactive,
                # while requiring the separate host acknowledgement for cleanup.
                self.returncode = -1
            if not self._confirmed_stopped.is_set():
                self._connection_error = ProcessClosedError(
                    f'Runner connection ended without confirming cleanup of process {self.pid}'
                )
            self._post_eof()
            self._exited.set()
            await self._websocket.close()

    def stderr_text(self) -> str:
        return self._stderr

    def _closed_error(self) -> ProcessClosedError:
        return ProcessClosedError(f'{os.path.basename(self.args[0])} is no longer running')

    async def write(self, text: str) -> None:
        # Large input, such as a history with images, goes in bounded pieces.
        # The Runner writes them in order, so the CLI reads the same bytes.
        async with self._write_lock:
            if self._websocket.closed or self._exited.is_set():
                raise self._closed_error()
            for start in range(0, len(text), MAX_STDIN_MESSAGE_CHARS):
                piece = text[start:start + MAX_STDIN_MESSAGE_CHARS]
                try:
                    await self._websocket.send_json({'type': 'stdin', 'data': piece})
                except (aiohttp.ClientError, ConnectionResetError) as error:
                    raise self._closed_error() from error

    async def _send_quietly(self, message: dict) -> None:
        try:
            if not self._websocket.closed:
                await self._websocket.send_json(message)
        except (aiohttp.ClientError, ConnectionResetError, RuntimeError):
            pass

    def close_stdin(self) -> None:
        asyncio.get_running_loop().create_task(self._close_stdin_after_writes())

    async def _close_stdin_after_writes(self) -> None:
        # Never end stdin between the pieces of a write in progress.
        async with self._write_lock:
            await self._send_quietly({'type': 'close_stdin'})

    async def finish_output(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._exited.wait(), timeout)
        except TimeoutError:
            pass

    async def wait(self, timeout: float | None = None) -> int:
        await asyncio.wait_for(self._exited.wait(), timeout)
        if not self._confirmed_stopped.is_set():
            raise self._connection_error or ProcessClosedError('Runner process cleanup is unconfirmed')
        return self.returncode

    def kill(self) -> None:
        """Schedule verified cleanup; async callers should await ``close``."""
        operation = self._ensure_close_task(5.0)
        operation.add_done_callback(self._log_cleanup_failure)

    def _log_cleanup_failure(self, operation: asyncio.Task) -> None:
        if operation.cancelled():
            return
        error = operation.exception()
        if error is not None:
            log.warning('Runner process %s cleanup could not be confirmed: %s', self.pid, error)

    def _ensure_close_task(self, timeout: float) -> asyncio.Task:
        self._disposal_started = True
        if self._close_task is None or self._close_failed():
            # A failed attempt is retried, for example once the Runner is back.
            self._close_task = asyncio.create_task(self._verified_close(timeout))
        return self._close_task

    def _close_failed(self) -> bool:
        operation = self._close_task
        return operation.done() and (operation.cancelled() or operation.exception() is not None)

    async def _confirm_stopped_with_runner(self) -> None:
        """Ask the Runner about a process whose stream ended without an acknowledgement."""
        failure = self._connection_error or ProcessClosedError(
            f'Runner did not confirm cleanup of process {self.pid}'
        )
        if self._machine is None or self.process_id is None:
            raise failure
        try:
            stopped = await self._machine.confirm_process_stopped(self.process_id)
        except SubscriptionError as error:
            raise failure from error
        if not stopped:
            raise failure
        self._confirmed_stopped.set()

    async def _confirm_stop_without_reply(self, confirmation: asyncio.Task) -> None:
        """Ask the Runner over HTTP, while still accepting a late reply on the connection.

        The connection may have closed, or gone silent without closing. A
        slow stop, or an older Runner without the HTTP route, can still
        answer on the connection.
        """
        request = asyncio.create_task(self._confirm_stopped_with_runner())
        try:
            await asyncio.wait((confirmation, request), return_when=asyncio.FIRST_COMPLETED)
            if not self._confirmed_stopped.is_set():
                # The Runner could not confirm over HTTP; keep listening on the connection.
                await asyncio.wait((confirmation, self._receiver), return_when=asyncio.FIRST_COMPLETED)
            if not self._confirmed_stopped.is_set():
                raise request.exception()
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)

    async def _verified_close(self, timeout: float) -> None:
        confirmation = None
        try:
            async with asyncio.timeout(timeout):
                if not self._confirmed_stopped.is_set():
                    await self._send_quietly({'type': 'kill'})
                    confirmation = asyncio.create_task(self._confirmed_stopped.wait())
                    reply_wait = min(STOP_REPLY_WAIT_SECONDS, timeout / 2)
                    await asyncio.wait(
                        (confirmation, self._receiver), timeout=reply_wait, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not self._confirmed_stopped.is_set():
                        await self._confirm_stop_without_reply(confirmation)
                # Keep the transport alive until the host has acknowledged
                # native cleanup. A stopped acknowledgement need not include
                # exit output, so explicitly release that open stream here.
                await self._websocket.close()
                await self._receiver
        except TimeoutError as error:
            raise TimeoutError(f'Runner did not confirm cleanup of process {self.pid} within {timeout:g} seconds') from error
        finally:
            if confirmation is not None:
                confirmation.cancel()
                await asyncio.gather(confirmation, return_exceptions=True)
            if not self._confirmed_stopped.is_set():
                # Release a failed transport without calling that release a
                # successful process stop. The caller still receives failure.
                await self._websocket.close()

    async def kill_and_wait(self, timeout: float = 5.0) -> None:
        self._disposal_started = True
        operation = self._ensure_close_task(timeout)
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            await _finish_cancelled_cleanup(operation)
            raise

    async def close(self, timeout: float = 5.0) -> None:
        self._disposal_started = True
        await self.kill_and_wait(timeout)


def check_runner_url(url: str) -> None:
    """Compatibility check for callers that do not need the canonical origin."""
    normalize_runner_url(url)


class RemoteMachine:
    """A Buddy Runner on another computer or in a container."""

    def __init__(self, machine_id: str, name: str, url: str, key: str):
        self.id = machine_id
        self.name = name
        self.url = normalize_runner_url(url)
        self._key = key
        self._session: aiohttp.ClientSession | None = None
        # Set once Buddy drops this machine, for example after its address or
        # key changed. Nothing closes a session opened after that.
        self._closed = False
        self._info: dict | None = None

    def matches(self, name: str, url: str, key: str) -> bool:
        return self.name == name and self.url == normalize_runner_url(url) and self._key == key

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self._key}'}

    def _new_session(self) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(ssl=runner_ssl_context())
        return aiohttp.ClientSession(
            connector=connector, headers=self._headers(),
            trace_configs=[runner_trace_config()],
        )

    def _client(self) -> aiohttp.ClientSession:
        if self._closed:
            raise SubscriptionError(f'{self.name} was changed or removed. Refresh before trying again.')
        if self._session is None or self._session.closed:
            self._session = self._new_session()
        return self._session

    async def _request(
        self, method: str, path: str, payload: dict | None = None, timeout: float = 30,
        session: aiohttp.ClientSession | None = None,
    ) -> dict:
        if session is None:
            session = self._client()
        try:
            async with session.request(
                method,
                f'{self.url}{path}',
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    detail = (body or {}).get('error') if isinstance(body, dict) else None
                    raise SubscriptionError(f'{self.name}: {detail or f"runner returned HTTP {response.status}"}')
                return body or {}
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise SubscriptionError(f'Could not reach {self.name} at {self.url}: {error}') from error

    async def info(self) -> dict:
        info = await self._request('GET', '/health')
        self._info = info
        return info

    async def _cached_info(self) -> dict:
        if self._info is None:
            return await self.info()
        return self._info

    async def chat_dir(self) -> str:
        return (await self._cached_info())['chat_dir']

    async def default_workspace(self) -> str:
        return (await self._cached_info())['default_workspace']

    async def find_tool(self, tool: str, configured_path: str = '') -> str | None:
        result = await self._request('POST', '/tools/find', {'tool': tool, 'configured_path': configured_path})
        return result.get('path')

    async def path_exists(self, path: str, kind: str) -> bool:
        result = await self._request('POST', '/paths/check', {'path': path, 'kind': kind})
        return bool(result.get('exists'))

    async def run(self, args: list[str], cwd: str, timeout: float, extra_env: dict | None = None):
        payload = {'args': args, 'cwd': cwd, 'timeout': timeout, 'env': extra_env or {}}
        result = await self._request('POST', '/run', payload, timeout=timeout + 15)
        if result.get('timed_out'):
            raise TimeoutError(f'{os.path.basename(args[0])} did not finish within {timeout:.0f} seconds')
        return result.get('returncode'), result.get('stdout', ''), result.get('stderr', '')

    async def start_process(
        self,
        args: list[str],
        cwd: str,
        extra_env: dict[str, str] | None = None,
        temp_files: dict[str, str] | None = None,
    ) -> RemoteProcess:
        program = os.path.basename(args[0])
        start = {
            'type': 'start',
            'args': args,
            'cwd': cwd,
            'env': extra_env or {},
            'temp_files': temp_files or {},
        }
        try:
            # Encode before connecting, so a local encoding error starts nothing.
            start_message = json.dumps(start)
        except (TypeError, ValueError) as error:
            raise SubscriptionError(f'{self.name} could not start {program}: {error}') from error

        try:
            websocket = await self._client().ws_connect(
                f'{self.url}/process', heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=1.0),
                max_msg_size=MAX_WEBSOCKET_MESSAGE_BYTES,
            )
        except (aiohttp.ClientError, TimeoutError) as error:
            raise SubscriptionError(f'Could not reach {self.name} at {self.url}: {error}') from error

        pid = None
        process_id = None
        try:
            await websocket.send_str(start_message)
            reply = await websocket.receive(timeout=60)
            event = json.loads(reply.data) if reply.type == aiohttp.WSMsgType.TEXT else {}
            if not isinstance(event, dict):
                raise ValueError('Runner startup response must be an object')
            if event.get('type') == 'start_failed':
                message = event.get('message')
                if not isinstance(message, str) or not message:
                    message = 'The Runner did not start the process.'
                raise _StartRefusedError(message)
            pid = event.get('pid')
            if event.get('type') != 'started':
                raise ValueError(event.get('message') or 'Runner did not acknowledge startup')
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise ValueError('Runner startup response has no valid process ID')
            # Runners without this ID can only confirm cleanup on this connection.
            if isinstance(event.get('id'), str) and event['id']:
                process_id = event['id']
        except _StartRefusedError as refusal:
            await self._close_refused_start(websocket)
            raise SubscriptionError(f'{self.name} could not start {program}: {refusal}') from refusal
        except Exception as error:
            await self._dispose_failed_start(websocket, args, pid)
            raise SubscriptionError(f'{self.name} could not start {program}: {error}') from error
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self._dispose_failed_start(websocket, args, pid))
            await _finish_cancelled_cleanup(cleanup)
            raise
        return RemoteProcess(websocket, args, pid, process_id=process_id, machine=self)

    async def confirm_process_stopped(self, process_id: str) -> bool:
        """Ask the Runner to stop a process whose connection ended, and confirm its cleanup.

        A process started before Buddy dropped this machine can still need
        this. That request uses a session of its own, closed when it ends.
        """
        encoded_id = quote(process_id, safe='')
        path = f'/processes/{encoded_id}/stop'
        if not self._closed:
            result = await self._request('POST', path, timeout=15)
        else:
            async with self._new_session() as session:
                result = await self._request('POST', path, timeout=15, session=session)
        return result.get('stopped') is True

    async def _close_refused_start(self, websocket) -> None:
        # The Runner reported that nothing started, so a failed close cannot
        # leave a process behind.
        try:
            await websocket.close()
        except Exception as error:
            log.debug('Could not close the refused %s startup connection: %s', self.name, error)

    async def _dispose_failed_start(self, websocket, args: list[str], pid: int | None) -> None:
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            try:
                await RemoteProcess(websocket, args, pid).close()
            except Exception as error:
                raise StartupCleanupUnconfirmedError(
                    f'{self.name} could not confirm cleanup of startup process {pid}'
                ) from error
            return
        detail = f'{self.name} startup ended before a process identity was received; process cleanup unconfirmed'
        try:
            await websocket.close()
        except Exception as error:
            raise StartupCleanupUnconfirmedError(detail) from error
        log.warning(detail)
        raise StartupCleanupUnconfirmedError(detail)

    async def close(self) -> None:
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
