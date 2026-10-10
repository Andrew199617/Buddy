"""Admin endpoints for Claude and ChatGPT subscriptions and the machines that run them.

Routes:
  GET    /                         — both providers plus the machine list
  GET    /machines                 — machines (runner keys are never returned)
  POST   /machines                 — add or update a Buddy Runner machine
  POST   /machines/verify          — check a runner address and key
  DELETE /machines/{machine}       — remove a machine
  GET    /{provider}               — one provider (?refresh=true checks again)
  POST   /{provider}/config        — save enable, access, workspace, CLI path, machine
  POST   /{provider}/login         — start signing in (browser or device code)
  GET    /{provider}/login         — sign-in progress
  POST   /{provider}/login/code    — paste the code shown after approving (Claude)
  DELETE /{provider}/login         — cancel signing in
  POST   /{provider}/logout        — sign the CLI out
"""

import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_webui.utils.auth import get_admin_user
from open_webui.utils.subscriptions import service
from open_webui.utils.subscriptions.events import SubscriptionError

log = logging.getLogger(__name__)

router = APIRouter()


class SubscriptionConfigForm(BaseModel):
    enable: Optional[bool] = None
    access: Optional[Literal['chat', 'read', 'full']] = None
    workspace: Optional[str] = None
    cli_path: Optional[str] = None
    machine_id: Optional[str] = None
    expected_machine_id: Optional[str] = None
    expected_machine_revision: Optional[str] = None


class LoginForm(BaseModel):
    method: Literal['browser', 'device'] = 'browser'
    expected_machine_id: Optional[str] = None
    expected_machine_revision: Optional[str] = None


class LoginCodeForm(BaseModel):
    code: str
    expected_machine_id: Optional[str] = None
    expected_machine_revision: Optional[str] = None


class MachineForm(BaseModel):
    id: Optional[str] = None
    name: str
    url: str
    # Leave empty when editing to keep the saved key.
    key: Optional[str] = None
    # Omit to keep the saved address; send null to clear it.
    browser_url: Optional[str] = None


class MachineVerifyForm(BaseModel):
    url: str
    key: str


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get('/')
async def get_subscriptions(user=Depends(get_admin_user)):
    claude, codex, machines = await asyncio.gather(
        service.describe_provider('claude'),
        service.describe_provider('codex'),
        service.describe_machines(),
    )
    return {'providers': [claude, codex], 'machines': machines}


@router.get('/machines')
async def get_machines(user=Depends(get_admin_user)):
    return {'machines': await service.describe_machines()}


@router.post('/machines')
async def save_machine(form_data: MachineForm, user=Depends(get_admin_user)):
    try:
        machine = await service.save_machine(
            form_data.id, form_data.name, form_data.url, form_data.key, form_data.browser_url,
            browser_url_supplied='browser_url' in form_data.model_fields_set
        )
    except SubscriptionError as error:
        raise _bad_request(error)
    return {'machine': machine, 'machines': await service.describe_machines()}


@router.post('/machines/verify')
async def verify_machine(form_data: MachineVerifyForm, user=Depends(get_admin_user)):
    try:
        return await service.verify_machine(form_data.url, form_data.key)
    except SubscriptionError as error:
        raise _bad_request(error)


@router.delete('/machines/{machine_id}')
async def delete_machine(machine_id: str, user=Depends(get_admin_user)):
    try:
        await service.delete_machine(machine_id)
    except SubscriptionError as error:
        raise _bad_request(error)
    return {'machines': await service.describe_machines()}


@router.get('/{provider_id}')
async def get_subscription(
    provider_id: str, refresh: bool = False, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_admin_user)
):
    return await service.describe_provider(
        provider_id, fresh=refresh, expected_machine_id=expected_machine_id,
        expected_machine_revision=expected_machine_revision
    )


@router.post('/{provider_id}/config')
async def update_subscription_config(
    provider_id: str,
    form_data: SubscriptionConfigForm,
    user=Depends(get_admin_user),
):
    try:
        await service.save_settings(provider_id, form_data.model_dump())
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)
    return await service.describe_provider(provider_id, fresh=True)


@router.post('/{provider_id}/login')
async def start_subscription_login(provider_id: str, form_data: LoginForm, user=Depends(get_admin_user)):
    try:
        return await service.start_login(
            provider_id, form_data.method, form_data.expected_machine_id, form_data.expected_machine_revision
        )
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.get('/{provider_id}/login')
async def get_subscription_login(
    provider_id: str, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_admin_user)
):
    return await service.login_state(provider_id, expected_machine_id, expected_machine_revision)


@router.post('/{provider_id}/login/code')
async def submit_subscription_login_code(
    provider_id: str,
    form_data: LoginCodeForm,
    user=Depends(get_admin_user),
):
    try:
        return await service.submit_login_code(
            provider_id, form_data.code, form_data.expected_machine_id, form_data.expected_machine_revision
        )
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.delete('/{provider_id}/login')
async def cancel_subscription_login(
    provider_id: str, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_admin_user)
):
    try:
        return await service.cancel_login(provider_id, expected_machine_id, expected_machine_revision)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.post('/{provider_id}/logout')
async def logout_subscription(
    provider_id: str, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_admin_user)
):
    try:
        await service.logout(provider_id, expected_machine_id, expected_machine_revision)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)
    return await service.describe_provider(
        provider_id, fresh=True, expected_machine_id=expected_machine_id,
        expected_machine_revision=expected_machine_revision
    )
