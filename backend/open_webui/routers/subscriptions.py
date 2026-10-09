"""Admin endpoints for Claude and ChatGPT subscription sign-in and settings.

Routes:
  GET    /                         — both providers' settings, status, and models
  GET    /{provider}               — one provider (?refresh=true checks again)
  POST   /{provider}/config        — save enable, access, workspace, CLI path
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


class LoginForm(BaseModel):
    method: Literal['browser', 'device'] = 'browser'


class LoginCodeForm(BaseModel):
    code: str


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get('/')
async def get_subscriptions(user=Depends(get_admin_user)):
    claude, codex = await asyncio.gather(
        service.describe_provider('claude'),
        service.describe_provider('codex'),
    )
    return {'providers': [claude, codex]}


@router.get('/{provider_id}')
async def get_subscription(provider_id: str, refresh: bool = False, user=Depends(get_admin_user)):
    return await service.describe_provider(provider_id, fresh=refresh)


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
    provider = service.get_provider(provider_id)
    settings = await service.get_settings(provider_id)
    try:
        return await provider.start_login(settings, form_data.method)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.get('/{provider_id}/login')
async def get_subscription_login(provider_id: str, user=Depends(get_admin_user)):
    login = service.get_provider(provider_id).login_state()
    if login.get('state') == 'success':
        service.invalidate(provider_id)
    return login


@router.post('/{provider_id}/login/code')
async def submit_subscription_login_code(
    provider_id: str,
    form_data: LoginCodeForm,
    user=Depends(get_admin_user),
):
    provider = service.get_provider(provider_id)
    try:
        login = await provider.submit_login_code(form_data.code)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)
    if login.get('state') == 'success':
        service.invalidate(provider_id)
    return login


@router.delete('/{provider_id}/login')
async def cancel_subscription_login(provider_id: str, user=Depends(get_admin_user)):
    await service.get_provider(provider_id).cancel_login()
    return {'state': 'idle'}


@router.post('/{provider_id}/logout')
async def logout_subscription(provider_id: str, user=Depends(get_admin_user)):
    provider = service.get_provider(provider_id)
    settings = await service.get_settings(provider_id)
    try:
        await provider.logout(settings)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)
    finally:
        service.invalidate(provider_id)
    return await service.describe_provider(provider_id, fresh=True)
