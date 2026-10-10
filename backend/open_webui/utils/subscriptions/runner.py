"""Buddy Runner: lets a Buddy server start Claude Code and Codex on this computer.

Run it on the machine whose terminal, files, and CLI sign-ins the models
should use, then add it to Buddy under Admin Settings → Connections →
Subscriptions → Machines:

    python -m open_webui.utils.subscriptions.runner --port 8765

Every request needs ``Authorization: Bearer <key>``; the key is created in
``~/.buddy-runner/key`` on first start. The runner listens on 127.0.0.1 by
default, which Docker Desktop containers reach as ``host.docker.internal``.
Processes use plain pipes, so JSON lines pass through unchanged, and a
process stops when Buddy's connection to it closes.

Anyone holding the key can run any command as the user running this runner.
"""

import argparse
import asyncio
import hmac
import json
import logging
import os
import secrets
import socket
import sys
from pathlib import Path

from aiohttp import WSMsgType, web

from open_webui.utils.subscriptions.discovery import find_tool
from open_webui.utils.subscriptions.machines import start_local_process
from open_webui.utils.subscriptions.process import ProcessClosedError, run_command, subscription_env

log = logging.getLogger('buddy_runner')

RUNNER_VERSION = 1
DEFAULT_PORT = 8765
DEFAULT_STATE_DIR = Path.home() / '.buddy-runner'


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({'error': message}, status=status)


def _auth_middleware(key: str):
    @web.middleware
    async def check_key(request: web.Request, handler):
        supplied = request.headers.get('Authorization', '')
        expected = f'Bearer {key}'
        if not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
            return _json_error(401, 'Missing or wrong runner key.')
        return await handler(request)

    return check_key


class Runner:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.chat_dir = state_dir / 'chat'
        self.default_workspace = state_dir / 'workspace'
        self.temp_dir = state_dir / 'tmp'
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
        timeout = float(body.get('timeout') or 30)
        cwd = str(body.get('cwd') or self.chat_dir)
        try:
            returncode, stdout, stderr = await run_command(args, cwd, subscription_env(body.get('env')), timeout)
        except TimeoutError:
            return web.json_response({'timed_out': True})
        except OSError as error:
            return _json_error(400, f'Could not run {args[0]}: {error}')
        return web.json_response({'returncode': returncode, 'stdout': stdout, 'stderr': stderr})

    async def process(self, request: web.Request) -> web.WebSocketResponse:
        """Start a process and stream it over a WebSocket until it exits or Buddy disconnects."""
        websocket = web.WebSocketResponse(heartbeat=30, max_msg_size=0)
        await websocket.prepare(request)

        start = await websocket.receive_json(timeout=60)
        args = start.get('args')
        if start.get('type') != 'start' or not isinstance(args, list) or not args:
            await websocket.send_json({'type': 'error', 'message': 'Expected a start message with args.'})
            await websocket.close()
            return websocket

        try:
            process = start_local_process(
                [str(arg) for arg in args],
                str(start.get('cwd') or self.chat_dir),
                start.get('env') or None,
                start.get('temp_files') or None,
                str(self.temp_dir),
            )
        except OSError as error:
            await websocket.send_json({'type': 'error', 'message': str(error)})
            await websocket.close()
            return websocket

        log.info('Started %s (pid %s)', os.path.basename(args[0]), process.pid)
        await websocket.send_json({'type': 'started', 'pid': process.pid})

        async def forward_output():
            try:
                while True:
                    text = await process.read_text()
                    if text is None:
                        break
                    await websocket.send_json({'type': 'stdout', 'data': text})
                await process.finish_output(30)
                exit_event = {'type': 'exit', 'code': process.returncode, 'stderr': process.stderr_text()}
                # The process is done; remove its temporary files before reporting.
                process.kill()
                await websocket.send_json(exit_event)
            except (ConnectionResetError, RuntimeError):
                # Buddy disconnected; the handler below stops the process.
                pass

        forwarder = asyncio.create_task(forward_output())
        try:
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
                    process.kill()
        finally:
            process.kill()
            if not forwarder.done():
                forwarder.cancel()
            log.info('Stopped %s (pid %s)', os.path.basename(args[0]), process.pid)
        return websocket


def build_app(key: str, state_dir: Path) -> web.Application:
    runner = Runner(state_dir)
    app = web.Application(middlewares=[_auth_middleware(key)], client_max_size=64 * 1024 * 1024)
    app.router.add_get('/health', runner.health)
    app.router.add_post('/tools/find', runner.find_tool)
    app.router.add_post('/paths/check', runner.check_path)
    app.router.add_post('/run', runner.run)
    app.router.add_get('/process', runner.process)
    return app


def load_or_create_key(key_file: Path) -> str:
    if key_file.is_file():
        return key_file.read_text(encoding='utf-8').strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)
    key_file.write_text(key, encoding='utf-8')
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description='Run Claude Code and Codex for a Buddy server.')
    parser.add_argument('--host', default='127.0.0.1', help='Address to listen on (default: 127.0.0.1).')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument('--key-file', type=Path, default=None, help='Default: <state-dir>/key')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    key_file = args.key_file or args.state_dir / 'key'
    key = load_or_create_key(key_file)
    log.info('Buddy Runner on http://%s:%s; key in %s', args.host, args.port, key_file)
    web.run_app(build_app(key, args.state_dir), host=args.host, port=args.port, print=None)


if __name__ == '__main__':
    main()
