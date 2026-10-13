"""Where the Buddy app's Runner connects, and how a computer is connected in the first place.

Routes:
  POST      /enrollments — a signed-in user asks to connect this PC; returns a 60-second, single-use token
  POST      /enroll      — the Buddy app trades that token for this computer's machine credential
  WEBSOCKET /connect     — a connected computer's Runner keeps this open; see buddy_runner/protocol.py
  *         /machines/{machine}/workspace/v1/... — a chat's Project tab, relayed to the user's own computer

Relayed project requests carry an ``X-Buddy-Relay-Client`` header naming the
page, so each page keeps its own workspaces and terminals. The computer still
checks its own folder, write and command approvals.

The credential lets this server run commands on that computer, so the app
asks for it itself and never shows it to the web page.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from buddy_runner import protocol
from open_webui.env import UVICORN_WORKERS
from open_webui.models.users import Users
from open_webui.routers.subscriptions import get_machine_session_user
from open_webui.utils.subscriptions import service
from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.tunnel_hub import HUB, RunnerConnection

log = logging.getLogger(__name__)

router = APIRouter()

# Cleanup retries started when a computer reconnects; kept until they finish.
_background_tasks: set[asyncio.Task] = set()
RELAY_BODY_BYTES = 2 * 1024 * 1024
# Long enough for the computer's own 60-second command limit.
RELAY_TIMEOUT_SECONDS = 70


class EnrollForm(BaseModel):
    token: str
    name: Optional[str] = None
    host: Optional[dict] = None


class EnrollThrottle:
    """Limits enrollment attempts per address in a sliding window, in bounded memory.

    Tokens are 256-bit and single-use, so this bounds each caller's work rather
    than guessing. The address can come from X-Forwarded-For, which a caller may
    forge: rotating it only gives that caller more attempts and never blocks
    anyone else. At most max_addresses are kept; the least recently seen goes first.
    """

    def __init__(self, per_address: int, max_addresses: int, window_seconds: float, clock=time.monotonic):
        self.per_address = per_address
        self.max_addresses = max_addresses
        self.window_seconds = window_seconds
        self.clock = clock
        # Most recently seen last.
        self.by_address: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, address: str) -> bool:
        now = self.clock()
        # A forwarded address is caller-chosen text; the longest real IPv6 address is 45 characters.
        key = address[:64]
        recent = [moment for moment in self.by_address.pop(key, []) if now - moment < self.window_seconds]
        allowed = len(recent) < self.per_address
        if allowed:
            recent.append(now)
        if recent:
            self.by_address[key] = recent
        while len(self.by_address) > self.max_addresses:
            self.by_address.popitem(last=False)
        return allowed


ENROLL_THROTTLE = EnrollThrottle(per_address=20, max_addresses=1000, window_seconds=600)


@router.post('/enrollments')
async def create_enrollment(user=Depends(get_machine_session_user)):
    try:
        return await service.create_enrollment(user)
    except SubscriptionError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.post('/enroll')
async def enroll(form_data: EnrollForm, request: Request):
    address = request.client.host if request.client else 'unknown'
    if not ENROLL_THROTTLE.allow(address):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Too many attempts. Wait a few minutes.')
    try:
        return await service.redeem_enrollment(form_data.token, form_data.name, form_data.host)
    except SubscriptionError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


class _StarletteSocket:
    """The text-in, text-out view of a WebSocket that RunnerConnection uses."""

    def __init__(self, websocket: WebSocket):
        self._websocket = websocket

    async def receive_text(self) -> str:
        return await self._websocket.receive_text()

    async def send_text(self, text: str) -> None:
        await self._websocket.send_text(text)

    async def close(self, code: int, reason: str = '') -> None:
        await self._websocket.close(code=code, reason=reason)


async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError:
        pass


async def _read_hello(websocket: WebSocket) -> dict | None:
    try:
        text = await asyncio.wait_for(websocket.receive_text(), protocol.HELLO_TIMEOUT_SECONDS)
    except Exception:
        return None
    if len(text) > protocol.MAX_HELLO_BYTES:
        return None
    try:
        hello = json.loads(text)
        if protocol.frame_type(hello) != 'hello':
            return None
    except ValueError:
        return None
    return hello


def _valid_info(info) -> bool:
    return (
        isinstance(info, dict)
        and isinstance(info.get('chat_dir'), str)
        and isinstance(info.get('default_workspace'), str)
    )


def _start_cleanup_retry(machine_id: str) -> None:
    task = asyncio.create_task(service.retry_machine_cleanups(machine_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.websocket('/connect')
async def runner_connect(websocket: WebSocket):
    await websocket.accept()
    if 'origin' in websocket.headers:
        # Browsers always send an Origin; a Runner never does.
        await _refuse(websocket, protocol.CLOSE_NOT_ALLOWED, 'Browsers cannot connect as a Runner.')
        return
    if UVICORN_WORKERS != 1:
        await _refuse(websocket, protocol.CLOSE_TRY_AGAIN_LATER, service.SINGLE_WORKER_MESSAGE)
        return
    hello = await _read_hello(websocket)
    if hello is None:
        await _refuse(websocket, protocol.CLOSE_BAD_HELLO, 'Expected a hello message.')
        return
    if hello.get('protocol') != protocol.PROTOCOL_VERSION:
        await _refuse(websocket, protocol.CLOSE_UNSUPPORTED_VERSION, 'Update the Buddy app on this computer.')
        return
    runner_info = hello.get('runner') if isinstance(hello.get('runner'), dict) else {}
    instance_id = runner_info.get('instance_id')
    info = hello.get('info')
    if not isinstance(instance_id, str) or not 0 < len(instance_id) <= 128 or not _valid_info(info):
        await _refuse(websocket, protocol.CLOSE_BAD_HELLO, 'The hello message is incomplete.')
        return

    machine = await service.authenticate_runner(hello.get('machine_id'), hello.get('credential'))
    if machine is None:
        await _refuse(websocket, protocol.CLOSE_UNKNOWN_MACHINE, service.REMOVED_MACHINE_MESSAGE)
        return
    owner = await Users.get_user_by_id(machine['user_id'])
    if owner is None or not await service.may_use_machines(owner):
        await _refuse(websocket, protocol.CLOSE_NOT_ALLOWED, service.NOT_ALLOWED_MESSAGE)
        return

    await service.record_runner_connected(machine['id'], info)
    connection = RunnerConnection(_StarletteSocket(websocket), machine['id'], owner.id, instance_id, info)
    try:
        await connection.send({
            't': 'welcome',
            'protocol': protocol.PROTOCOL_VERSION,
            'machine': {'id': machine['id'], 'name': machine['name']},
        })
    except ConnectionResetError:
        return
    previous = HUB.attach(connection)
    if previous is not None:
        await previous.close(protocol.CLOSE_REPLACED, 'This computer connected again from another session.')
    log.info('Runner %s connected for user %s', machine['id'], owner.id)
    _start_cleanup_retry(machine['id'])
    try:
        await connection.serve()
    finally:
        HUB.detach(connection)
        await service.record_runner_disconnected(machine['id'])
        log.info('Runner %s disconnected', machine['id'])


async def _relay_body(request: Request):
    if request.method not in ('POST', 'PUT'):
        return None
    raw = await request.body()
    if len(raw) > RELAY_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail='Request bodies are limited to 2 MiB.')
    try:
        return json.loads(raw or b'null')
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Provide a valid JSON object.')


@router.api_route('/machines/{machine_id}/workspace/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
async def relay_workspace(machine_id: str, path: str, request: Request, user=Depends(get_machine_session_user)):
    body = await _relay_body(request)
    params = {
        'client_id': request.headers.get('x-buddy-relay-client', ''),
        'method': request.method,
        'path': '/' + path,
        'query': dict(request.query_params),
        'body': body,
    }
    try:
        connection = await service.relay_connection(user.id, machine_id)
        reply = await connection.call('workspace.request', params, timeout=RELAY_TIMEOUT_SECONDS)
    except SubscriptionError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    reply_status = reply.get('status') if isinstance(reply, dict) else None
    reply_body = reply.get('body') if isinstance(reply, dict) else None
    if isinstance(reply_status, bool) or not isinstance(reply_status, int) or not 100 <= reply_status <= 599:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='The computer sent an invalid reply.')
    return JSONResponse(status_code=reply_status, content=reply_body, headers={'Cache-Control': 'no-store'})
