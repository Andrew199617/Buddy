"""Subscription models inside Open WebUI: settings, machines, model list, and chat.

Settings live in the config table under ``subscriptions.claude`` and
``subscriptions.codex``; Buddy Runner machines under ``subscriptions.machines``.
Each provider runs its CLI on one machine: this server, or a runner such as
the administrator's PC. Models appear only when a provider is turned on and
its CLI is signed in on that machine. Only administrators can use them: they
run on the administrator's personal plan and, with tool access, on that machine.
"""

import asyncio
import ipaddress
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from starlette.responses import StreamingResponse

from open_webui.env import DATA_DIR, UVICORN_WORKERS
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
from open_webui.utils.subscriptions.machines import (
    LOCAL_MACHINE_ID, LocalMachine, RemoteMachine, StartupCleanupUnconfirmedError,
)
from open_webui.utils.subscriptions.streaming import collect_events, failure_events, stream_events

log = logging.getLogger(__name__)

STATE_DIR = Path(DATA_DIR) / 'subscriptions'
STATUS_TTL_SECONDS = 60
MODELS_TTL_SECONDS = 600
FIRST_LOAD_WAIT_SECONDS = 20
MACHINES_CONFIG_KEY = 'subscriptions.machines'
REVERIFY_MACHINE_MESSAGE = 'Verify the selected machine again in subscription settings before using its provider.'
SINGLE_WORKER_MESSAGE = 'Subscription provider execution and host changes require one Buddy backend worker.'
STARTUP_CLEANUP_MESSAGE = (
    'An earlier provider startup could not confirm that it stopped. '
    'Ask the host operator to stop or restart that host, then restart the Buddy backend before using this provider.'
)

PROVIDERS = {
    'claude': ClaudeCodeProvider(STATE_DIR),
    'codex': CodexProvider(),
}
PROVIDER_LABELS = {'claude': 'Claude', 'codex': 'ChatGPT'}
MODEL_ID_PREFIXES = {'claude': 'claude-code', 'codex': 'codex'}
MODEL_TAGS = {'claude': 'Claude plan', 'codex': 'ChatGPT plan'}

LOCAL_MACHINE = LocalMachine(STATE_DIR)
# RemoteMachine objects keep an HTTP session, so they are reused until their
# address or key changes.
_remote_machines: dict[str, RemoteMachine] = {}
_provider_locks: dict[str, asyncio.Lock] = {}
_machine_lock = asyncio.Lock()
_provider_generations: dict[str, int] = {}
_active_provider_tasks: dict[str, set[asyncio.Task]] = {}
_tracked_machines: dict[str, 'TrackingMachine'] = {}


def _provider_lock(provider_id: str) -> asyncio.Lock:
    get_provider(provider_id)
    return _provider_locks.setdefault(provider_id, asyncio.Lock())


def _require_single_worker() -> None:
    if UVICORN_WORKERS != 1:
        raise SubscriptionError(SINGLE_WORKER_MESSAGE)


class _ProviderLocks:
    """Machine edits acquire provider locks in one consistent order."""

    async def __aenter__(self):
        self.acquired = []
        try:
            for provider_id in sorted(PROVIDERS):
                lock = _provider_lock(provider_id)
                await lock.acquire()
                self.acquired.append(lock)
        except BaseException:
            for lock in reversed(self.acquired):
                lock.release()
            raise

    async def __aexit__(self, *args):
        for lock in reversed(self.acquired):
            lock.release()


async def _machine_revision(machine_id: str) -> str | None:
    if machine_id == LOCAL_MACHINE_ID:
        return 'local'
    config = next((machine for machine in await _machine_configs() if machine['id'] == machine_id), None)
    revision = (config or {}).get('revision')
    if isinstance(revision, str) and revision:
        return revision
    return None


async def _require_verified_machine(settings: ProviderSettings) -> None:
    if settings.machine_id != LOCAL_MACHINE_ID and await _machine_revision(settings.machine_id) is None:
        raise SubscriptionError(REVERIFY_MACHINE_MESSAGE)


async def _check_expected_machine(
    settings: ProviderSettings, expected_machine_id: str | None,
    expected_machine_revision: str | None = None, mutation=False
) -> None:
    if expected_machine_id is None and mutation and settings.machine_id != LOCAL_MACHINE_ID:
        raise HTTPException(status_code=409, detail='Confirm the selected machine by refreshing before trying again.')
    if expected_machine_id is not None and expected_machine_id != settings.machine_id:
        raise HTTPException(status_code=409, detail='The selected machine changed. Refresh before trying again.')
    revision = await _machine_revision(settings.machine_id)
    if mutation and settings.machine_id != LOCAL_MACHINE_ID and (not revision or not expected_machine_revision):
        raise HTTPException(status_code=409, detail='Verify the selected machine again, then refresh before trying again.')
    if expected_machine_revision is not None and expected_machine_revision != revision:
        raise HTTPException(status_code=409, detail='The selected machine changed. Refresh before trying again.')


async def _stop_tracked_process(process) -> None:
    close = getattr(process, 'kill_and_wait', None)
    if close is not None:
        await close()
        return
    process.kill()
    wait = getattr(process, 'wait', None)
    if wait is not None:
        await wait(5)


async def _finish_cancelled_cleanup(operation) -> None:
    """Repeated caller cancellation must not interrupt owned cleanup."""
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            continue
    operation.result()


class TrackingMachine:
    """One provider's stable machine handle, revoked on authority changes."""

    def __init__(self, machine):
        self.machine = machine
        self.valid = True
        self.processes = set()
        self.startups = set()
        self.startup_cleanup_error = None
        self.cleanup_error = None

    @property
    def id(self):
        return self.machine.id

    @property
    def name(self):
        return self.machine.name

    def _check(self):
        self._check_cleanup()
        if not self.valid:
            raise SubscriptionError('The provider machine changed; the old operation was stopped.')

    def _check_cleanup(self):
        if self.startup_cleanup_error is not None:
            raise SubscriptionError(self._startup_cleanup_message())
        if self.cleanup_error is not None or (not self.valid and (self.processes or self.startups)):
            raise SubscriptionError('An old provider operation could not confirm that it stopped. Retry cleanup before using this provider.')

    def _startup_cleanup_message(self):
        return f'{STARTUP_CLEANUP_MESSAGE} Earlier machine: {self.name} ({self.id}).'

    async def info(self):
        self._check()
        return await self.machine.info()

    async def chat_dir(self):
        self._check()
        return await self.machine.chat_dir()

    async def default_workspace(self):
        self._check()
        return await self.machine.default_workspace()

    async def find_tool(self, tool, configured_path=''):
        self._check()
        return await self.machine.find_tool(tool, configured_path)

    async def path_exists(self, path, kind):
        self._check()
        return await self.machine.path_exists(path, kind)

    async def start_process(self, args, cwd, extra_env=None, temp_files=None):
        self._check()
        startup = asyncio.create_task(self._start_process_owned(args, cwd, extra_env, temp_files))
        self.startups.add(startup)
        startup.add_done_callback(self.startups.discard)
        try:
            process = await asyncio.shield(startup)
        except asyncio.CancelledError:
            # A remote process may already exist before its identity arrives.
            # Retain the startup connection until we have a handle to stop.
            cleanup = asyncio.create_task(self._stop_startup(startup))
            await _finish_cancelled_cleanup(cleanup)
            raise
        self._check()
        return process

    async def _start_process_owned(self, args, cwd, extra_env, temp_files):
        if not self.valid:
            return None
        # Leader exit alone does not prove its descendants or pipes closed.
        for previous in list(self.processes):
            if bool(getattr(previous, 'cleanup_confirmed', False)):
                self.processes.discard(previous)
            elif (
                bool(getattr(previous, 'disposal_started', False))
                or getattr(previous, 'returncode', None) is not None
            ):
                await self._dispose_process(previous)
        if not self.valid:
            return None
        self._check_cleanup()
        try:
            process = await self.machine.start_process(args, cwd, extra_env=extra_env, temp_files=temp_files)
        except StartupCleanupUnconfirmedError as error:
            self.startup_cleanup_error = error
            raise
        self.processes.add(process)
        if not self.valid or self.startup_cleanup_error is not None or self.cleanup_error is not None:
            await self._dispose_process(process)
            return None
        return process

    async def _stop_startup(self, startup):
        process = await startup
        if process is not None:
            await self._dispose_process(process)

    async def _dispose_process(self, process):
        try:
            await _stop_tracked_process(process)
        except BaseException:
            self.cleanup_error = SubscriptionError('An old provider process could not confirm that it stopped.')
            raise
        self.processes.discard(process)

    async def run(self, args, cwd, timeout, extra_env=None):
        process = await self.start_process(args, cwd, extra_env=extra_env)

        async def capture():
            output = []
            output_bytes = 0
            while True:
                chunk = await process.read_text()
                if chunk is None:
                    break
                output_bytes += len(chunk.encode('utf-8'))
                if output_bytes > 1024 * 1024:
                    raise SubscriptionError('The provider command exceeded its output limit.')
                output.append(chunk)
            code = await process.wait()
            await process.finish_output(2)
            return code, ''.join(output), process.stderr_text()

        try:
            return await asyncio.wait_for(capture(), timeout)
        finally:
            await self._dispose_process(process)

    async def revoke(self):
        self.valid = False
        processes = list(self.processes)
        startups = set(self.startups)
        results = await asyncio.gather(*(self._dispose_process(process) for process in processes), return_exceptions=True)
        if startups:
            done, pending = await asyncio.wait(startups, timeout=10)
            if pending:
                raise SubscriptionError('An old provider startup could not confirm that it stopped.')
            for task in done:
                if task.cancelled():
                    results.append(asyncio.CancelledError())
                else:
                    results.append(task.exception())
        if self.startup_cleanup_error is not None:
            raise SubscriptionError(self._startup_cleanup_message())
        if any(isinstance(result, BaseException) for result in results):
            raise SubscriptionError('An old provider process could not confirm that it stopped.')
        self.cleanup_error = None


def _require_provider_cleanup(provider_id: str) -> None:
    tracked = _tracked_machines.get(provider_id)
    if tracked is not None:
        tracked._check_cleanup()


def _tracking_machine(provider_id: str, machine) -> TrackingMachine:
    _require_provider_cleanup(provider_id)
    tracked = _tracked_machines.get(provider_id)
    if tracked is None or tracked.machine is not machine or not tracked.valid:
        tracked = TrackingMachine(machine)
        _tracked_machines[provider_id] = tracked
    return tracked


async def _quiesce_provider(provider_id: str) -> None:
    """Called with its provider lock held, after committing disabled settings."""
    operation = asyncio.create_task(_quiesce_provider_safely(provider_id))
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await _finish_cancelled_cleanup(operation)
        raise


async def _quiesce_provider_safely(provider_id: str) -> None:
    try:
        await _quiesce_provider_work(provider_id)
    except Exception:
        # Failed cleanup must never leave a provider enabled for new work.
        settings = await get_settings(provider_id)
        settings.enable = False
        settings.access = ACCESS_CHAT
        settings.workspace = ''
        settings.cli_path = ''
        await Config.upsert({_config_key(provider_id): asdict(settings)})
        raise


async def _quiesce_provider_work(provider_id: str) -> None:
    _provider_generations[provider_id] = _provider_generations.get(provider_id, 0) + 1
    invalidate(provider_id)
    current = asyncio.current_task()
    tasks = {task for task in _active_provider_tasks.get(provider_id, set()) if task is not current and not task.done()}
    for task in tasks:
        task.cancel()
    tracked = _tracked_machines.get(provider_id)
    cleanup_error = None
    if tracked:
        try:
            await tracked.revoke()
        except SubscriptionError as error:
            cleanup_error = error
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=10)
        if pending:
            cleanup_error = SubscriptionError('An old provider operation could not confirm that it stopped.')
    provider = get_provider(provider_id)
    await provider.cancel_login()
    if isinstance(provider, CodexProvider):
        provider.stop()
    if cleanup_error:
        raise cleanup_error
    if tracked is not None and _tracked_machines.get(provider_id) is tracked:
        _tracked_machines.pop(provider_id)


async def _quiesce_providers(provider_ids: list[str]) -> None:
    results = await asyncio.gather(*(_quiesce_provider(provider_id) for provider_id in provider_ids), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


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
    if cached and cached.matches(cached.name, config['url'], config['key']):
        cached.name = config['name']
        return cached
    if cached:
        await cached.close()
    machine = RemoteMachine(machine_id, config['name'], config['url'], config['key'])
    _remote_machines[machine_id] = machine
    return machine


async def describe_machines() -> list[dict]:
    """Machines for the settings page; runner keys never leave the server."""
    machines = [{'id': LOCAL_MACHINE_ID, 'name': LOCAL_MACHINE.name, 'url': None}]
    for config in await _machine_configs():
        machines.append(_public_machine(config))
    return machines


def _public_machine(config: dict) -> dict:
    machine = {'id': config['id'], 'name': config['name'], 'url': config['url']}
    if isinstance(config.get('revision'), str):
        machine['revision'] = config['revision']
    if isinstance(config.get('browser_url'), str):
        machine['browser_url'] = config['browser_url']
    observed_host = config.get('host')
    if isinstance(observed_host, dict):
        machine['host'] = {
            field: observed_host[field] for field in ('id', 'name', 'platform', 'version')
            if isinstance(observed_host.get(field), str)
        }
    observed_capabilities = config.get('capabilities')
    if isinstance(observed_capabilities, dict):
        machine['capabilities'] = {
            field: observed_capabilities[field]
            for field in ('directories', 'files', 'workspaces', 'terminals', 'pty', 'providerExecution')
            if isinstance(observed_capabilities.get(field), bool)
        }
    return machine


def validate_browser_url(value: str | None) -> str | None:
    """A browser address identifies a host; it never creates directory authority."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if any(character in value for character in ('\\', '?', '#')):
        raise SubscriptionError('The browser address cannot include backslashes, a query, or a fragment.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SubscriptionError('Enter a browser HTTPS origin or a loopback HTTP origin.') from error
    loopback = parsed.hostname == 'localhost'
    try:
        loopback = loopback or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        pass
    if (
        not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path not in ('', '/') or parsed.query or parsed.fragment
        or parsed.scheme not in ('http', 'https')
        or (parsed.scheme == 'http' and not loopback)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise SubscriptionError('Enter a browser HTTPS origin or a loopback HTTP origin, without credentials or a path.')
    return value[:-1] if value.endswith('/') else value


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


async def save_machine(
    machine_id: str | None, name: str, url: str, key: str | None, browser_url: str | None = None,
    *, browser_url_supplied: bool | None = None
) -> dict:
    """Preserve an omitted browser address; explicit null uses its supplied flag."""
    _require_single_worker()
    async with _machine_lock, _ProviderLocks():
        return await _save_machine(
            machine_id, name, url, key, browser_url, browser_url_supplied=browser_url_supplied
        )


async def _save_machine(
    machine_id: str | None, name: str, url: str, key: str | None, browser_url: str | None = None,
    *, browser_url_supplied: bool | None = None
) -> dict:
    """Add or update a runner after checking that it answers with the key."""
    name = name.strip()
    url = url.strip().rstrip('/')
    if not name or not url:
        raise SubscriptionError('Enter a name and the runner address.')

    configs = await _machine_configs()
    existing = next((config for config in configs if config['id'] == machine_id), None) if machine_id else None
    previous_endpoint = (existing or {}).get('url')
    previous_key = (existing or {}).get('key')
    previous_instance = ((existing or {}).get('host') or {}).get('id')
    key = (key or '').strip() or (existing or {}).get('key', '')
    if not key:
        raise SubscriptionError('Enter the runner key from ~/.buddy-runner/key on that computer.')

    if browser_url_supplied is None:
        browser_url_supplied = browser_url is not None
    if browser_url_supplied:
        browser_url = validate_browser_url(browser_url)
    info = await verify_machine(url, key)

    if existing:
        existing.update({'name': name, 'url': url, 'key': key})
        saved = existing
    else:
        taken = {config['id'] for config in configs}
        for provider_id in PROVIDERS:
            taken.add((await get_settings(provider_id)).machine_id)
        taken.update(tracked.id for tracked in _tracked_machines.values())
        # Deleted selections and retained cleanup owners must never reconnect
        # automatically when a different host registers under the same name.
        saved = {'id': _machine_id_from_name(name, taken), 'name': name, 'url': url}
        saved['key'] = key
        configs.append(saved)
    if browser_url_supplied:
        saved['browser_url'] = browser_url
    # Only key-free, observed metadata reaches the UI. Paths in health are not grants.
    observed_host = info.get('host')
    if isinstance(observed_host, dict):
        saved['host'] = {field: observed_host[field] for field in ('id', 'name', 'platform', 'version') if isinstance(observed_host.get(field), str)}
    else:
        saved.pop('host', None)
    observed_capabilities = info.get('capabilities')
    if isinstance(observed_capabilities, dict):
        saved['capabilities'] = {field: observed_capabilities[field] for field in ('directories', 'files', 'workspaces', 'terminals', 'pty', 'providerExecution') if isinstance(observed_capabilities.get(field), bool)}
    else:
        saved.pop('capabilities', None)
    updates = {MACHINES_CONFIG_KEY: configs}
    host_changed = bool(existing) and (
        previous_endpoint != url or previous_key != key
        or (previous_instance and previous_instance != saved.get('host', {}).get('id'))
    )
    if host_changed or not isinstance(saved.get('revision'), str) or not saved['revision']:
        saved['revision'] = uuid.uuid4().hex
    affected = []
    if host_changed:
        for provider_id in PROVIDERS:
            settings = await get_settings(provider_id)
            if settings.machine_id == saved['id']:
                settings.enable = False
                settings.access = ACCESS_CHAT
                settings.workspace = ''
                settings.cli_path = ''
                updates[_config_key(provider_id)] = asdict(settings)
                affected.append(provider_id)
    await Config.upsert(updates)
    try:
        await _quiesce_providers(affected)
    finally:
        if host_changed:
            cached = _remote_machines.pop(saved['id'], None)
            if cached:
                await cached.close()
    for provider_id in PROVIDERS:
        invalidate(provider_id)
    return _public_machine(saved)


async def delete_machine(machine_id: str) -> None:
    _require_single_worker()
    async with _machine_lock, _ProviderLocks():
        await _delete_machine(machine_id)


async def _delete_machine(machine_id: str) -> None:
    if machine_id == LOCAL_MACHINE_ID:
        raise SubscriptionError('This server cannot be removed.')
    configs = [config for config in await _machine_configs() if config['id'] != machine_id]
    updates = {MACHINES_CONFIG_KEY: configs}
    affected = []
    for provider_id in PROVIDERS:
        settings = await get_settings(provider_id)
        if settings.machine_id == machine_id:
            settings.enable = False
            settings.access = ACCESS_CHAT
            settings.workspace = ''
            settings.cli_path = ''
            # Keep the missing ID so execution cannot silently move to another host.
            updates[_config_key(provider_id)] = asdict(settings)
            affected.append(provider_id)
    await Config.upsert(updates)
    try:
        await _quiesce_providers(affected)
    finally:
        cached = _remote_machines.pop(machine_id, None)
        if cached:
            await cached.close()


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


async def save_settings(
    provider_id: str, updates: dict, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None
) -> ProviderSettings:
    _require_single_worker()
    async with _provider_lock(provider_id):
        return await _save_settings(provider_id, updates, expected_machine_id, expected_machine_revision)


async def _save_settings(
    provider_id: str, updates: dict, expected_machine_id: str | None, expected_machine_revision: str | None
) -> ProviderSettings:
    previous = await get_settings(provider_id)
    if expected_machine_id is None:
        expected_machine_id = updates.get('expected_machine_id')
    if expected_machine_revision is None:
        expected_machine_revision = updates.get('expected_machine_revision')
    requested_machine_id = updates.get('machine_id')
    recover_missing_machine = False
    if (
        previous.machine_id != LOCAL_MACHINE_ID
        and expected_machine_id == previous.machine_id
        and isinstance(requested_machine_id, str)
        and requested_machine_id
        and requested_machine_id != previous.machine_id
    ):
        # A deleted registry entry has no revision left to confirm. Only an
        # explicit change away from its retained ID can recover the selection.
        recover_missing_machine = not any(
            machine['id'] == previous.machine_id for machine in await _machine_configs()
        )
    if recover_missing_machine:
        _require_provider_cleanup(provider_id)
    else:
        await _check_expected_machine(previous, expected_machine_id, expected_machine_revision, mutation=True)
    current = asdict(previous)
    for setting in fields(ProviderSettings):
        if updates.get(setting.name) is not None:
            current[setting.name] = updates[setting.name]

    settings = ProviderSettings(**current)
    if settings.machine_id != previous.machine_id:
        # Paths and access belong to the old machine. A separate save is required
        # to enable a provider or approve access on the newly selected machine.
        settings.enable = False
        settings.access = ACCESS_CHAT
        settings.workspace = ''
        settings.cli_path = ''
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
    if settings != previous:
        await _quiesce_provider(provider_id)
    invalidate(provider_id)
    return settings


async def working_directory(settings: ProviderSettings, access: str, machine) -> str:
    """Chat-only turns run in an empty folder; tool access uses the workspace."""
    if access == ACCESS_CHAT:
        return await machine.chat_dir()
    if settings.workspace:
        return settings.workspace
    return await machine.default_workspace()


async def start_login(
    provider_id: str, method: str, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None
) -> dict:
    _require_single_worker()
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        await _check_expected_machine(settings, expected_machine_id, expected_machine_revision, mutation=True)
        await _quiesce_provider(provider_id)
        machine = _tracking_machine(provider_id, await get_machine(settings.machine_id))
        return await get_provider(provider_id).start_login(settings, machine, method)


async def login_state(
    provider_id: str, expected_machine_id: str | None = None, expected_machine_revision: str | None = None
) -> dict:
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        await _check_expected_machine(settings, expected_machine_id, expected_machine_revision)
        if UVICORN_WORKERS != 1:
            return {'state': 'idle', 'message': SINGLE_WORKER_MESSAGE}
        try:
            await _require_verified_machine(settings)
            _require_provider_cleanup(provider_id)
        except SubscriptionError as error:
            return {'state': 'idle', 'message': str(error)}
        login = get_provider(provider_id).login_state()
        if login.get('state') == 'success':
            invalidate(provider_id)
        return login


async def submit_login_code(
    provider_id: str, code: str, expected_machine_id: str | None = None, expected_machine_revision: str | None = None
) -> dict:
    _require_single_worker()
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        await _check_expected_machine(settings, expected_machine_id, expected_machine_revision, mutation=True)
        _require_provider_cleanup(provider_id)
        login = await get_provider(provider_id).submit_login_code(code)
        if login.get('state') == 'success':
            invalidate(provider_id)
        return login


async def cancel_login(
    provider_id: str, expected_machine_id: str | None = None, expected_machine_revision: str | None = None
) -> dict:
    _require_single_worker()
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        await _check_expected_machine(settings, expected_machine_id, expected_machine_revision, mutation=True)
        await _quiesce_provider(provider_id)
        return {'state': 'idle'}


async def logout(
    provider_id: str, expected_machine_id: str | None = None, expected_machine_revision: str | None = None
) -> None:
    _require_single_worker()
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        await _check_expected_machine(settings, expected_machine_id, expected_machine_revision, mutation=True)
        await _quiesce_provider(provider_id)
        machine = _tracking_machine(provider_id, await get_machine(settings.machine_id))
        try:
            await get_provider(provider_id).logout(settings, machine)
        finally:
            invalidate(provider_id)


async def _tracked_turn(provider_id: str, provider, turn: TurnRequest, generation: int | None = None):
    if generation is None:
        generation = _provider_generations.get(provider_id, 0)
    task = asyncio.current_task()
    async with _provider_lock(provider_id):
        error = None
        if generation != _provider_generations.get(provider_id, 0):
            error = 'The provider machine changed; refresh before starting another turn.'
        else:
            try:
                _require_single_worker()
                await _require_verified_machine(turn.settings)
                _require_provider_cleanup(provider_id)
            except SubscriptionError as failure:
                error = str(failure)
            if error is None:
                _active_provider_tasks.setdefault(provider_id, set()).add(task)
    if error is not None:
        async for event in failure_events(error):
            yield event
        return
    try:
        async for event in provider.run_turn(turn):
            yield event
    finally:
        _active_provider_tasks.get(provider_id, set()).discard(task)


# Cached status and model lists


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
        refresh = self._refreshes.pop(provider_id, None)
        self._generations[provider_id] = self._generations.get(provider_id, 0) + 1
        if refresh is not None and not refresh.done():
            refresh.cancel()

    async def _refresh(self, provider_id: str) -> Any:
        generation = self._generations.get(provider_id, 0)
        value = await self._load(provider_id)
        if self._generations.get(provider_id, 0) != generation:
            return None
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
        generation = self._generations.get(provider_id, 0)
        entry = self._entries.get(provider_id)
        if entry and not fresh and entry.expires_at > time.monotonic():
            return entry.value

        task = self._start_refresh(provider_id)
        if entry and not fresh:
            return entry.value
        try:
            value = await asyncio.wait_for(asyncio.shield(task), FIRST_LOAD_WAIT_SECONDS)
            if generation != self._generations.get(provider_id, 0):
                return None
            return value
        except TimeoutError:
            log.warning('Subscription provider %s is slow to answer; continuing without it', provider_id)
            return None
        except asyncio.CancelledError:
            if task.cancelled() and not asyncio.current_task().cancelling():
                return None
            raise


def _log_refresh_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error:
        log.warning('Could not refresh subscription details: %s', error)


async def _load_status(provider_id: str) -> dict:
    task = asyncio.current_task()
    try:
        _require_single_worker()
        async with _provider_lock(provider_id):
            settings = await get_settings(provider_id)
            await _require_verified_machine(settings)
            _require_provider_cleanup(provider_id)
            machine = _tracking_machine(provider_id, await get_machine(settings.machine_id))
            _active_provider_tasks.setdefault(provider_id, set()).add(task)
        return await get_provider(provider_id).status(settings, machine)
    except (SubscriptionError, OSError) as error:
        return {'installed': True, 'signed_in': False, 'message': str(error)}
    finally:
        _active_provider_tasks.get(provider_id, set()).discard(task)


async def _load_models(provider_id: str) -> list[ProviderModel]:
    task = asyncio.current_task()
    try:
        _require_single_worker()
        async with _provider_lock(provider_id):
            settings = await get_settings(provider_id)
            await _require_verified_machine(settings)
            _require_provider_cleanup(provider_id)
            machine = _tracking_machine(provider_id, await get_machine(settings.machine_id))
            _active_provider_tasks.setdefault(provider_id, set()).add(task)
        return await get_provider(provider_id).list_models(settings, machine)
    except (SubscriptionError, OSError) as error:
        log.warning('Could not list %s models: %s', PROVIDER_LABELS[provider_id], error)
        return []
    finally:
        _active_provider_tasks.get(provider_id, set()).discard(task)


status_cache = CachedLoader(STATUS_TTL_SECONDS, _load_status)
models_cache = CachedLoader(MODELS_TTL_SECONDS, _load_models)


def uses_plan(provider_status: dict) -> bool:
    """Signed in with a Claude or ChatGPT plan, not an account that bills the API."""
    return bool(provider_status.get('signed_in')) and not provider_status.get('api_billing')


def invalidate(provider_id: str) -> None:
    status_cache.invalidate(provider_id)
    models_cache.invalidate(provider_id)


async def describe_provider(
    provider_id: str, fresh: bool = False, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None
) -> dict:
    """Everything the Connections page shows for one provider."""
    for _ in range(2):
        result = await _describe_provider_snapshot(provider_id, fresh, expected_machine_id, expected_machine_revision)
        if result is not None:
            return result
    raise HTTPException(status_code=409, detail='The selected machine changed. Refresh before trying again.')


async def _describe_provider_snapshot(
    provider_id: str, fresh: bool, expected_machine_id: str | None, expected_machine_revision: str | None
) -> dict | None:
    provider = get_provider(provider_id)
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        await _check_expected_machine(settings, expected_machine_id, expected_machine_revision)
        revision = await _machine_revision(settings.machine_id)
        generation = _provider_generations.get(provider_id, 0)
        machine = None
        verified = settings.machine_id == LOCAL_MACHINE_ID or revision is not None
        execution_ready = verified and UVICORN_WORKERS == 1
        readiness_message = REVERIFY_MACHINE_MESSAGE if UVICORN_WORKERS == 1 else SINGLE_WORKER_MESSAGE
        if execution_ready:
            try:
                _require_provider_cleanup(provider_id)
                machine = _tracking_machine(provider_id, await get_machine(settings.machine_id))
            except SubscriptionError as error:
                execution_ready = False
                readiness_message = str(error)
                log.info('Could not describe the machine for %s: %s', provider_id, error)
    if execution_ready:
        provider_status = await status_cache.get(provider_id, fresh=fresh)
    else:
        provider_status = {'installed': True, 'signed_in': False, 'message': readiness_message}
    if provider_status is None:
        # The CLI is still answering; the page shows "Checking" and asks again.
        provider_status = {'checking': True}
    models = []
    if settings.enable and uses_plan(provider_status):
        for model in await models_cache.get(provider_id, fresh=fresh) or []:
            models.append({'id': _model_id(provider_id, model), 'name': model.name})

    machine_name = 'Unavailable machine'
    default_workspace = ''
    try:
        if machine is not None:
            machine_name = machine.name
            default_workspace = await machine.default_workspace()
    except SubscriptionError as error:
        log.info('Could not describe the machine for %s: %s', provider_id, error)

    if generation != _provider_generations.get(provider_id, 0):
        return None
    public_machine = {'id': settings.machine_id, 'name': machine_name}
    if revision is not None:
        public_machine['revision'] = revision
    return {
        'id': provider_id,
        'name': PROVIDER_LABELS[provider_id],
        'settings': asdict(settings),
        'status': provider_status,
        'login': provider.login_state() if execution_ready else {'state': 'idle'},
        'models': models,
        'machine': public_machine,
        'default_workspace': default_workspace,
    }


# Models and chat


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
    async with _provider_lock(provider_id):
        settings = await get_settings(provider_id)
        try:
            _require_single_worker()
            await _require_verified_machine(settings)
            _require_provider_cleanup(provider_id)
        except SubscriptionError:
            return []
        generation = _provider_generations.get(provider_id, 0)
    if not settings.enable:
        return []
    provider_status = await status_cache.get(provider_id) or {}
    if not uses_plan(provider_status):
        return []
    models = await models_cache.get(provider_id) or []
    if generation != _provider_generations.get(provider_id, 0):
        return []
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
    is_task = bool(metadata.get('task'))

    try:
        async with _provider_lock(provider_id):
            _require_single_worker()
            settings = await get_settings(provider_id)
            await _require_verified_machine(settings)
            _require_provider_cleanup(provider_id)
            generation = _provider_generations.get(provider_id, 0)
        if not settings.enable:
            raise SubscriptionError(
                f'{PROVIDER_LABELS[provider_id]} subscription models are turned off in '
                'Admin Settings → Connections → Subscriptions.'
            )
        provider_status = await status_cache.get(provider_id) or {}
        if provider_status.get('api_billing'):
            # Refuse rather than bill the API under a "plan" model name.
            raise SubscriptionError(provider_status.get('message') or 'This account bills API usage, not a plan.')
        async with _provider_lock(provider_id):
            if generation != _provider_generations.get(provider_id, 0):
                raise SubscriptionError('The provider machine changed; refresh before starting another turn.')
            machine = _tracking_machine(provider_id, await get_machine(settings.machine_id))
            access = ACCESS_CHAT if is_task else settings.access
            turn = TurnRequest(
                model=subscription['model'],
                conversation=parse_messages(payload.get('messages')),
                settings=settings,
                cwd=await working_directory(settings, access, machine),
                effort=requested_effort(payload),
                is_task=is_task,
                machine=machine,
            )
    except SubscriptionError as error:
        events = failure_events(str(error))
    else:
        events = _tracked_turn(provider_id, provider, turn, generation)

    response_model_id = form_data.get('model') or model_id
    if payload.get('stream'):
        return StreamingResponse(stream_events(events, response_model_id), media_type='text/event-stream')
    return await collect_events(events, response_model_id)
