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

import aiohttp

from open_webui.utils.subscriptions.discovery import find_tool
from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.process import (
    ChildProcess,
    ProcessClosedError,
    StreamedOutput,
    run_command,
    subscription_env,
)

log = logging.getLogger(__name__)

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


class RemoteProcess(StreamedOutput):
    """A process on a Buddy Runner, with the same methods as ChildProcess."""

    def __init__(self, websocket: aiohttp.ClientWebSocketResponse, args: list[str], pid: int):
        super().__init__()
        self.args = args
        self.pid = pid
        self.returncode: int | None = None
        self._websocket = websocket
        self._stderr = ''
        self._exited = asyncio.Event()
        self._receiver = asyncio.create_task(self._receive())

    async def _receive(self) -> None:
        try:
            async for message in self._websocket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                event = json.loads(message.data)
                if event.get('type') == 'stdout':
                    self._chunks.put_nowait(event.get('data', ''))
                elif event.get('type') == 'exit':
                    self.returncode = event.get('code')
                    self._stderr = event.get('stderr') or ''
                    break
        except (aiohttp.ClientError, ValueError) as error:
            log.debug('Runner process %s stream ended: %s', self.pid, error)
        finally:
            self._chunks.put_nowait(None)
            self._exited.set()
            await self._websocket.close()

    def stderr_text(self) -> str:
        return self._stderr

    async def write(self, text: str) -> None:
        if self._websocket.closed or self._exited.is_set():
            raise ProcessClosedError(f'{os.path.basename(self.args[0])} is no longer running')
        try:
            await self._websocket.send_json({'type': 'stdin', 'data': text})
        except (aiohttp.ClientError, ConnectionResetError) as error:
            raise ProcessClosedError(f'{os.path.basename(self.args[0])} is no longer running') from error

    async def _send_quietly(self, message: dict, then_close: bool = False) -> None:
        try:
            if not self._websocket.closed:
                await self._websocket.send_json(message)
        except (aiohttp.ClientError, ConnectionResetError, RuntimeError):
            pass
        if then_close:
            await self._websocket.close()

    def close_stdin(self) -> None:
        asyncio.get_running_loop().create_task(self._send_quietly({'type': 'close_stdin'}))

    async def finish_output(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._exited.wait(), timeout)
        except TimeoutError:
            pass

    async def wait(self, timeout: float | None = None) -> int:
        await asyncio.wait_for(self._exited.wait(), timeout)
        return self.returncode

    def kill(self) -> None:
        """Stop the process on the runner; closing the connection also stops it."""
        if self._websocket.closed:
            return
        asyncio.get_running_loop().create_task(self._send_quietly({'type': 'kill'}, then_close=True))


class RemoteMachine:
    """A Buddy Runner on another computer or in a container."""

    def __init__(self, machine_id: str, name: str, url: str, key: str):
        self.id = machine_id
        self.name = name
        self.url = url.rstrip('/')
        self._key = key
        self._session: aiohttp.ClientSession | None = None
        self._info: dict | None = None

    def matches(self, name: str, url: str, key: str) -> bool:
        return self.name == name and self.url == url.rstrip('/') and self._key == key

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self._key}'}

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers())
        return self._session

    async def _request(self, method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
        try:
            async with self._client().request(
                method,
                f'{self.url}{path}',
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
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
        try:
            websocket = await self._client().ws_connect(f'{self.url}/process', heartbeat=30, max_msg_size=0)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise SubscriptionError(f'Could not reach {self.name} at {self.url}: {error}') from error

        start = {
            'type': 'start',
            'args': args,
            'cwd': cwd,
            'env': extra_env or {},
            'temp_files': temp_files or {},
        }
        await websocket.send_json(start)
        reply = await websocket.receive(timeout=60)
        event = json.loads(reply.data) if reply.type == aiohttp.WSMsgType.TEXT else {}
        if event.get('type') != 'started':
            await websocket.close()
            raise SubscriptionError(f'{self.name} could not start {os.path.basename(args[0])}: {event.get("message")}')
        return RemoteProcess(websocket, args, event.get('pid'))

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
