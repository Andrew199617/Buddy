"""Where the Buddy app's Runner connects, and how a computer is connected in the first place.

Routes:
  POST      /enrollments — a signed-in user asks to connect this PC; returns a 60-second, single-use token
  POST      /enroll      — the Buddy app trades that token for this computer's machine credential
  WEBSOCKET /connect     — a connected computer's Runner keeps this open; see buddy_runner/protocol.py

The credential lets this server run commands on that computer, so the app
asks for it itself and never shows it to the web page.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
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

# Failed or expired enrollments allowed per address in the window.
ENROLL_ATTEMPTS_PER_WINDOW = 20
ENROLL_WINDOW_SECONDS = 600
_enroll_attempts: dict[str, list[float]] = {}
# Cleanup retries started when a computer reconnects; kept until they finish.
_background_tasks: set[asyncio.Task] = set()


class EnrollForm(BaseModel):
    token: str
    name: Optional[str] = None
    host: Optional[dict] = None


def _allow_enroll_attempt(address: str) -> bool:
    now = time.monotonic()
    recent = [moment for moment in _enroll_attempts.get(address, []) if now - moment < ENROLL_WINDOW_SECONDS]
    if len(recent) >= ENROLL_ATTEMPTS_PER_WINDOW:
        _enroll_attempts[address] = recent
        return False
    recent.append(now)
    _enroll_attempts[address] = recent
    return True


@router.post('/enrollments')
async def create_enrollment(user=Depends(get_machine_session_user)):
    try:
        return await service.create_enrollment(user)
    except SubscriptionError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.post('/enroll')
async def enroll(form_data: EnrollForm, request: Request):
    address = request.client.host if request.client else 'unknown'
    if not _allow_enroll_attempt(address):
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
