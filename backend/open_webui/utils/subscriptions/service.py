"""Subscription models inside Open WebUI: settings, machines, model list, and chat.

Settings live in the config table under ``subscriptions.claude`` and
``subscriptions.codex``; Buddy Runner machines under ``subscriptions.machines``.
Each provider runs its CLI on one machine: this server, or a runner such as
the administrator's PC. Models appear only when a provider is turned on and
its CLI is signed in on that machine. Only administrators can use them: they
run on the administrator's personal plan and, with tool access, on that machine.
"""

import re
from dataclasses import asdict, fields
from pathlib import Path

from fastapi import HTTPException, status

from open_webui.env import DATA_DIR
from open_webui.models.config import Config
from open_webui.utils.subscriptions.claude_code import ClaudeCodeProvider
from open_webui.utils.subscriptions.codex import CodexProvider
from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_LEVELS,
    ProviderSettings,
)
from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.machines import LOCAL_MACHINE_ID, LocalMachine, RemoteMachine

STATE_DIR = Path(DATA_DIR) / 'subscriptions'
MACHINES_CONFIG_KEY = 'subscriptions.machines'

PROVIDERS = {
    'claude': ClaudeCodeProvider(STATE_DIR),
    'codex': CodexProvider(),
}

LOCAL_MACHINE = LocalMachine(STATE_DIR)
# RemoteMachine objects keep an HTTP session, so they are reused until their
# address or key changes.
_remote_machines: dict[str, RemoteMachine] = {}


def get_provider(provider_id: str):
    provider = PROVIDERS.get(provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown subscription provider.')
    return provider


# Machines


async def _machine_configs() -> list[dict]:
    stored = await Config.get(MACHINES_CONFIG_KEY, []) or []
    return [machine for machine in stored if isinstance(machine, dict) and machine.get('id')]


async def get_machine(machine_id: str):
    if not machine_id or machine_id == LOCAL_MACHINE_ID:
        return LOCAL_MACHINE

    config = next((machine for machine in await _machine_configs() if machine['id'] == machine_id), None)
    if not config:
        raise SubscriptionError('The selected machine no longer exists. Choose another in the subscription settings.')

    cached = _remote_machines.get(machine_id)
    if cached and cached.matches(config['name'], config['url'], config['key']):
        return cached
    if cached:
        await cached.close()
    machine = RemoteMachine(machine_id, config['name'], config['url'], config['key'])
    _remote_machines[machine_id] = machine
    return machine


def _machine_id_from_name(name: str, taken: set[str]) -> str:
    base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'machine'
    machine_id = base
    suffix = 2
    while machine_id in taken or machine_id == LOCAL_MACHINE_ID:
        machine_id = f'{base}-{suffix}'
        suffix += 1
    return machine_id


async def verify_machine(url: str, key: str) -> dict:
    probe = RemoteMachine('verify', 'The runner', url, key)
    try:
        return await probe.info()
    finally:
        await probe.close()


async def save_machine(machine_id: str | None, name: str, url: str, key: str | None) -> dict:
    """Add or update a runner after checking that it answers with the key."""
    name = name.strip()
    url = url.strip().rstrip('/')
    if not name or not url:
        raise SubscriptionError('Enter a name and the runner address.')

    configs = await _machine_configs()
    existing = next((config for config in configs if config['id'] == machine_id), None) if machine_id else None
    key = (key or '').strip() or (existing or {}).get('key', '')
    if not key:
        raise SubscriptionError('Enter the runner key from ~/.buddy-runner/key on that computer.')

    await verify_machine(url, key)

    if existing:
        existing.update({'name': name, 'url': url, 'key': key})
        saved = existing
    else:
        saved = {'id': _machine_id_from_name(name, {config['id'] for config in configs}), 'name': name, 'url': url}
        saved['key'] = key
        configs.append(saved)
    await Config.upsert({MACHINES_CONFIG_KEY: configs})
    return {'id': saved['id'], 'name': saved['name'], 'url': saved['url']}


async def delete_machine(machine_id: str) -> None:
    configs = [config for config in await _machine_configs() if config['id'] != machine_id]
    await Config.upsert({MACHINES_CONFIG_KEY: configs})
    cached = _remote_machines.pop(machine_id, None)
    if cached:
        await cached.close()
    # Providers that used the machine fall back to this server.
    for provider_id in PROVIDERS:
        settings = await get_settings(provider_id)
        if settings.machine_id == machine_id:
            await save_settings(provider_id, {'machine_id': LOCAL_MACHINE_ID})


# Provider settings


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
        machine_id=str(stored.get('machine_id') or LOCAL_MACHINE_ID),
    )


async def save_settings(provider_id: str, updates: dict) -> ProviderSettings:
    provider = get_provider(provider_id)
    previous = await get_settings(provider_id)
    current = asdict(previous)
    for setting in fields(ProviderSettings):
        if updates.get(setting.name) is not None:
            current[setting.name] = updates[setting.name]

    settings = ProviderSettings(**current)
    settings.workspace = settings.workspace.strip()
    settings.cli_path = settings.cli_path.strip()
    if settings.access not in ACCESS_LEVELS:
        raise SubscriptionError('Choose chat only, read files, or full access.')

    machine = await get_machine(settings.machine_id)
    if settings.workspace and not await machine.path_exists(settings.workspace, 'dir'):
        raise SubscriptionError(f'The working folder does not exist on {machine.name}: {settings.workspace}')
    if settings.cli_path and not await machine.path_exists(settings.cli_path, 'file'):
        raise SubscriptionError(f'The CLI path does not exist on {machine.name}: {settings.cli_path}')

    await Config.upsert({_config_key(provider_id): asdict(settings)})
    if settings.machine_id != previous.machine_id:
        await provider.cancel_login()
    if not settings.enable and isinstance(provider, CodexProvider):
        provider.stop()
    return settings
