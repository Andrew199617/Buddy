"""Subscription models inside Open WebUI: settings, model list, and chat.

Settings live in the config table under ``subscriptions.claude`` and
``subscriptions.codex``. Models appear only when a provider is turned on and
its CLI is signed in. Only administrators can use them: they run on the
administrator's personal plan and, with tool access, on this computer.
"""

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, status
from starlette.responses import StreamingResponse

from open_webui.env import DATA_DIR
from open_webui.models.config import Config
from open_webui.models.models import Models
from open_webui.utils.payload import apply_model_params_to_body_openai, apply_system_prompt_to_body
from open_webui.utils.subscriptions.claude_code import ClaudeCodeProvider
from open_webui.utils.subscriptions.codex import CodexProvider
from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_LEVELS,
    SUBSCRIPTION_OWNED_BY,
    ProviderModel,
    ProviderSettings,
    TurnRequest,
    requested_effort,
)
from open_webui.utils.subscriptions.conversation import parse_messages
from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.streaming import collect_events, failure_events, stream_events

log = logging.getLogger(__name__)

STATE_DIR = Path(DATA_DIR) / 'subscriptions'
CHAT_DIR = STATE_DIR / 'chat'
DEFAULT_WORKSPACE = STATE_DIR / 'workspace'
STATUS_TTL_SECONDS = 60
MODELS_TTL_SECONDS = 600
FIRST_LOAD_WAIT_SECONDS = 20

PROVIDERS = {
    'claude': ClaudeCodeProvider(STATE_DIR, CHAT_DIR),
    'codex': CodexProvider(CHAT_DIR),
}
PROVIDER_LABELS = {'claude': 'Claude', 'codex': 'ChatGPT'}
MODEL_ID_PREFIXES = {'claude': 'claude-code', 'codex': 'codex'}
MODEL_TAGS = {'claude': 'Claude plan', 'codex': 'ChatGPT plan'}
def get_provider(provider_id: str):
    provider = PROVIDERS.get(provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown subscription provider.')
    return provider


def _config_key(provider_id: str) -> str:
    return f'subscriptions.{provider_id}'


async def get_settings(provider_id: str) -> ProviderSettings:
    stored = await Config.get(_config_key(provider_id), {}) or {}
    access = stored.get('access')
    if access not in ACCESS_LEVELS:
        access = ACCESS_CHAT
    return ProviderSettings(
        enable=bool(stored.get('enable', False)),
        access=access,
        workspace=str(stored.get('workspace') or '').strip(),
        cli_path=str(stored.get('cli_path') or '').strip(),
    )


async def save_settings(provider_id: str, updates: dict) -> ProviderSettings:
    provider = get_provider(provider_id)
    current = asdict(await get_settings(provider_id))
    for setting in fields(ProviderSettings):
        if updates.get(setting.name) is not None:
            current[setting.name] = updates[setting.name]

    settings = ProviderSettings(**current)
    settings.workspace = settings.workspace.strip()
    settings.cli_path = settings.cli_path.strip()
    if settings.access not in ACCESS_LEVELS:
        raise SubscriptionError('Choose chat only, read files, or full access.')
    if settings.workspace and not Path(settings.workspace).is_dir():
        raise SubscriptionError(f'The working folder does not exist: {settings.workspace}')
    if settings.cli_path and not Path(settings.cli_path).is_file():
        raise SubscriptionError(f'The CLI path does not exist: {settings.cli_path}')

    await Config.upsert({_config_key(provider_id): asdict(settings)})
    if not settings.enable and isinstance(provider, CodexProvider):
        provider.stop()
    invalidate(provider_id)
    return settings


def working_directory(settings: ProviderSettings, access: str) -> str:
    """Chat-only turns run in an empty folder; tool access uses the workspace."""
    if access == ACCESS_CHAT:
        CHAT_DIR.mkdir(parents=True, exist_ok=True)
        return str(CHAT_DIR)
    if settings.workspace:
        return settings.workspace
    DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    return str(DEFAULT_WORKSPACE)


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class CachedLoader:
    """Caches one value per provider and serves the old value while refreshing.

    Model lists are requested on most page loads, and the CLIs take a few
    seconds to answer, so only the first load waits for them.
    """

    def __init__(self, ttl_seconds: float, load: Callable[[str], Awaitable[Any]]):
        self._ttl_seconds = ttl_seconds
        self._load = load
        self._entries: dict[str, _CacheEntry] = {}
        self._refreshes: dict[str, asyncio.Task] = {}
        # Bumped on invalidate so a refresh started earlier cannot store old results.
        self._generations: dict[str, int] = {}

    def invalidate(self, provider_id: str) -> None:
        self._entries.pop(provider_id, None)
        self._refreshes.pop(provider_id, None)
        self._generations[provider_id] = self._generations.get(provider_id, 0) + 1

    async def _refresh(self, provider_id: str) -> Any:
        generation = self._generations.get(provider_id, 0)
        value = await self._load(provider_id)
        if self._generations.get(provider_id, 0) == generation:
            self._entries[provider_id] = _CacheEntry(value, time.monotonic() + self._ttl_seconds)
        return value

    def _start_refresh(self, provider_id: str) -> asyncio.Task:
        task = self._refreshes.get(provider_id)
        if task is None or task.done():
            task = asyncio.create_task(self._refresh(provider_id))
            task.add_done_callback(_log_refresh_failure)
            self._refreshes[provider_id] = task
        return task

    async def get(self, provider_id: str, fresh: bool = False) -> Any:
        entry = self._entries.get(provider_id)
        if entry and not fresh and entry.expires_at > time.monotonic():
            return entry.value

        task = self._start_refresh(provider_id)
        if entry and not fresh:
            return entry.value
        try:
            return await asyncio.wait_for(asyncio.shield(task), FIRST_LOAD_WAIT_SECONDS)
        except TimeoutError:
            log.warning('Subscription provider %s is slow to answer; continuing without it', provider_id)
            return None


def _log_refresh_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error:
        log.warning('Could not refresh subscription details: %s', error)


async def _load_status(provider_id: str) -> dict:
    settings = await get_settings(provider_id)
    try:
        return await get_provider(provider_id).status(settings)
    except (SubscriptionError, OSError) as error:
        return {'installed': True, 'signed_in': False, 'message': str(error)}


async def _load_models(provider_id: str) -> list[ProviderModel]:
    settings = await get_settings(provider_id)
    try:
        return await get_provider(provider_id).list_models(settings)
    except (SubscriptionError, OSError) as error:
        log.warning('Could not list %s models: %s', PROVIDER_LABELS[provider_id], error)
        return []


status_cache = CachedLoader(STATUS_TTL_SECONDS, _load_status)
models_cache = CachedLoader(MODELS_TTL_SECONDS, _load_models)


def uses_plan(provider_status: dict) -> bool:
    """Signed in with a Claude or ChatGPT plan, not an account that bills the API."""
    return bool(provider_status.get('signed_in')) and not provider_status.get('api_billing')


def invalidate(provider_id: str) -> None:
    status_cache.invalidate(provider_id)
    models_cache.invalidate(provider_id)


async def describe_provider(provider_id: str, fresh: bool = False) -> dict:
    """Everything the Connections page shows for one provider."""
    provider = get_provider(provider_id)
    settings = await get_settings(provider_id)
    provider_status = await status_cache.get(provider_id, fresh=fresh)
    if provider_status is None:
        # The CLI is still answering; the page shows "Checking" and asks again.
        provider_status = {'checking': True}
    models = []
    if settings.enable and uses_plan(provider_status):
        for model in await models_cache.get(provider_id, fresh=fresh) or []:
            models.append({'id': _model_id(provider_id, model), 'name': model.name})
    return {
        'id': provider_id,
        'name': PROVIDER_LABELS[provider_id],
        'settings': asdict(settings),
        'status': provider_status,
        'login': provider.login_state(),
        'models': models,
        'default_workspace': str(DEFAULT_WORKSPACE),
    }


def _model_id(provider_id: str, model: ProviderModel) -> str:
    return f'{MODEL_ID_PREFIXES[provider_id]}.{model.key}'


def _model_entry(provider_id: str, model: ProviderModel) -> dict:
    return {
        'id': _model_id(provider_id, model),
        'name': model.name,
        'object': 'model',
        'created': 0,
        'owned_by': SUBSCRIPTION_OWNED_BY,
        'connection_type': 'external',
        'tags': [{'name': MODEL_TAGS[provider_id]}],
        'subscription': {
            'provider': provider_id,
            'model': model.value,
            'description': model.description,
            'efforts': model.efforts,
        },
    }


async def _provider_models(provider_id: str) -> list[dict]:
    settings = await get_settings(provider_id)
    if not settings.enable:
        return []
    provider_status = await status_cache.get(provider_id) or {}
    if not uses_plan(provider_status):
        return []
    models = await models_cache.get(provider_id) or []
    return [_model_entry(provider_id, model) for model in models]


async def get_subscription_models() -> list[dict]:
    claude_models, codex_models = await asyncio.gather(_provider_models('claude'), _provider_models('codex'))
    return claude_models + codex_models


async def _apply_preset(request, payload: dict, metadata: dict, user) -> tuple[str, dict]:
    """Apply a Workspace model preset's params and system prompt, like the OpenAI route."""
    model_id = payload.get('model')
    model_info = await Models.get_model_by_id(model_id)
    if not model_info:
        return model_id, payload

    if model_info.base_model_id:
        model_id = model_info.base_model_id
    params = model_info.params.model_dump()
    if params:
        system = params.pop('system', None)
        payload = apply_model_params_to_body_openai(params, payload)
        if not getattr(request.state, 'bypass_system_prompt', False):
            payload = await apply_system_prompt_to_body(system, payload, metadata, user)
    return model_id, payload


async def generate_subscription_chat_completion(request, form_data: dict, user, models):
    if getattr(user, 'role', None) != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription models use the administrator's personal plan and are only available to them.",
        )

    payload = {**form_data}
    metadata = payload.pop('metadata', None) or {}
    model_id, payload = await _apply_preset(request, payload, metadata, user)
    base_model = models.get(model_id) or {}
    subscription = base_model.get('subscription')
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Model not found')

    provider_id = subscription['provider']
    provider = get_provider(provider_id)
    settings = await get_settings(provider_id)
    is_task = bool(metadata.get('task'))

    try:
        if not settings.enable:
            raise SubscriptionError(
                f'{PROVIDER_LABELS[provider_id]} subscription models are turned off in '
                'Admin Settings → Connections → Subscriptions.'
            )
        provider_status = await status_cache.get(provider_id) or {}
        if provider_status.get('api_billing'):
            # Refuse rather than bill the API under a "plan" model name.
            raise SubscriptionError(provider_status.get('message') or 'This account bills API usage, not a plan.')
        access = ACCESS_CHAT if is_task else settings.access
        turn = TurnRequest(
            model=subscription['model'],
            conversation=parse_messages(payload.get('messages')),
            settings=settings,
            cwd=working_directory(settings, access),
            effort=requested_effort(payload),
            is_task=is_task,
        )
    except SubscriptionError as error:
        events = failure_events(str(error))
    else:
        events = provider.run_turn(turn)

    response_model_id = form_data.get('model') or model_id
    if payload.get('stream'):
        return StreamingResponse(stream_events(events, response_model_id), media_type='text/event-stream')
    return await collect_events(events, response_model_id)
