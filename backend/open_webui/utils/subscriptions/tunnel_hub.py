"""Buddy's side of the Runner tunnel: open connections, calls, and process streams.

Each connected computer keeps one WebSocket to this server (see
``buddy_runner/protocol.py``). The hub knows which machines are online;
``TunnelMachine`` in ``machines.py`` drives a machine through it. Connections
live in this process, which is why subscriptions need one backend worker.
"""

import asyncio
import itertools
import json
import logging

from buddy_runner import protocol
from open_webui.utils.subscriptions.events import SubscriptionError

log = logging.getLogger(__name__)


class RunnerCallError(SubscriptionError):
    """The Runner answered a call with an error."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class TunnelStream:
    """One process stream, with the channel methods RemoteProcess reads and writes through."""

    def __init__(self, connection: 'RunnerConnection', number: int, start_id: str | None):
        self._connection = connection
        self.number = number
        self.start_id = start_id
        # The Runner instance that received the start; a restart cannot vouch for it.
        self.instance_id = connection.instance_id
        self._events = asyncio.Queue()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def deliver(self, message) -> None:
        if not self._closed:
            self._events.put_nowait(message)

    def remote_closed(self) -> None:
        """The Runner closed the stream, or the connection dropped."""
        if not self._closed:
            self._closed = True
            self._events.put_nowait(None)

    async def receive_event(self, timeout: float):
        event = await asyncio.wait_for(self._events.get(), timeout)
        if event is None:
            raise ConnectionResetError('The Runner stream closed before it answered.')
        return event

    async def events(self):
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def send_text(self, text: str) -> None:
        raise ConnectionResetError('A tunnel stream receives its start in the open message.')

    async def send_json(self, message: dict) -> None:
        if self._closed:
            raise ConnectionResetError('The Runner stream is closed.')
        await self._connection.send({'t': 'stream', 's': self.number, 'message': message})

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._events.put_nowait(None)
            try:
                await self._connection.send({'t': 'close', 's': self.number})
            except ConnectionResetError:
                pass
        self._connection.forget_stream(self.number)


class RunnerConnection:
    """One connected Runner: send calls and open streams; ``serve`` reads its messages."""

    def __init__(self, socket, machine_id: str, owner_id: str, instance_id: str, info: dict):
        self._socket = socket
        self.machine_id = machine_id
        self.owner_id = owner_id
        self.instance_id = instance_id
        self.info = info
        self.closed = False
        self._pending: dict[int, asyncio.Future] = {}
        self._streams: dict[int, TunnelStream] = {}
        self._call_numbers = itertools.count(1)
        self._stream_numbers = itertools.count(1)
        self._send_lock = asyncio.Lock()

    async def send(self, frame: dict) -> None:
        if self.closed:
            raise ConnectionResetError('The Runner disconnected.')
        text = json.dumps(frame)
        async with self._send_lock:
            try:
                await self._socket.send_text(text)
            except Exception as error:
                raise ConnectionResetError(f'The Runner disconnected: {error}') from error

    async def serve(self) -> None:
        """Read the Runner's messages until the connection ends."""
        try:
            while not self.closed:
                text = await self._socket.receive_text()
                self._dispatch(json.loads(text))
        except (protocol.ProtocolError, ValueError) as error:
            log.warning('Runner %s broke the tunnel protocol: %s', self.machine_id, error)
            await self.close(protocol.CLOSE_BAD_HELLO, 'Unexpected message')
        except Exception as error:
            # A disconnect surfaces as the socket's own exception type.
            log.info('Runner %s disconnected: %s', self.machine_id, error)
        finally:
            self._shut()

    def _dispatch(self, frame) -> None:
        kind = protocol.frame_type(frame)
        if kind == 'result':
            future = self._pending.get(protocol.call_id(frame))
            if future is not None and not future.done():
                future.set_result(frame)
        elif kind == 'stream':
            stream = self._streams.get(protocol.stream_number(frame))
            if stream is not None:
                stream.deliver(frame.get('message'))
        elif kind == 'close':
            stream = self._streams.get(protocol.stream_number(frame))
            if stream is not None:
                stream.remote_closed()
        else:
            raise protocol.ProtocolError(f'Unexpected tunnel message: {kind}')

    def _shut(self) -> None:
        self.closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionResetError('The Runner disconnected.'))
        self._pending.clear()
        for stream in list(self._streams.values()):
            stream.remote_closed()

    async def call(self, method: str, params: dict | None = None, timeout: float = protocol.CALL_TIMEOUT_SECONDS):
        if len(self._pending) >= protocol.MAX_PENDING_CALLS:
            raise SubscriptionError('The Runner is busy. Try again in a moment.')
        number = next(self._call_numbers)
        future = asyncio.get_running_loop().create_future()
        self._pending[number] = future
        try:
            await self.send({'t': 'call', 'id': number, 'method': method, 'params': params or {}})
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            raise SubscriptionError(f'The Runner did not answer {method} in time.') from error
        except ConnectionResetError as error:
            raise SubscriptionError('The Runner disconnected.') from error
        finally:
            self._pending.pop(number, None)
        if reply.get('ok') is True:
            return reply.get('result')
        error = reply.get('error') if isinstance(reply.get('error'), dict) else {}
        status = error.get('status') if isinstance(error.get('status'), int) else 500
        message = error.get('message') if isinstance(error.get('message'), str) else 'The Runner refused the request.'
        raise RunnerCallError(status, message)

    async def open_stream(self, start: dict) -> TunnelStream:
        if len(self._streams) >= protocol.MAX_OPEN_STREAMS:
            raise SubscriptionError('The Runner has too many processes open.')
        number = next(self._stream_numbers)
        stream = TunnelStream(self, number, start.get('start_id'))
        self._streams[number] = stream
        try:
            await self.send({'t': 'open', 's': number, 'start': start})
        except ConnectionResetError as error:
            self._streams.pop(number, None)
            stream.remote_closed()
            raise SubscriptionError('The Runner disconnected.') from error
        return stream

    def forget_stream(self, number: int) -> None:
        self._streams.pop(number, None)

    async def close(self, code: int, reason: str = '') -> None:
        try:
            await self._socket.close(code, reason)
        except Exception as error:
            log.debug('Could not close the connection of Runner %s: %s', self.machine_id, error)
        self._shut()


class RunnerHub:
    """Which machines are connected, and a way to wait for one to come back."""

    def __init__(self):
        self._connections: dict[str, RunnerConnection] = {}
        self._waiters: dict[str, set[asyncio.Future]] = {}

    def connection(self, machine_id: str) -> RunnerConnection | None:
        connection = self._connections.get(machine_id)
        if connection is None or connection.closed:
            return None
        return connection

    def is_online(self, machine_id: str) -> bool:
        return self.connection(machine_id) is not None

    def connections(self) -> list[RunnerConnection]:
        return [connection for connection in self._connections.values() if not connection.closed]

    async def wait_connection(self, machine_id: str, timeout: float) -> RunnerConnection | None:
        connection = self.connection(machine_id)
        if connection is not None or timeout <= 0:
            return connection
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(machine_id, set()).add(waiter)
        try:
            return await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            return None
        finally:
            waiters = self._waiters.get(machine_id)
            if waiters is not None:
                waiters.discard(waiter)
                if not waiters:
                    self._waiters.pop(machine_id, None)

    def attach(self, connection: RunnerConnection) -> RunnerConnection | None:
        """Make this the machine's connection; returns the one it replaces, which the caller closes."""
        previous = self._connections.get(connection.machine_id)
        self._connections[connection.machine_id] = connection
        for waiter in self._waiters.pop(connection.machine_id, set()):
            if not waiter.done():
                waiter.set_result(connection)
        if previous is connection or (previous is not None and previous.closed):
            return None
        return previous

    def detach(self, connection: RunnerConnection) -> None:
        if self._connections.get(connection.machine_id) is connection:
            self._connections.pop(connection.machine_id)

    async def disconnect(self, machine_id: str, code: int, reason: str) -> None:
        connection = self._connections.pop(machine_id, None)
        if connection is not None:
            await connection.close(code, reason)


HUB = RunnerHub()
