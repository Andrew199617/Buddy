"""Connect this Runner out to its Buddy server and serve it over one WebSocket.

See ``protocol.py`` for the messages. Process streams reuse
``Runner.serve_process``, so a stream the server closes, or a connection that
drops, stops its process with the same verified cleanup as the listener.
"""

import asyncio
import json
import logging
import os
import random
import ssl
import time

import aiohttp

from buddy_runner import protocol
from buddy_runner.runner import RUNNER_VERSION, Runner, RunnerError
from buddy_runner.urls import websocket_url

log = logging.getLogger('buddy_runner.tunnel')

# A connection that stayed up this long resets the reconnect delay.
STABLE_CONNECTION_SECONDS = 60
MAX_RECONNECT_DELAY_SECONDS = 60


class PermanentDisconnect(Exception):
    """The server closed the connection for a reason reconnecting cannot fix."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason or f'Buddy closed the connection ({code}).')
        self.code = code
        self.reason = reason


def server_ssl_context() -> ssl.SSLContext:
    """System trust, plus ``BUDDY_SERVER_CA_FILE`` for a server with a private certificate authority."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    ca_file = os.environ.get('BUDDY_SERVER_CA_FILE')
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    return context


class TunnelStreamChannel:
    """One process stream over the tunnel, with the methods ``Runner.serve_process`` uses."""

    def __init__(self, tunnel: 'Tunnel', number: int):
        self._tunnel = tunnel
        self.number = number
        self.closed = False
        self._incoming = asyncio.Queue()

    def deliver(self, message) -> None:
        if not self.closed:
            self._incoming.put_nowait(message)

    def remote_closed(self) -> None:
        """The server closed the stream, or the connection dropped."""
        if not self.closed:
            self.closed = True
            self._incoming.put_nowait(None)

    async def receive_json(self, timeout: float):
        message = await asyncio.wait_for(self._incoming.get(), timeout)
        if message is None:
            raise ConnectionResetError('The Buddy stream closed.')
        return message

    async def commands(self):
        while True:
            message = await self._incoming.get()
            if message is None:
                return
            if isinstance(message, dict):
                yield message

    async def send_json(self, message: dict) -> None:
        if self.closed:
            raise ConnectionResetError('The Buddy stream is closed.')
        await self._tunnel.send({'t': 'stream', 's': self.number, 'message': message})

    async def close(self, code: int | None = None) -> None:
        if self.closed:
            self._tunnel.forget_stream(self.number)
            return
        self.closed = True
        self._incoming.put_nowait(None)
        try:
            await self._tunnel.send({'t': 'close', 's': self.number})
        except ConnectionResetError:
            pass
        self._tunnel.forget_stream(self.number)


class Tunnel:
    """Serves one open connection: calls, and a process per stream."""

    def __init__(self, websocket, runner: Runner):
        self._websocket = websocket
        self._runner = runner
        self._streams: dict[int, TunnelStreamChannel] = {}
        self._tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()
        self._methods = {}
        self._register('health', self._health)
        self._register('tools.find', self._find_tool)
        self._register('paths.check', self._check_path)
        self._register('process.stop', self._stop_process)
        self._register('workspace.request', self._workspace_request)

    def _register(self, method: str, handler) -> None:
        self._methods[method] = handler

    async def send(self, frame: dict) -> None:
        if self._websocket.closed:
            raise ConnectionResetError('The Buddy connection is closed.')
        text = json.dumps(frame)
        async with self._send_lock:
            try:
                await self._websocket.send_str(text)
            except (ConnectionResetError, RuntimeError, aiohttp.ClientError) as error:
                raise ConnectionResetError(f'The Buddy connection closed: {error}') from error

    def forget_stream(self, number: int) -> None:
        self._streams.pop(number, None)

    def _spawn(self, operation) -> None:
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def serve(self) -> None:
        try:
            async for message in self._websocket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                self._dispatch(json.loads(message.data))
        finally:
            # Every open stream stops its process, as when a listener's
            # WebSocket closes; wait until each has confirmed its cleanup.
            for channel in list(self._streams.values()):
                channel.remote_closed()
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            # Pages relayed through this connection cannot reach their sessions any more.
            await self._runner.workspaces.end_relay_sessions()

    def _dispatch(self, frame) -> None:
        kind = protocol.frame_type(frame)
        if kind == 'call':
            self._spawn(self._answer(frame))
        elif kind == 'open':
            self._open(frame)
        elif kind == 'stream':
            channel = self._streams.get(protocol.stream_number(frame))
            if channel is not None:
                channel.deliver(frame.get('message'))
        elif kind == 'close':
            channel = self._streams.get(protocol.stream_number(frame))
            if channel is not None:
                channel.remote_closed()
        else:
            raise protocol.ProtocolError(f'Unknown tunnel message: {kind}')

    def _open(self, frame: dict) -> None:
        number = protocol.stream_number(frame)
        if number in self._streams:
            raise protocol.ProtocolError('That stream is already open.')
        start = frame.get('start')
        if not isinstance(start, dict):
            # serve_process refuses a start that is not a start message.
            start = {}
        channel = TunnelStreamChannel(self, number)
        self._streams[number] = channel
        self._spawn(self._runner.serve_process(channel, start=start))

    async def _answer(self, frame: dict) -> None:
        number = protocol.call_id(frame)
        params = frame.get('params')
        if not isinstance(params, dict):
            params = {}
        handler = self._methods.get(frame.get('method'))
        try:
            if handler is None:
                raise RunnerError(404, 'This Runner does not know that request. It may need an update.')
            reply = {'t': 'result', 'id': number, 'ok': True, 'result': await handler(params)}
        except RunnerError as error:
            reply = {'t': 'result', 'id': number, 'ok': False, 'error': {'status': error.status, 'message': error.message}}
        except (ValueError, TypeError, OSError) as error:
            reply = {'t': 'result', 'id': number, 'ok': False, 'error': {'status': 400, 'message': str(error)}}
        try:
            await self.send(reply)
        except ConnectionResetError:
            pass

    async def _health(self, params: dict) -> dict:
        return self._runner.describe()

    async def _find_tool(self, params: dict) -> dict:
        tool = str(params.get('tool'))
        configured_path = str(params.get('configured_path') or '')
        return {'path': self._runner.locate_tool(tool, configured_path)}

    async def _check_path(self, params: dict) -> dict:
        exists = await self._runner.check_path_exists(str(params.get('path') or ''), params.get('kind'))
        return {'exists': exists}

    async def _workspace_request(self, params: dict) -> dict:
        status, body = await self._runner.workspaces.relay(
            params.get('client_id'), params.get('method'), params.get('path'), params.get('query'), params.get('body'),
        )
        return {'status': status, 'body': body}

    async def _stop_process(self, params: dict) -> dict:
        process_id = params.get('process_id')
        if isinstance(process_id, str) and process_id:
            return await self._runner.stop_known_process(process_id)
        start_id = params.get('start_id')
        if isinstance(start_id, str) and start_id:
            return await self._runner.stop_named_start(start_id)
        raise RunnerError(400, 'Name the process or startup to stop.')


def hello_message(machine_id: str, credential: str, runner: Runner) -> dict:
    return {
        't': 'hello',
        'protocol': protocol.PROTOCOL_VERSION,
        'machine_id': machine_id,
        'credential': credential,
        'runner': {'version': RUNNER_VERSION, 'instance_id': runner.workspaces.host['id']},
        'info': runner.describe(),
    }


async def _read_welcome(websocket) -> dict:
    reply = await websocket.receive(timeout=protocol.HELLO_TIMEOUT_SECONDS)
    if reply.type == aiohttp.WSMsgType.TEXT:
        welcome = json.loads(reply.data)
        if protocol.frame_type(welcome) != 'welcome':
            raise protocol.ProtocolError('Buddy did not welcome this Runner.')
        return welcome
    code = websocket.close_code
    reason = ''
    if reply.type == aiohttp.WSMsgType.CLOSE and isinstance(reply.extra, str):
        reason = reply.extra
    if code in protocol.PERMANENT_CLOSE_CODES:
        raise PermanentDisconnect(code, reason)
    raise ConnectionResetError(reason or f'Buddy closed the connection ({code}).')


async def connect_and_serve(server_url: str, machine_id: str, credential: str, runner: Runner, on_status=None) -> None:
    """Serve one connection until it ends; PermanentDisconnect means stop trying."""
    url = websocket_url(server_url, protocol.CONNECT_PATH)
    ssl_context = None
    if url.startswith('wss://'):
        ssl_context = server_ssl_context()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            url,
            heartbeat=protocol.HEARTBEAT_SECONDS,
            max_msg_size=protocol.MAX_MESSAGE_BYTES,
            ssl=ssl_context,
        ) as websocket:
            await websocket.send_str(json.dumps(hello_message(machine_id, credential, runner)))
            welcome = await _read_welcome(websocket)
            machine_name = (welcome.get('machine') or {}).get('name') or machine_id
            log.info('Connected to %s as %s', server_url, machine_name)
            if on_status is not None:
                on_status('connected', machine_name)
            await Tunnel(websocket, runner).serve()
            code = websocket.close_code
            if code in protocol.PERMANENT_CLOSE_CODES:
                raise PermanentDisconnect(code, '')


async def run_tunnel(
    server_url: str, machine_id: str, credential: str, runner: Runner, *,
    on_status=None, connect=connect_and_serve, sleep=asyncio.sleep,
) -> None:
    """Keep the connection up, reconnecting with growing, jittered delays.

    Returns only by raising PermanentDisconnect, for example when the machine
    was removed in Buddy.
    """
    delay = 1.0
    while True:
        if on_status is not None:
            on_status('connecting', server_url)
        started = time.monotonic()
        try:
            await connect(server_url, machine_id, credential, runner, on_status)
        except PermanentDisconnect:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as error:
            log.warning('Could not stay connected to Buddy: %s', error)
        if time.monotonic() - started >= STABLE_CONNECTION_SECONDS:
            delay = 1.0
        if on_status is not None:
            on_status('offline', server_url)
        # Full jitter keeps many Runners from reconnecting at the same moment.
        await sleep(random.uniform(0, delay))
        delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)
