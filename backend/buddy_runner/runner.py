"""Buddy Runner: lets a Buddy server start Claude Code and Codex on this computer.

Run it on the machine whose terminal, files, and CLI sign-ins the models
should use, then add it to Buddy under Admin Settings → AI plans → Your
Runner addresses. (The Buddy app runs it in connect mode instead; see
buddy_runner/connect.py.)

    python -m buddy_runner --port 8765

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
import ssl
import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import WSMsgType, web

from buddy_runner.discovery import find_tool
from buddy_runner.keys import load_or_create_key
from buddy_runner.local_process import start_local_process
from buddy_runner.process import OutputLimitError, ProcessClosedError, run_command, subscription_env
from buddy_runner.workspace_capabilities import WorkspaceCapabilities

log = logging.getLogger('buddy_runner')

RUNNER_VERSION = 1
DEFAULT_PORT = 8765
DEFAULT_STATE_DIR = Path.home() / '.buddy-runner'
MAX_PENDING_STDIN_BYTES = 32 * 1024 * 1024
MAX_PENDING_STDIN_ITEMS = 128
MAX_WEBSOCKET_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_STOPPED_PROCESS_RECORDS = 4096


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({'error': message}, status=status)


async def _refuse_start(channel, message: str) -> None:
    """Report a start that created no process, so Buddy keeps no cleanup debt."""
    await channel.send_json({'type': 'start_failed', 'message': message})


async def _finish_owned_cleanup(task: asyncio.Task) -> None:
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Keep disposal ownership even if a disconnected handler is cancelled
        # repeatedly. Propagate cancellation only after cleanup has finished.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        task.result()
        raise


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


class RunnerError(Exception):
    """A request the Runner refuses, with the HTTP status the listener reports."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class WebSocketProcessChannel:
    """A process stream over its own listener WebSocket.

    Process channels give ``Runner.serve_process`` one process's messages
    whatever carries them: ``closed``, ``receive_json(timeout)``,
    ``commands()``, ``send_json(message)`` and ``close(code)``.
    """

    def __init__(self, websocket: web.WebSocketResponse):
        self.websocket = websocket

    @property
    def closed(self) -> bool:
        return self.websocket.closed

    async def receive_json(self, timeout: float):
        return await self.websocket.receive_json(timeout=timeout)

    async def commands(self):
        async for message in self.websocket:
            if message.type != WSMsgType.TEXT:
                continue
            yield json.loads(message.data)

    async def send_json(self, message: dict) -> None:
        await self.websocket.send_json(message)

    async def close(self, code: int | None = None) -> None:
        if code is None:
            await self.websocket.close()
        else:
            await self.websocket.close(code=code)


def _valid_start_id(start: dict) -> str | None:
    start_id = start.get('start_id')
    if isinstance(start_id, str) and 0 < len(start_id) <= 128:
        return start_id
    return None


class Runner:
    def __init__(self, state_dir: Path, workspaces: WorkspaceCapabilities):
        self.state_dir = state_dir
        self.chat_dir = state_dir / 'chat'
        self.default_workspace = state_dir / 'workspace'
        self.temp_dir = state_dir / 'tmp'
        self.workspaces = workspaces
        self.processes = set()
        self.stdin_workers = {}
        self.channels = set()
        # The channel that started each process, for shutdown acknowledgements.
        self.process_channels = {}
        # After a lost connection, Buddy asks about a process by the ID sent in
        # its startup reply. Only verified cleanup moves an ID to the stopped
        # records, which keep the most recent IDs.
        self.running_process_ids = {}
        self.stopped_process_ids = OrderedDict()
        # Over the tunnel, Buddy names each startup before sending it, so after
        # a lost connection it can ask about a startup whose reply never came.
        # A running process keeps its startup name until its cleanup is
        # verified; finished and refused startups join the bounded records.
        self.running_start_ids = {}
        self.process_start_ids = {}
        self.finished_start_ids = OrderedDict()
        self.command_requests = set()
        self.closing = False
        self._shutdown_task = None
        for directory in (self.chat_dir, self.default_workspace, self.temp_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict:
        return {
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

    def locate_tool(self, tool: str, configured_path: str) -> str | None:
        return find_tool(tool, configured_path)

    def path_exists(self, path: str, kind: str) -> bool:
        if kind == 'dir':
            return Path(path).is_dir()
        return Path(path).is_file()

    def at_process_limit(self) -> bool:
        return len(self.command_requests) + len(self.processes) >= 16

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(self.describe())

    async def find_tool(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            path = self.locate_tool(str(body.get('tool')), str(body.get('configured_path') or ''))
        except ValueError as error:
            return _json_error(400, str(error))
        return web.json_response({'path': path})

    async def check_path(self, request: web.Request) -> web.Response:
        body = await request.json()
        exists = self.path_exists(str(body.get('path') or ''), body.get('kind'))
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
        if self.at_process_limit():
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
        if self.at_process_limit():
            return _json_error(429, 'The Runner process limit was reached.')
        websocket = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_WEBSOCKET_MESSAGE_BYTES)
        await websocket.prepare(request)
        await self.serve_process(WebSocketProcessChannel(websocket))
        return websocket

    async def serve_process(self, channel, start: dict | None = None) -> None:
        """Start one process and stream it over ``channel`` until it exits or the channel closes.

        ``start`` is the start message when the transport has already read it;
        otherwise the first message on the channel must be one.
        """
        self.channels.add(channel)
        process = None
        process_id = None
        start_id = None
        forwarder = None
        stdin_queue = asyncio.Queue()
        pending_stdin_bytes = 0
        pending_stdin_items = 0
        stdin_closed = False
        failure_reporting = False

        def queue_stdin(text: str | None) -> None:
            nonlocal pending_stdin_bytes, pending_stdin_items
            size = len(text.encode('utf-8')) if text is not None else 0
            if (
                pending_stdin_bytes + size > MAX_PENDING_STDIN_BYTES
                or pending_stdin_items >= MAX_PENDING_STDIN_ITEMS
            ):
                raise OutputLimitError('The Runner stdin buffer limit was reached')
            pending_stdin_bytes += size
            pending_stdin_items += 1
            stdin_queue.put_nowait((text, size))

        async def write_stdin():
            nonlocal pending_stdin_bytes, pending_stdin_items, stdin_closed
            while True:
                text, size = await stdin_queue.get()
                try:
                    if text is None:
                        await asyncio.to_thread(process.close_stdin)
                        return
                    await process.write(text)
                except ProcessClosedError:
                    stdin_closed = True
                    return
                finally:
                    pending_stdin_bytes -= size
                    pending_stdin_items -= 1

        async def forward_output():
            nonlocal failure_reporting
            try:
                while True:
                    text = await process.read_text()
                    if text is None:
                        break
                    await channel.send_json({'type': 'stdout', 'data': text})
                await process.finish_output(30)
                # The process is done; remove its temporary files before reporting.
                await process.close()
                if failure_reporting:
                    # The input handler owns the failed exit acknowledgement.
                    # Native cleanup can produce EOF before it reports the error.
                    return
                exit_event = {'type': 'exit', 'code': process.returncode, 'stderr': process.stderr_text()}
                await channel.send_json(exit_event)
            except ProcessClosedError as error:
                failure_reporting = True
                try:
                    await self._report_process_failure(process, channel, error)
                except (OSError, RuntimeError, TimeoutError) as cleanup_error:
                    log.warning('Could not confirm failed process cleanup: %s', cleanup_error)
            except (OSError, ConnectionResetError, RuntimeError, TimeoutError):
                # Closing wakes the handler and its process-cleanup finally.
                await channel.close()
            else:
                await channel.close()

        try:
            if start is None:
                start = await channel.receive_json(timeout=60)
            args = start.get('args') if isinstance(start, dict) else None
            if isinstance(start, dict):
                start_id = _valid_start_id(start)
            if start_id is not None and (start_id in self.running_start_ids or start_id in self.finished_start_ids):
                # A repeated name would make a later stop question ambiguous.
                start_id = None
                await _refuse_start(channel, 'This startup was already received.')
                return
            if (
                not isinstance(start, dict) or start.get('type') != 'start'
                or not isinstance(args, list) or not args
                or not all(isinstance(arg, str) and '\0' not in arg for arg in args)
            ):
                await _refuse_start(channel, 'Expected a start message with string args.')
                return
            if self.closing or self.at_process_limit():
                await _refuse_start(channel, 'The Runner is unavailable or at its process limit.')
                return
            try:
                process = start_local_process(
                    args, str(start.get('cwd') or self.chat_dir), start.get('env') or None,
                    start.get('temp_files') or None, str(self.temp_dir),
                )
            except (OSError, ValueError, TypeError) as error:
                # A missing working folder or CLI fails before anything runs.
                await _refuse_start(channel, f'Could not start {os.path.basename(args[0])}: {error}')
                return
            self.processes.add(process)
            self.process_channels[process] = channel
            process_id = secrets.token_urlsafe(16)
            self.running_process_ids[process_id] = process
            if start_id is not None:
                self.running_start_ids[start_id] = process_id
                self.process_start_ids[process_id] = start_id
            log.info('Started %s (pid %s)', os.path.basename(args[0]), process.pid)
            # Startup acknowledgement is covered by the same cleanup finally.
            await channel.send_json({'type': 'started', 'pid': process.pid, 'id': process_id})
            forwarder = asyncio.create_task(forward_output())
            self.stdin_workers[process] = asyncio.create_task(write_stdin())
            async for command in channel.commands():
                kind = command.get('type')
                if kind == 'stdin':
                    if not stdin_closed:
                        queue_stdin(str(command.get('data') or ''))
                elif kind == 'close_stdin':
                    if not stdin_closed:
                        queue_stdin(None)
                        stdin_closed = True
                elif kind == 'kill':
                    await process.close()
                    # Closing the channel is not proof of native process cleanup.
                    # Acknowledge only after the whole owned process tree stops.
                    if not channel.closed:
                        await channel.send_json({'type': 'stopped', 'pid': process.pid})
        except ProcessClosedError as error:
            failure_reporting = True
            await self._report_process_failure(process, channel, error)
        except (OSError, ValueError, TimeoutError, ConnectionResetError, RuntimeError) as error:
            if not channel.closed:
                reply = {'type': 'error', 'message': str(error)}
                if process is None:
                    # No process exists yet, so this failure started nothing.
                    reply['type'] = 'start_failed'
                try:
                    await channel.send_json(reply)
                except (ConnectionResetError, RuntimeError):
                    pass
        finally:
            if process is None and start_id is not None:
                self._remember_finished_start(start_id)
            cleanup = asyncio.create_task(self._dispose_connection(process, process_id, forwarder, channel))
            await _finish_owned_cleanup(cleanup)

    async def _report_process_failure(self, process, channel, error: ProcessClosedError) -> None:
        """Report failed execution only after its native cleanup is verified."""
        try:
            await process.close()
            if not channel.closed:
                code = process.returncode
                if not isinstance(code, int) or code == 0:
                    code = 1
                stderr = process.stderr_text()
                detail = f'{type(error).__name__}: {error}'
                if stderr:
                    detail = f'{stderr}\n{detail}'
                await channel.send_json({'type': 'exit', 'code': code, 'stderr': detail})
        finally:
            # An unverified cleanup releases the failed transport, without an
            # exit acknowledgement that could falsely authorize fresh work.
            await channel.close()

    async def _dispose_connection(self, process, process_id, forwarder, channel) -> None:
        try:
            if process is not None:
                await process.close()
                await self._forget_stopped_process(process, process_id)
                log.info('Stopped process %s', process.pid)
        finally:
            if forwarder is not None and not forwarder.done():
                forwarder.cancel()
            if forwarder is not None:
                await asyncio.gather(forwarder, return_exceptions=True)
            self.channels.discard(channel)
            await channel.close()

    async def _stop_stdin_worker(self, process) -> None:
        worker = self.stdin_workers.pop(process, None)
        if worker is not None:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    def _remember_finished_start(self, start_id: str) -> None:
        self.finished_start_ids[start_id] = True
        if len(self.finished_start_ids) > MAX_STOPPED_PROCESS_RECORDS:
            self.finished_start_ids.popitem(last=False)

    async def _forget_stopped_process(self, process, process_id: str | None) -> None:
        """Release a process; call only after its native cleanup was verified."""
        if process_id is not None:
            self.running_process_ids.pop(process_id, None)
            self.stopped_process_ids[process_id] = True
            if len(self.stopped_process_ids) > MAX_STOPPED_PROCESS_RECORDS:
                self.stopped_process_ids.popitem(last=False)
            start_id = self.process_start_ids.pop(process_id, None)
            if start_id is not None:
                self.running_start_ids.pop(start_id, None)
                self._remember_finished_start(start_id)
        await self._stop_stdin_worker(process)
        self.processes.discard(process)
        self.process_channels.pop(process, None)

    async def stop_known_process(self, process_id: str) -> dict:
        """Stop a process whose connection Buddy lost, and confirm its cleanup."""
        if process_id in self.stopped_process_ids:
            return {'stopped': True}
        process = self.running_process_ids.get(process_id)
        if process is None:
            # A restarted Runner cannot vouch for an earlier run's processes.
            raise RunnerError(404, 'This Runner has no record of that process. It may have restarted.')
        try:
            await process.close()
        except (OSError, RuntimeError, TimeoutError) as error:
            raise RunnerError(500, f'Could not confirm that process {process.pid} stopped: {error}') from error
        await self._forget_stopped_process(process, process_id)
        return {'stopped': True}

    async def stop_named_start(self, start_id: str) -> dict:
        """Stop whatever a named tunnel startup created, and confirm its cleanup.

        A name this Runner has no record of was never received, or its
        process stopped long enough ago to leave the records. Either way
        nothing runs for it; ``known`` tells Buddy which answer this is, and
        Buddy trusts it only when the Runner has not restarted since.
        """
        if start_id in self.finished_start_ids:
            return {'stopped': True, 'known': True}
        process_id = self.running_start_ids.get(start_id)
        if process_id is None:
            return {'stopped': True, 'known': False}
        result = await self.stop_known_process(process_id)
        result['known'] = True
        return result

    async def stop_process(self, request: web.Request) -> web.Response:
        try:
            result = await self.stop_known_process(request.match_info['process_id'])
        except RunnerError as error:
            return _json_error(error.status, error.message)
        return web.json_response(result)

    async def _acknowledge_stopped(self, process) -> None:
        """Confirm verified cleanup to Buddy before its connection closes."""
        channel = self.process_channels.get(process)
        if channel is None or channel.closed:
            return
        try:
            await channel.send_json({'type': 'stopped', 'pid': process.pid})
        except (ConnectionResetError, RuntimeError):
            pass

    async def operator_grants(self, request: web.Request) -> web.Response:
        return web.json_response({'grants': self.workspaces.list_grants()})

    async def revoke_grant(self, request: web.Request) -> web.Response:
        revoked = await self.workspaces.revoke_grant(request.match_info['grant_id'])
        return web.json_response({'revoked': revoked})

    async def refresh_pairing(self, request: web.Request) -> web.Response:
        # The host operator can pair another device without stopping provider CLIs.
        # This route never approves a directory or changes an existing session.
        return web.json_response(self.workspaces.issue_pairing_code())

    async def close(self, app: web.Application | None = None) -> None:
        self.closing = True
        retry_failed_shutdown = self._shutdown_task is not None and self._shutdown_task.done() and (
            self._shutdown_task.cancelled() or self._shutdown_task.exception() is not None
        )
        if self._shutdown_task is None or retry_failed_shutdown:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        await _finish_owned_cleanup(self._shutdown_task)

    async def _shutdown(self) -> None:
        # A failed process must not skip cleanup of other owned transports and
        # finite commands. Keep the failure visible after every cleanup attempt.
        processes = list(self.processes)
        errors = await asyncio.gather(*(process.close() for process in processes), return_exceptions=True)
        for process, result in zip(processes, errors):
            if not isinstance(result, BaseException):
                await self._stop_stdin_worker(process)
                # A bare close would leave Buddy unsure the process stopped.
                await self._acknowledge_stopped(process)
        errors.extend(await asyncio.gather(
            *(channel.close(code=1001) for channel in list(self.channels)), return_exceptions=True
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
    app.router.add_post('/processes/{process_id}/stop', runner.stop_process)
    app.router.add_get('/workspace-grants', runner.operator_grants)
    app.router.add_delete('/workspace-grants/{grant_id}', runner.revoke_grant)
    app.router.add_post('/workspace-pairing', runner.refresh_pairing)
    app.on_shutdown.append(runner.close)
    workspaces.register_routes(app)
    return app


def _check_server_certificate_validity(certificate_data: bytes) -> None:
    from cryptography import x509

    leaf = x509.load_pem_x509_certificate(certificate_data)
    valid_from = getattr(leaf, 'not_valid_before_utc', None)
    expires = getattr(leaf, 'not_valid_after_utc', None)
    if valid_from is None:
        valid_from = leaf.not_valid_before.replace(tzinfo=timezone.utc)
        expires = leaf.not_valid_after.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now < valid_from:
        raise ValueError(f'The Runner TLS certificate is not valid until {valid_from.isoformat()}. Supply a currently valid certificate.')
    if now >= expires:
        raise ValueError(f'The Runner TLS certificate expired on {expires.isoformat()}. Renew it before starting.')
    if expires - now <= timedelta(days=30):
        log.warning(
            'Runner TLS certificate expires on %s. Renew the host certificate and restart the Runner to load it; active CLI and terminal sessions will stop.',
            expires.isoformat(),
        )


def build_server_tls_context(certificate: Path | None, private_key: Path | None) -> ssl.SSLContext | None:
    if certificate is None and private_key is None:
        return None
    if certificate is None or private_key is None:
        raise ValueError('Supply --tls-cert and --tls-key together for HTTPS.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        # An empty password explicitly disables OpenSSL's interactive prompt.
        # Operators supply an unencrypted key; this process never creates one.
        certificate_data = certificate.read_bytes()
        if b'PRIVATE KEY-----' in certificate_data:
            raise ValueError('The server certificate chain must not contain a private key; keep the key in its separate host-only file.')
        _check_server_certificate_validity(certificate_data)
        # OpenSSL requires a filename. Load the exact public bytes validated
        # above, even if the operator replaces the original during startup.
        # The private key stays at its original host-only path.
        with tempfile.TemporaryDirectory(prefix='buddy-runner-public-cert-') as directory:
            with tempfile.NamedTemporaryFile(dir=directory, suffix='.pem', delete=False) as snapshot:
                snapshot.write(certificate_data)
            context.load_cert_chain(snapshot.name, str(private_key), password='')
    except (OSError, ValueError) as error:
        raise ValueError(f'Could not load the Runner TLS certificate and private key: {error}') from error
    return context


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ('run', 'enroll'):
        # Connect mode, which the Buddy app uses; imported here because it builds on this module.
        from buddy_runner.connect import main as connect_main

        sys.exit(connect_main(sys.argv[1:]))
    parser = argparse.ArgumentParser(prog='buddy-runner', description='Run Claude Code and Codex for a Buddy server.')
    parser.add_argument('--host', default='127.0.0.1', help='Address to listen on (default: 127.0.0.1).')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument('--key-file', type=Path, default=None, help='Default: <state-dir>/key')
    parser.add_argument('--tls-cert', type=Path, default=None, help='Operator-supplied PEM server certificate chain for HTTPS.')
    parser.add_argument('--tls-key', type=Path, default=None, help='Operator-supplied unencrypted PEM private key; use with --tls-cert.')
    parser.add_argument('--root', action='append', default=[], help='Approve one absolute project directory; repeat as needed.')
    parser.add_argument('--write', action='store_true', help='Approve edits of existing UTF-8 files in the selected roots.')
    parser.add_argument('--allow-host-execution', action='store_true', help='Approve commands as the Runner account; a working directory is not a sandbox.')
    parser.add_argument('--origin', action='append', default=[], help='Exact Buddy browser origin; replaces the defaults when supplied.')
    parser.add_argument('--allow-no-origin', action='store_true', help='Explicitly permit non-browser workspace clients.')
    parser.add_argument('--name', default=None, help='Execution host name shown to workspace clients.')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The Runner binds only to loopback. Use a separately reviewed authenticated private transport for remote access.')
    try:
        tls_context = build_server_tls_context(args.tls_cert, args.tls_key)
    except ValueError as error:
        # TLS misconfiguration must fail before persistent Runner credentials
        # or state directories are created.
        parser.error(str(error))

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    key_file = args.key_file or args.state_dir / 'key'
    key = load_or_create_key(key_file, state_dir=args.state_dir)
    scheme = 'https' if tls_context is not None else 'http'
    log.info('Buddy Runner on %s://%s:%s; key in %s', scheme, args.host, args.port, key_file)
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
    web.run_app(app, host=args.host, port=args.port, ssl_context=tls_context, print=None)


if __name__ == '__main__':
    main()
