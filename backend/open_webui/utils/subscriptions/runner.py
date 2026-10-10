"""Buddy Runner: lets a Buddy server start Claude Code and Codex on this computer.

Run it on the machine whose terminal, files, and CLI sign-ins the models
should use, then add it to Buddy under Admin Settings → Connections →
Subscriptions → Machines:

    python -m open_webui.utils.subscriptions.runner --port 8765

Provider and operator requests need ``Authorization: Bearer <key>``; the key
is created in ``~/.buddy-runner/key`` on first start. Browser workspace requests
use independent temporary pairing tokens and explicit host-approved roots.
The runner listens on 127.0.0.1 by default. Docker/private transport setup is
separate from this executable and must preserve the authorization boundaries.
Processes use plain pipes, so JSON lines pass through unchanged, and a
process stops when Buddy's connection to it closes.

Anyone holding the key can run any command as the user running this runner.
"""

import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import secrets
import socket
import sys
from pathlib import Path

from aiohttp import WSMsgType, web

from open_webui.utils.subscriptions.discovery import find_tool
from open_webui.utils.subscriptions.machines import start_local_process
from open_webui.utils.subscriptions.process import ProcessClosedError, run_command, subscription_env
from open_webui.utils.subscriptions.workspace_capabilities import WorkspaceCapabilities

log = logging.getLogger('buddy_runner')

RUNNER_VERSION = 1
DEFAULT_PORT = 8765
DEFAULT_STATE_DIR = Path.home() / '.buddy-runner'


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({'error': message}, status=status)


def _auth_middleware(key: str):
    @web.middleware
    async def check_key(request: web.Request, handler):
        if request.path.startswith('/v1/'):
            # WorkspaceCapabilities authenticates browser sessions independently.
            return await handler(request)
        if 'Origin' in request.headers:
            return _json_error(403, 'Provider execution credentials are not browser credentials.')
        supplied = request.headers.get('Authorization', '')
        expected = f'Bearer {key}'
        if not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
            return _json_error(401, 'Missing or wrong runner key.')
        return await handler(request)

    return check_key


class Runner:
    def __init__(self, state_dir: Path, workspaces: WorkspaceCapabilities):
        self.state_dir = state_dir
        self.chat_dir = state_dir / 'chat'
        self.default_workspace = state_dir / 'workspace'
        self.temp_dir = state_dir / 'tmp'
        self.workspaces = workspaces
        self.processes = set()
        self.websockets = set()
        self.command_requests = set()
        self.closing = False
        self._shutdown_task = None
        for directory in (self.chat_dir, self.default_workspace, self.temp_dir):
            directory.mkdir(parents=True, exist_ok=True)

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                'ok': True,
                'version': RUNNER_VERSION,
                'platform': sys.platform,
                'hostname': socket.gethostname(),
                'home': str(Path.home()),
                'chat_dir': str(self.chat_dir),
                'default_workspace': str(self.default_workspace),
                'host': self.workspaces.host,
                'capabilities': {
                    'directories': True, 'files': True, 'workspaces': True,
                    'terminals': True, 'pty': False, 'providerExecution': True,
                },
            }
        )

    async def find_tool(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            path = find_tool(str(body.get('tool')), str(body.get('configured_path') or ''))
        except ValueError as error:
            return _json_error(400, str(error))
        return web.json_response({'path': path})

    async def check_path(self, request: web.Request) -> web.Response:
        body = await request.json()
        path = Path(str(body.get('path') or ''))
        if body.get('kind') == 'dir':
            exists = path.is_dir()
        else:
            exists = path.is_file()
        return web.json_response({'exists': exists})

    async def run(self, request: web.Request) -> web.Response:
        body = await request.json()
        args = body.get('args')
        if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
            return _json_error(400, 'args must be a non-empty list of strings.')
        try:
            timeout = float(body.get('timeout', 30))
        except (ValueError, TypeError):
            return _json_error(400, 'Choose a finite timeout between 0.1 and 120 seconds.')
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 120:
            return _json_error(400, 'Choose a finite timeout between 0.1 and 120 seconds.')
        if self.closing:
            return _json_error(503, 'The Runner is shutting down.')
        if len(self.command_requests) + len(self.processes) >= 16:
            return _json_error(429, 'The Runner process limit was reached.')
        cwd = str(body.get('cwd') or self.chat_dir)
        task = asyncio.current_task()
        self.command_requests.add(task)
        try:
            returncode, stdout, stderr = await run_command(args, cwd, subscription_env(body.get('env')), timeout)
        except TimeoutError:
            return web.json_response({'timed_out': True})
        except (OSError, ValueError, ProcessClosedError) as error:
            return _json_error(400, f'Could not run {args[0]}: {error}')
        finally:
            self.command_requests.discard(task)
        return web.json_response({'returncode': returncode, 'stdout': stdout, 'stderr': stderr})

    async def process(self, request: web.Request) -> web.WebSocketResponse:
        """Start a process and stream it over a WebSocket until it exits or Buddy disconnects."""
        if self.closing:
            return _json_error(503, 'The Runner is shutting down.')
        if len(self.command_requests) + len(self.processes) >= 16:
            return _json_error(429, 'The Runner process limit was reached.')
        websocket = web.WebSocketResponse(heartbeat=30, max_msg_size=32 * 1024 * 1024)
        await websocket.prepare(request)
        self.websockets.add(websocket)
        process = None
        forwarder = None

        async def forward_output():
            try:
                while True:
                    text = await process.read_text()
                    if text is None:
                        break
                    await websocket.send_json({'type': 'stdout', 'data': text})
                await process.finish_output(30)
                # The process is done; remove its temporary files before reporting.
                await process.close()
                exit_event = {'type': 'exit', 'code': process.returncode, 'stderr': process.stderr_text()}
                await websocket.send_json(exit_event)
            except (OSError, ConnectionResetError, RuntimeError, ProcessClosedError, TimeoutError):
                # Closing wakes the handler and its process-cleanup finally.
                await websocket.close()
            else:
                await websocket.close()

        try:
            start = await websocket.receive_json(timeout=60)
            args = start.get('args') if isinstance(start, dict) else None
            if (
                not isinstance(start, dict) or start.get('type') != 'start'
                or not isinstance(args, list) or not args
                or not all(isinstance(arg, str) and '\0' not in arg for arg in args)
            ):
                await websocket.send_json({'type': 'error', 'message': 'Expected a start message with string args.'})
                return websocket
            if self.closing or len(self.command_requests) + len(self.processes) >= 16:
                await websocket.send_json({'type': 'error', 'message': 'The Runner is unavailable or at its process limit.'})
                return websocket
            process = start_local_process(
                args, str(start.get('cwd') or self.chat_dir), start.get('env') or None,
                start.get('temp_files') or None, str(self.temp_dir),
            )
            self.processes.add(process)
            log.info('Started %s (pid %s)', os.path.basename(args[0]), process.pid)
            # Startup acknowledgement is covered by the same cleanup finally.
            await websocket.send_json({'type': 'started', 'pid': process.pid})
            forwarder = asyncio.create_task(forward_output())
            async for message in websocket:
                if message.type != WSMsgType.TEXT:
                    continue
                command = json.loads(message.data)
                kind = command.get('type')
                if kind == 'stdin':
                    try:
                        await process.write(str(command.get('data') or ''))
                    except ProcessClosedError:
                        pass
                elif kind == 'close_stdin':
                    process.close_stdin()
                elif kind == 'kill':
                    await process.close()
                    # Closing the socket is not proof of native process cleanup.
                    # Acknowledge only after the whole owned process tree stops.
                    if not websocket.closed:
                        await websocket.send_json({'type': 'stopped', 'pid': process.pid})
        except (OSError, ValueError, TimeoutError, ConnectionResetError, RuntimeError) as error:
            if not websocket.closed:
                try:
                    await websocket.send_json({'type': 'error', 'message': str(error)})
                except (ConnectionResetError, RuntimeError):
                    pass
        finally:
            cleanup = asyncio.create_task(self._dispose_connection(process, forwarder, websocket))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # aiohttp cancels request handlers on a lost connection. Native
                # disposal and registry cleanup must still finish before exit.
                await cleanup
                raise
        return websocket

    async def _dispose_connection(self, process, forwarder, websocket) -> None:
        try:
            if process is not None:
                await process.close()
                self.processes.discard(process)
                log.info('Stopped process %s', process.pid)
        finally:
            if forwarder is not None and not forwarder.done():
                forwarder.cancel()
            if forwarder is not None:
                await asyncio.gather(forwarder, return_exceptions=True)
            self.websockets.discard(websocket)
            await websocket.close()

    async def operator_grants(self, request: web.Request) -> web.Response:
        return web.json_response({'grants': self.workspaces.list_grants()})

    async def revoke_grant(self, request: web.Request) -> web.Response:
        revoked = await self.workspaces.revoke_grant(request.match_info['grant_id'])
        return web.json_response({'revoked': revoked})

    async def refresh_pairing(self, request: web.Request) -> web.Response:
        # The host operator can pair another device without stopping provider CLIs.
        # This route never approves a directory or changes an existing session.
        return web.json_response(self.workspaces.issue_pairing_code())

    async def close(self, app: web.Application) -> None:
        self.closing = True
        retry_failed_shutdown = self._shutdown_task is not None and self._shutdown_task.done() and (
            self._shutdown_task.cancelled() or self._shutdown_task.exception() is not None
        )
        if self._shutdown_task is None or retry_failed_shutdown:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            while not self._shutdown_task.done():
                try:
                    await asyncio.shield(self._shutdown_task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            self._shutdown_task.result()
            raise

    async def _shutdown(self) -> None:
        # A failed process must not skip cleanup of other owned transports and
        # finite commands. Keep the failure visible after every cleanup attempt.
        errors = await asyncio.gather(
            *(process.close() for process in list(self.processes)), return_exceptions=True
        )
        errors.extend(await asyncio.gather(
            *(websocket.close(code=1001) for websocket in list(self.websockets)), return_exceptions=True
        ))
        requests = list(self.command_requests)
        for task in requests:
            task.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        # aiohttp stops later shutdown callbacks when one raises. Attempt the
        # workspace boundary here as well before reporting provider failures.
        errors.extend(await asyncio.gather(self.workspaces.close(), return_exceptions=True))
        for error in errors:
            if isinstance(error, BaseException):
                raise error


def build_app(
    key: str, state_dir: Path, *, roots=None, origins=None, host_name=None, allow_no_origin=False
) -> web.Application:
    if not key or not key.strip():
        raise ValueError('A non-empty provider execution key is required.')
    workspaces = WorkspaceCapabilities(
        roots=roots, origins=origins, host_name=host_name, allow_no_origin=allow_no_origin
    )
    runner = Runner(state_dir, workspaces)
    app = web.Application(middlewares=[_auth_middleware(key)], client_max_size=64 * 1024 * 1024)
    app['workspace_capabilities'] = workspaces
    app['runner'] = runner
    app.router.add_get('/health', runner.health)
    app.router.add_post('/tools/find', runner.find_tool)
    app.router.add_post('/paths/check', runner.check_path)
    app.router.add_post('/run', runner.run)
    app.router.add_get('/process', runner.process)
    app.router.add_get('/workspace-grants', runner.operator_grants)
    app.router.add_delete('/workspace-grants/{grant_id}', runner.revoke_grant)
    app.router.add_post('/workspace-pairing', runner.refresh_pairing)
    app.on_shutdown.append(runner.close)
    workspaces.register_routes(app)
    return app


def load_or_create_key(key_file: Path) -> str:
    if key_file.is_file():
        key = key_file.read_text(encoding='utf-8').strip()
        if not key:
            raise ValueError('The Runner key file is empty. Create a new key before starting.')
        return key
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(key)
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description='Run Claude Code and Codex for a Buddy server.')
    parser.add_argument('--host', default='127.0.0.1', help='Address to listen on (default: 127.0.0.1).')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument('--key-file', type=Path, default=None, help='Default: <state-dir>/key')
    parser.add_argument('--root', action='append', default=[], help='Approve one absolute project directory; repeat as needed.')
    parser.add_argument('--write', action='store_true', help='Approve edits of existing UTF-8 files in the selected roots.')
    parser.add_argument('--allow-host-execution', action='store_true', help='Approve commands as the Runner account; a working directory is not a sandbox.')
    parser.add_argument('--origin', action='append', default=[], help='Exact Buddy browser origin; replaces the defaults when supplied.')
    parser.add_argument('--allow-no-origin', action='store_true', help='Explicitly permit non-browser workspace clients.')
    parser.add_argument('--name', default=None, help='Execution host name shown to workspace clients.')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The Runner binds only to loopback. Use a separately reviewed authenticated private transport for remote access.')

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    key_file = args.key_file or args.state_dir / 'key'
    key = load_or_create_key(key_file)
    log.info('Buddy Runner on http://%s:%s; key in %s', args.host, args.port, key_file)
    origins = args.origin or [
        'http://127.0.0.1:8081', 'http://localhost:8081',
        'http://127.0.0.1:8082', 'http://localhost:8082',
    ]
    app = build_app(
        key, args.state_dir,
        roots=[{'path': directory, 'write': args.write, 'execute': args.allow_host_execution} for directory in args.root],
        origins=origins, host_name=args.name, allow_no_origin=args.allow_no_origin,
    )
    workspace_host = app['workspace_capabilities']
    log.info('Workspace host: %s (%s); instance %s', workspace_host.host['name'], workspace_host.host['platform'], workspace_host.host['id'])
    log.info('Approved project roots: %s; file write %s; host execution %s', len(args.root), args.write, args.allow_host_execution)
    if args.root:
        print(f'One-use workspace pairing code (expires in 5 minutes): {workspace_host.pairing_code}', flush=True)
    if args.allow_host_execution:
        log.warning('Commands run as the host OS account. Their working directory is not a sandbox.')
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == '__main__':
    main()
