"""Each user's Claude and ChatGPT subscriptions and the machines that run them.

Every route acts on the signed-in user's own providers and machines. Changes
need a browser session: API keys can read but not change machines, sign-ins,
or keys.

Routes:
  GET    /                         — the user's providers plus their machines
  GET    /machines                 — the user's machines (runner keys are never returned)
  POST   /machines                 — add or update a listen-mode Buddy Runner (administrators)
  POST   /machines/verify          — check a runner address and key (administrators)
  PATCH  /machines/{machine}       — rename one of the user's machines
  DELETE /machines/{machine}       — remove one of the user's machines
  GET    /admin/machines           — every user's machines, for administrators
  DELETE /admin/machines/{machine} — remove anyone's machine
  GET    /admin/policy             — who may use machines, and which sign-ins are allowed
  POST   /admin/policy             — save that policy
  POST   /machines/{machine}/folders/check — check that a folder exists on one of the user's computers
  GET    /folders/{folder}/project — the user's project folder for a chat folder, and their computers
  PUT    /folders/{folder}/project — choose it: let the AI choose, an exact folder per computer, or no files
  DELETE /folders/{folder}/project — forget it, so the chat folder follows its parent again
  GET    /{provider}               — one provider (?refresh=true checks again)
  POST   /{provider}/config        — save enable, access, workspace, CLI path, machine
  POST   /{provider}/login         — start signing in (browser or device code)
  GET    /{provider}/login         — sign-in progress
  POST   /{provider}/login/code    — paste the code shown after approving (Claude)
  DELETE /{provider}/login         — cancel signing in
  POST   /{provider}/logout        — sign the CLI out, or forget the stored token or key it uses
  PUT    /{provider}/credentials/api_key    — keep the user's own API key and use it everywhere
  PUT    /claude/credentials/plan_token     — keep a one-year Claude token and use it everywhere
  DELETE /{provider}/credentials/{kind}     — forget a stored token or key
  POST   /{provider}/credentials/machine    — use the computer's own sign-in again
  POST   /{provider}/credentials/{kind}/use — use a stored token or key again

Stored tokens and keys never come back: responses show a fingerprint and expiry.
"""

import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.utils.auth import get_admin_user, get_verified_user
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
    # Leave empty when editing to keep the saved key. A changed address needs the key.
    key: Optional[str] = None
    # Omit to keep the saved address; send null to clear it.
    browser_url: Optional[str] = None


class MachineVerifyForm(BaseModel):
    url: str
    key: str
    # Optional; checked the same way Save checks it, so Verify cannot pass an address Save rejects.
    browser_url: Optional[str] = None


class MachineRenameForm(BaseModel):
    name: str


class ApiKeyForm(BaseModel):
    key: str


class PlanTokenForm(BaseModel):
    token: str


class PolicyForm(BaseModel):
    user_machines: Optional[Literal['all', 'groups', 'admins']] = None
    machine_group_ids: Optional[list[str]] = None
    plan_sign_in: Optional[bool] = None
    api_keys: Optional[bool] = None
    server_machine: Optional[bool] = None
    desktop_download_url: Optional[str] = None


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


def _require_session(request: Request) -> None:
    if getattr(request.state, 'auth_type', None) == 'api_key':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Sign in to Buddy to change machines, sign-ins, or keys; API keys cannot.',
        )


async def get_machine_user(user=Depends(get_verified_user)):
    """A user the policy allows to use machines."""
    if not await service.may_use_machines(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=service.NOT_ALLOWED_MESSAGE)
    return user


async def get_machine_session_user(request: Request, user=Depends(get_machine_user)):
    """An allowed user in a signed-in session, for changes."""
    _require_session(request)
    return user


async def get_admin_session_user(request: Request, user=Depends(get_admin_user)):
    _require_session(request)
    return user


@router.get('/')
async def get_subscriptions(user=Depends(get_verified_user)):
    policy = await service.get_policy()
    if not await service.may_use_machines(user):
        return {'allowed': False, 'providers': [], 'machines': [], 'policy': _public_policy(policy)}
    claude, codex, machines = await asyncio.gather(
        service.describe_provider(user.id, 'claude'),
        service.describe_provider(user.id, 'codex'),
        service.describe_machines(user.id),
    )
    return {'allowed': True, 'providers': [claude, codex], 'machines': machines, 'policy': _public_policy(policy)}


def _public_policy(policy) -> dict:
    """What every user may know about the policy: which sign-ins are allowed, and where the app is."""
    return {
        'plan_sign_in': policy.plan_sign_in,
        'api_keys': policy.api_keys,
        'desktop_download_url': policy.desktop_download_url,
    }


@router.get('/machines')
async def get_machines(user=Depends(get_machine_user)):
    return {'machines': await service.describe_machines(user.id)}


@router.post('/machines')
async def save_machine(form_data: MachineForm, user=Depends(get_admin_session_user)):
    try:
        machine = await service.save_machine(
            user.id, form_data.id, form_data.name, form_data.url, form_data.key, form_data.browser_url,
            browser_url_supplied='browser_url' in form_data.model_fields_set
        )
    except SubscriptionError as error:
        raise _bad_request(error)
    return {'machine': machine, 'machines': await service.describe_machines(user.id)}


@router.post('/machines/verify')
async def verify_machine(form_data: MachineVerifyForm, user=Depends(get_admin_session_user)):
    try:
        return await service.verify_machine(form_data.url, form_data.key, form_data.browser_url)
    except SubscriptionError as error:
        raise _bad_request(error)


@router.patch('/machines/{machine_id}')
async def rename_machine(machine_id: str, form_data: MachineRenameForm, user=Depends(get_machine_session_user)):
    try:
        await service.rename_machine(user.id, machine_id, form_data.name)
    except SubscriptionError as error:
        raise _bad_request(error)
    return {'machines': await service.describe_machines(user.id)}


@router.delete('/machines/{machine_id}')
async def delete_machine(machine_id: str, user=Depends(get_machine_session_user)):
    try:
        await service.delete_machine(user.id, machine_id)
    except SubscriptionError as error:
        raise _bad_request(error)
    return {'machines': await service.describe_machines(user.id)}


@router.get('/admin/machines')
async def get_all_machines(user=Depends(get_admin_user)):
    return {'machines': await service.describe_all_machines()}


@router.delete('/admin/machines/{machine_id}')
async def admin_delete_machine(machine_id: str, user=Depends(get_admin_session_user)):
    try:
        await service.admin_delete_machine(machine_id)
    except SubscriptionError as error:
        raise _bad_request(error)
    return {'machines': await service.describe_all_machines()}


@router.get('/admin/policy')
async def get_policy(user=Depends(get_admin_user)):
    return await _policy_response()


@router.post('/admin/policy')
async def save_policy(form_data: PolicyForm, user=Depends(get_admin_session_user)):
    try:
        await service.save_policy(form_data.model_dump())
    except SubscriptionError as error:
        raise _bad_request(error)
    return await _policy_response()


async def _policy_response() -> dict:
    policy = await service.get_policy()
    return {
        'user_machines': policy.user_machines,
        'machine_group_ids': policy.machine_group_ids,
        'plan_sign_in': policy.plan_sign_in,
        'api_keys': policy.api_keys,
        'server_machine': policy.server_machine,
        'desktop_download_url': policy.desktop_download_url,
        'owner_id': await service.server_owner_id(),
    }


class FolderCheckForm(BaseModel):
    model_config = {'extra': 'forbid'}
    path: str


class FolderProjectForm(BaseModel):
    model_config = {'extra': 'forbid'}
    mode: Literal['auto', 'folder', 'chat']
    # Machine ID to folder path; only for mode 'folder'.
    paths: dict[str, str] = {}


@router.post('/machines/{machine_id}/folders/check')
async def check_folder_on_machine(machine_id: str, form_data: FolderCheckForm, user=Depends(get_machine_session_user)):
    try:
        return {'path': await service.check_machine_folder(user, machine_id, form_data.path)}
    except SubscriptionError as error:
        raise _bad_request(error)


@router.get('/machines/{machine_id}/agents')
async def get_machine_agents(machine_id: str, path: str, user=Depends(get_machine_user)):
    try:
        return await service.server_agent_files(user, machine_id, path)
    except SubscriptionError as error:
        raise _bad_request(error)


@router.get('/folders/{folder_id}/project')
async def get_folder_project(folder_id: str, user=Depends(get_machine_user)):
    return await service.describe_folder_project(user, folder_id)


@router.put('/folders/{folder_id}/project')
async def save_folder_project(folder_id: str, form_data: FolderProjectForm, user=Depends(get_machine_session_user)):
    try:
        return await service.save_folder_project(user, folder_id, form_data.mode, form_data.paths)
    except SubscriptionError as error:
        raise _bad_request(error)


@router.delete('/folders/{folder_id}/project')
async def clear_folder_project(folder_id: str, user=Depends(get_machine_session_user)):
    try:
        return await service.clear_folder_project(user, folder_id)
    except SubscriptionError as error:
        raise _bad_request(error)


@router.get('/{provider_id}')
async def get_subscription(
    provider_id: str, refresh: bool = False, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_machine_user)
):
    return await service.describe_provider(
        user.id, provider_id, fresh=refresh, expected_machine_id=expected_machine_id,
        expected_machine_revision=expected_machine_revision
    )


@router.post('/{provider_id}/config')
async def update_subscription_config(
    provider_id: str,
    form_data: SubscriptionConfigForm,
    user=Depends(get_machine_session_user),
):
    try:
        await service.save_settings(user.id, provider_id, form_data.model_dump())
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)
    return await service.describe_provider(user.id, provider_id, fresh=True)


@router.post('/{provider_id}/login')
async def start_subscription_login(provider_id: str, form_data: LoginForm, user=Depends(get_machine_session_user)):
    try:
        return await service.start_login(
            user.id, provider_id, form_data.method, form_data.expected_machine_id, form_data.expected_machine_revision
        )
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.get('/{provider_id}/login')
async def get_subscription_login(
    provider_id: str, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_machine_user)
):
    return await service.login_state(user.id, provider_id, expected_machine_id, expected_machine_revision)


@router.post('/{provider_id}/login/code')
async def submit_subscription_login_code(
    provider_id: str,
    form_data: LoginCodeForm,
    user=Depends(get_machine_session_user),
):
    try:
        return await service.submit_login_code(
            user.id, provider_id, form_data.code, form_data.expected_machine_id, form_data.expected_machine_revision
        )
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.delete('/{provider_id}/login')
async def cancel_subscription_login(
    provider_id: str, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_machine_session_user)
):
    try:
        return await service.cancel_login(user.id, provider_id, expected_machine_id, expected_machine_revision)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)


@router.post('/{provider_id}/logout')
async def logout_subscription(
    provider_id: str, expected_machine_id: Optional[str] = None,
    expected_machine_revision: Optional[str] = None, user=Depends(get_machine_session_user)
):
    try:
        await service.logout(user.id, provider_id, expected_machine_id, expected_machine_revision)
    except (SubscriptionError, OSError) as error:
        raise _bad_request(error)
    return await service.describe_provider(
        user.id, provider_id, fresh=True, expected_machine_id=expected_machine_id,
        expected_machine_revision=expected_machine_revision
    )


@router.put('/{provider_id}/credentials/api_key')
async def save_api_key(provider_id: str, form_data: ApiKeyForm, user=Depends(get_machine_session_user)):
    try:
        await service.save_api_key(user.id, provider_id, form_data.key)
    except SubscriptionError as error:
        raise _bad_request(error)
    return await service.describe_provider(user.id, provider_id, fresh=True)


@router.put('/claude/credentials/plan_token')
async def save_plan_token(form_data: PlanTokenForm, user=Depends(get_machine_session_user)):
    try:
        await service.save_plan_token(user.id, form_data.token)
    except SubscriptionError as error:
        raise _bad_request(error)
    return await service.describe_provider(user.id, 'claude', fresh=True)


@router.delete('/{provider_id}/credentials/{kind}')
async def delete_credential(provider_id: str, kind: str, user=Depends(get_machine_session_user)):
    try:
        await service.delete_credential(user.id, provider_id, kind)
    except SubscriptionError as error:
        raise _bad_request(error)
    return await service.describe_provider(user.id, provider_id, fresh=True)


@router.post('/{provider_id}/credentials/machine')
async def use_machine_sign_in(provider_id: str, user=Depends(get_machine_session_user)):
    try:
        await service.use_machine_sign_in(user.id, provider_id)
    except SubscriptionError as error:
        raise _bad_request(error)
    return await service.describe_provider(user.id, provider_id, fresh=True)


@router.post('/{provider_id}/credentials/{kind}/use')
async def use_stored_credential(provider_id: str, kind: str, user=Depends(get_machine_session_user)):
    try:
        await service.use_stored_credential(user.id, provider_id, kind)
    except SubscriptionError as error:
        raise _bad_request(error)
    return await service.describe_provider(user.id, provider_id, fresh=True)
