"""Subscription models inside Open WebUI: settings, machines, model list, and chat.

Every user runs Claude and ChatGPT on their own plan, on their own machines:
computers they connect to Buddy, and for the instance owner also this server.
Each provider runs its CLI on one of the user's machines. A user's models
appear only to that user, once the provider is turned on and its CLI is
signed in on that machine.

Machines are one list under ``subscriptions.machines``; each entry names its
owner. Provider settings are stored per user under
``subscriptions.users.<user id>.<provider>``. Subscriptions were admin-only
before machines were per user. The instance owner keeps that setup's keys
(``subscriptions.claude`` and ``subscriptions.codex``), and machine entries
without an owner are theirs, so upgrading needs no data migration.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from starlette.responses import StreamingResponse

from open_webui.env import DATA_DIR, UVICORN_WORKERS
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.models import Models
from open_webui.models.chat_run_targets import (
    SOURCE_BAR,
    SOURCE_DEFAULT,
    SOURCE_REFUSED,
    ChatRunTargetModel,
    ChatRunTargets,
)
from open_webui.models.chats import Chats
from open_webui.models.subscription_credentials import KIND_API_KEY, KIND_PLAN_TOKEN, SubscriptionCredentials
from open_webui.models.users import Users
from open_webui.utils.chat_id import CHANNEL_CHAT_ID_PREFIX, is_saved_chat_id, is_temporary_chat_id
from open_webui.utils.payload import apply_model_params_to_body_openai, apply_system_prompt_to_body
from open_webui.utils.subscriptions.claude_code import ClaudeCodeProvider
from open_webui.utils.subscriptions.codex import CodexProvider
from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_FULL,
    ACCESS_LEVELS,
    CREDENTIAL_API_KEY,
    CREDENTIAL_MACHINE,
    CREDENTIAL_MODES,
    CREDENTIAL_PLAN_TOKEN,
    SUBSCRIPTION_OWNED_BY,
    ProviderModel,
    ProviderSettings,
    TurnRequest,
    UltraOptions,
    requested_effort,
)
from open_webui.utils.subscriptions.conversation import parse_messages
from open_webui.utils.subscriptions import (
    computer_settings, folder_listing, project_agents, project_folders, run_targets, ultra,
)
from open_webui.utils.subscriptions.events import StatusUpdate, SubscriptionError
from open_webui.utils.subscriptions.machines import (
    LOCAL_MACHINE_ID, LocalMachine, RemoteMachine, StartupCleanupUnconfirmedError, TunnelMachine,
)
from open_webui.utils.subscriptions.tunnel_hub import HUB, RunnerCallError
from open_webui.utils.subscriptions.runner_transport import normalize_runner_url
from open_webui.utils.subscriptions.streaming import collect_events, failure_events, stream_events

log = logging.getLogger(__name__)

STATE_DIR = Path(DATA_DIR) / 'subscriptions'
STATUS_TTL_SECONDS = 60
MODELS_TTL_SECONDS = 600
FIRST_LOAD_WAIT_SECONDS = 20
MACHINES_CONFIG_KEY = 'subscriptions.machines'
SERVER_OWNER_CONFIG_KEY = 'subscriptions.owner'
POLICY_CONFIG_KEY = 'subscriptions.policy'
USER_SETTINGS_PREFIX = 'subscriptions.users'
# Key-free metadata a runner reports in /health that is saved and shown.
HOST_FIELDS = ('id', 'name', 'platform', 'version')
CAPABILITY_FIELDS = ('directories', 'files', 'workspaces', 'terminals', 'pty', 'providerExecution', 'folderBrowsing')
REVERIFY_MACHINE_MESSAGE = 'Verify the selected machine again in subscription settings before using its provider.'
NO_MACHINE_MESSAGE = 'Choose one of your computers for this provider in Settings → Machines.'
SERVER_MACHINE_MESSAGE = 'This server is not available to your account. Choose one of your computers.'
NOT_ALLOWED_MESSAGE = 'Your account cannot use AI plans on computers here. Ask an administrator.'
SINGLE_WORKER_MESSAGE = 'Subscription provider execution and host changes require one Buddy backend worker.'
STARTUP_CLEANUP_MESSAGE = (
    'An earlier provider startup could not confirm that it stopped. '
    'Ask the host operator to stop or restart that host, then restart the Buddy backend before using this provider.'
)

# A computer connected through the Buddy app's Runner, over the tunnel.
MACHINE_KIND_RUNNER = 'runner'
MAX_MACHINES_PER_USER = 10
MACHINE_LIMIT_MESSAGE = f'You can connect up to {MAX_MACHINES_PER_USER} computers. Remove one first.'
# How long the token behind "Connect this computer" stays usable.
ENROLLMENT_SECONDS = 60
MAX_PENDING_ENROLLMENTS = 500
# Tunnel close codes, from buddy_runner.protocol.
CLOSE_UNKNOWN_MACHINE = 4401
CLOSE_NOT_ALLOWED = 4403
REMOVED_MACHINE_MESSAGE = 'This computer was removed from Buddy. Connect it again from the Buddy app.'
REMOVED_COMPUTER_MESSAGE = 'That computer was removed. Choose another.'
OWN_COMPUTER_SETTINGS_MESSAGE = (
    'This computer has its own start folder and CLI paths now. Change them in Settings → Machines.'
)

PROVIDER_IDS = ('claude', 'codex')
PROVIDER_LABELS = {'claude': 'Claude', 'codex': 'ChatGPT'}
MODEL_ID_PREFIXES = {'claude': 'claude-code', 'codex': 'codex'}
MODEL_TAGS = {'claude': 'Claude plan', 'codex': 'ChatGPT plan'}
API_KEY_MODEL_TAGS = {'claude': 'Anthropic API key', 'codex': 'OpenAI API key'}
API_KEY_LABELS = {'claude': 'Anthropic API key', 'codex': 'OpenAI API key'}
# A token from ``claude setup-token`` lasts a year; warn this long before it ends.
PLAN_TOKEN_DAYS = 365
CREDENTIAL_EXPIRY_WARNING_DAYS = 30
MAX_SECRET_LENGTH = 1024

# Who may connect their own computers and use AI plans on them.
POLICY_ALL = 'all'
POLICY_GROUPS = 'groups'
POLICY_ADMINS = 'admins'
USER_MACHINE_POLICIES = (POLICY_ALL, POLICY_GROUPS, POLICY_ADMINS)


@dataclass(frozen=True)
class ProviderRef:
    """One user's provider: whose settings, sign-in, and machines a request uses."""

    owner_id: str
    provider_id: str

    def on(self, machine_id: str) -> 'MachineRef':
        return MachineRef(self.owner_id, self.provider_id, machine_id)


@dataclass(frozen=True)
class MachineRef:
    """One user's provider on one of their computers: what runs there, and its state there."""

    owner_id: str
    provider_id: str
    machine_id: str

    @property
    def ref(self) -> ProviderRef:
        return ProviderRef(self.owner_id, self.provider_id)


@dataclass
class SubscriptionPolicy:
    user_machines: str = POLICY_ALL
    # The groups whose members may connect computers when user_machines is 'groups'.
    machine_group_ids: list[str] = field(default_factory=list)
    # Signing in with a Claude or ChatGPT plan. A public deployment turns this
    # off: Anthropic does not allow third-party products to offer claude.ai
    # login without its approval. API keys are the alternative.
    plan_sign_in: bool = True
    # Users' own Anthropic and OpenAI API keys, billed per use.
    api_keys: bool = True
    # Whether the instance owner may also run providers on this server.
    server_machine: bool = True
    # Where people get the Buddy app for Windows; Settings → Machines links to it.
    desktop_download_url: str = ''


LOCAL_MACHINE = LocalMachine(STATE_DIR)
# RemoteMachine objects keep an HTTP session, so they are reused until their
# address or key changes.
_remote_machines: dict[str, RemoteMachine] = {}
# One handle per connected computer, so tracked processes keep their machine.
_tunnel_machines: dict[str, TunnelMachine] = {}
# Pending "Connect this computer" requests: token digest -> owner and expiry.
_enrollments: dict[str, dict] = {}
_providers: dict[ProviderRef, Any] = {}
_provider_locks: dict[ProviderRef, asyncio.Lock] = {}
_machine_lock = asyncio.Lock()
_computer_settings_lock = asyncio.Lock()
_provider_generations: dict[ProviderRef, int] = {}
# Each running status check, model list or turn, with the computer it runs on.
_active_provider_tasks: dict[ProviderRef, dict[asyncio.Task, str]] = {}
# One handle per computer a provider runs on, so a turn on one computer
# never drops the handle of a turn still running on another.
_tracked_machines: dict[MachineRef, 'TrackingMachine'] = {}
# The computer each provider's sign-in in progress runs on. A provider signs
# in on one computer at a time.
_login_computers: dict[ProviderRef, str] = {}
_server_owner_id: str | None = None


def check_provider_id(provider_id: str) -> None:
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown subscription provider.')


def _ref(owner_id: str, provider_id: str) -> ProviderRef:
    check_provider_id(provider_id)
    return ProviderRef(owner_id, provider_id)


def _owner_refs(owner_id: str) -> list[ProviderRef]:
    return [ProviderRef(owner_id, provider_id) for provider_id in PROVIDER_IDS]


def _provider_lock(ref: ProviderRef) -> asyncio.Lock:
    return _provider_locks.setdefault(ref, asyncio.Lock())


def _require_single_worker() -> None:
    if UVICORN_WORKERS != 1:
        raise SubscriptionError(SINGLE_WORKER_MESSAGE)


class _ProviderLocks:
    """Machine edits acquire one owner's provider locks in one consistent order."""

    def __init__(self, owner_id: str):
        self.owner_id = owner_id

    async def __aenter__(self):
        self.acquired = []
        try:
            for ref in _owner_refs(self.owner_id):
                lock = _provider_lock(ref)
                await lock.acquire()
                self.acquired.append(lock)
        except BaseException:
            for lock in reversed(self.acquired):
                lock.release()
            raise

    async def __aexit__(self, *args):
        for lock in reversed(self.acquired):
            lock.release()


# Instance owner and policy


async def server_owner_id() -> str | None:
    """The user who owns this Buddy instance's original subscription setup.

    That is the first account, which Open WebUI makes an administrator, or
    else the first administrator found. It is remembered once found, so it
    does not change when other administrators are added later.
    """
    global _server_owner_id
    if _server_owner_id:
        return _server_owner_id
    stored = await Config.get(SERVER_OWNER_CONFIG_KEY)
    if isinstance(stored, str) and stored:
        _server_owner_id = stored
        return stored
    owner = await Users.get_first_user()
    if owner is None or owner.role != 'admin':
        owner = await Users.get_super_admin_user()
    if owner is None:
        return None
    await Config.upsert({SERVER_OWNER_CONFIG_KEY: owner.id})
    _server_owner_id = owner.id
    return owner.id


async def get_policy() -> SubscriptionPolicy:
    stored = await Config.get(POLICY_CONFIG_KEY, {}) or {}
    policy = SubscriptionPolicy()
    if stored.get('user_machines') in USER_MACHINE_POLICIES:
        policy.user_machines = stored['user_machines']
    group_ids = stored.get('machine_group_ids')
    if isinstance(group_ids, list):
        policy.machine_group_ids = [group_id for group_id in group_ids if isinstance(group_id, str)]
    if isinstance(stored.get('plan_sign_in'), bool):
        policy.plan_sign_in = stored['plan_sign_in']
    if isinstance(stored.get('api_keys'), bool):
        policy.api_keys = stored['api_keys']
    if isinstance(stored.get('server_machine'), bool):
        policy.server_machine = stored['server_machine']
    if isinstance(stored.get('desktop_download_url'), str):
        policy.desktop_download_url = stored['desktop_download_url']
    return policy


def _validate_download_url(value) -> str:
    """An HTTPS link people can open to get the Buddy app, or '' for none."""
    if not isinstance(value, str):
        raise SubscriptionError('Enter the download link as text.')
    value = value.strip()
    if not value:
        return ''
    parsed = urlsplit(value)
    if len(value) > 2000 or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise SubscriptionError('Enter an https:// download link without a user name or password.')
    return value


async def save_policy(updates: dict) -> SubscriptionPolicy:
    """Save the administrator's policy; work it no longer allows is stopped."""
    _require_single_worker()
    policy = await get_policy()
    if updates.get('user_machines') is not None:
        if updates['user_machines'] not in USER_MACHINE_POLICIES:
            raise SubscriptionError('Choose everyone, selected groups, or administrators only.')
        policy.user_machines = updates['user_machines']
    if updates.get('machine_group_ids') is not None:
        group_ids = updates['machine_group_ids']
        if not isinstance(group_ids, list) or not all(isinstance(group_id, str) for group_id in group_ids):
            raise SubscriptionError('Choose groups by their IDs.')
        policy.machine_group_ids = list(dict.fromkeys(group_ids))
    if updates.get('plan_sign_in') is not None:
        policy.plan_sign_in = bool(updates['plan_sign_in'])
    if updates.get('api_keys') is not None:
        policy.api_keys = bool(updates['api_keys'])
    if updates.get('server_machine') is not None:
        policy.server_machine = bool(updates['server_machine'])
    if updates.get('desktop_download_url') is not None:
        policy.desktop_download_url = _validate_download_url(updates['desktop_download_url'])
    await Config.upsert({POLICY_CONFIG_KEY: asdict(policy)})
    for ref in list(_provider_generations) + _tracked_refs() + list(_providers):
        invalidate(ref)
    # Before disconnecting computers, so their processes can still be stopped.
    await _stop_disallowed_providers()
    await _disconnect_disallowed_runners()
    return policy


async def _policy_allows_provider(ref: ProviderRef) -> bool:
    """Whether the saved policy still allows this provider's owner, sign-in and, on this server, machine."""
    owner = await Users.get_user_by_id(ref.owner_id)
    if not await may_use_machines(owner):
        return False
    try:
        await _require_allowed_credential(ref, await _load_credential_mode(ref))
    except SubscriptionError:
        return False
    settings = await _load_settings(ref)
    if settings.machine_id == LOCAL_MACHINE_ID:
        return await _may_use_server_machine(ref.owner_id)
    return True


def _has_work_to_stop(ref: ProviderRef) -> bool:
    """Whether this provider has a live provider, a tracked machine or running tasks."""
    return ref in _providers or bool(_tracked_handles(ref)) or bool(_active_provider_tasks.get(ref))


async def _stop_provider_if_disallowed(ref: ProviderRef) -> None:
    # Nothing runs for it, as for a deleted user: checking would only bring its provider back.
    if not _has_work_to_stop(ref):
        return
    try:
        allowed = await _policy_allows_provider(ref)
    except Exception as error:
        # Fail closed: work the policy may forbid is stopped when it cannot be checked.
        log.warning('Could not check the policy for %s of %s, so it is stopped: %s',
                    PROVIDER_LABELS[ref.provider_id], ref.owner_id, error)
        allowed = False
    async with _provider_lock(ref):
        if not allowed:
            await _quiesce_provider(ref)
        elif _has_work_on(ref, LOCAL_MACHINE_ID) and not await _may_use_server_machine(ref.owner_id):
            # The plan runs elsewhere, but a sign-in or turn on this server must stop too.
            await _quiesce_computer(ref, LOCAL_MACHINE_ID)


async def _stop_disallowed_providers() -> None:
    """Stop turns, sign-ins and app-servers that the policy no longer allows.

    New work is refused by the policy checks when it starts. Settings are
    kept, so turning a switch back on needs nothing else.
    """
    refs = list(set(_providers) | set(_tracked_refs()) | set(_active_provider_tasks))
    results = await asyncio.gather(*(_stop_provider_if_disallowed(ref) for ref in refs), return_exceptions=True)
    for ref, result in zip(refs, results):
        if isinstance(result, Exception):
            # The policy is already saved. A provider whose cleanup could not be
            # confirmed is turned off by _quiesce_provider.
            log.warning(
                'Could not confirm that %s stopped for %s: %s', PROVIDER_LABELS[ref.provider_id], ref.owner_id, result
            )


async def _disconnect_disallowed_runners() -> None:
    for connection in HUB.connections():
        owner = await Users.get_user_by_id(connection.owner_id)
        if owner is None or not await may_use_machines(owner):
            await HUB.disconnect(connection.machine_id, CLOSE_NOT_ALLOWED, NOT_ALLOWED_MESSAGE)


async def may_use_machines(user) -> bool:
    """Whether this user may connect computers and use AI plans on them, under the policy."""
    if user is None or getattr(user, 'role', None) not in ('user', 'admin'):
        return False
    if user.id == await server_owner_id():
        return True
    policy = await get_policy()
    if policy.user_machines == POLICY_ALL:
        return True
    if user.role == 'admin':
        return True
    if policy.user_machines == POLICY_ADMINS:
        return False
    group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id)}
    return bool(group_ids & set(policy.machine_group_ids))


async def _may_use_server_machine(owner_id: str) -> bool:
    if owner_id != await server_owner_id():
        return False
    return (await get_policy()).server_machine


async def _machine_revision(owner_id: str, machine_id: str) -> str | None:
    if machine_id == LOCAL_MACHINE_ID:
        return 'local'
    config = next((machine for machine in await _owned_machine_configs(owner_id) if machine['id'] == machine_id), None)
    revision = (config or {}).get('revision')
    if isinstance(revision, str) and revision:
        return revision
    return None


async def _require_verified_machine(ref: ProviderRef, settings: ProviderSettings) -> None:
    if not settings.machine_id:
        raise SubscriptionError(NO_MACHINE_MESSAGE)
    if settings.machine_id == LOCAL_MACHINE_ID:
        if not await _may_use_server_machine(ref.owner_id):
            raise SubscriptionError(SERVER_MACHINE_MESSAGE)
        return
    if await _machine_revision(ref.owner_id, settings.machine_id) is None:
        raise SubscriptionError(REVERIFY_MACHINE_MESSAGE)


def _selects_computer(settings: ProviderSettings) -> bool:
    """Whether the settings name one of the user's computers, rather than this server or nothing yet."""
    return settings.machine_id not in ('', LOCAL_MACHINE_ID)


async def _check_expected_machine(
    owner_id: str, settings: ProviderSettings, expected_machine_id: str | None,
    expected_machine_revision: str | None = None, mutation=False
) -> None:
    if expected_machine_id is None and mutation and _selects_computer(settings):
        raise HTTPException(status_code=409, detail='Confirm the selected machine by refreshing before trying again.')
    if expected_machine_id is not None and expected_machine_id != settings.machine_id:
        raise HTTPException(status_code=409, detail='The selected machine changed. Refresh before trying again.')
    revision = await _machine_revision(owner_id, settings.machine_id)
    if mutation and _selects_computer(settings) and (not revision or not expected_machine_revision):
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

    def __init__(self, machine, credential_env: dict | None = None):
        self.machine = machine
        # The token or API key the CLI signs in with, when Buddy keeps one for the user.
        self.credential_env = dict(credential_env or {})
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
        self._check_startup_cleanup()
        if self.cleanup_error is not None or (not self.valid and (self.processes or self.startups)):
            raise SubscriptionError(
                'An old provider operation could not confirm that it stopped. Once its machine is reachable, '
                'turn the provider off and on in subscription settings to retry cleanup. If its Runner '
                'restarted, check that the old CLI stopped, then restart the Buddy backend.'
            )

    def _check_startup_cleanup(self):
        """Refuse while a startup may have left a process Buddy has no handle to stop."""
        if self.startup_cleanup_error is not None:
            raise SubscriptionError(self._startup_cleanup_message())

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
        if self.credential_env:
            extra_env = {**self.credential_env, **(extra_env or {})}
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

    async def retry_cleanup(self):
        """Stop again the processes whose cleanup could not be confirmed; clear the error once all are."""
        if self.cleanup_error is None:
            return
        processes = list(self.processes)
        results = await asyncio.gather(*(self._dispose_process(process) for process in processes), return_exceptions=True)
        if not any(isinstance(result, BaseException) for result in results):
            self.cleanup_error = None

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


def _tracked_handles(ref: ProviderRef) -> list[TrackingMachine]:
    """The provider's handles on every computer it has run on."""
    return [tracked for key, tracked in _tracked_machines.items() if key.ref == ref]


def _tracked_refs() -> list[ProviderRef]:
    return list({key.ref for key in _tracked_machines})


def _track_task(ref: ProviderRef, machine_id: str, task: asyncio.Task) -> None:
    _active_provider_tasks.setdefault(ref, {})[task] = machine_id


def _untrack_task(ref: ProviderRef, task: asyncio.Task) -> None:
    _active_provider_tasks.get(ref, {}).pop(task, None)


def _running_tasks(ref: ProviderRef, machine_id: str | None = None) -> set[asyncio.Task]:
    """The provider's other unfinished tasks, on one computer or on all of them."""
    current = asyncio.current_task()
    tasks = set()
    for task, task_machine_id in _active_provider_tasks.get(ref, {}).items():
        if task is current or task.done():
            continue
        if machine_id is None or task_machine_id == machine_id:
            tasks.add(task)
    return tasks


def _waits_for_its_computer(tracked: TrackingMachine) -> bool:
    """A Buddy app computer that is offline: its stops are confirmed when it connects again."""
    return isinstance(tracked.machine, TunnelMachine) and not HUB.is_online(tracked.id)


def _require_computer_cleanup(ref: ProviderRef, machine_id: str) -> None:
    """Old work whose stop is unconfirmed blocks the provider's new work.

    Unconfirmed work on an offline Buddy app computer blocks only that
    computer, so a laptop that sleeps doesn't stop the plan everywhere; it is
    retried when the computer connects again. Anywhere else it blocks the
    provider on every computer, as does a startup that may have left a
    process Buddy has no handle to stop.
    """
    for key, tracked in _tracked_machines.items():
        if key.ref != ref:
            continue
        tracked._check_startup_cleanup()
        if key.machine_id == machine_id or not _waits_for_its_computer(tracked):
            tracked._check_cleanup()


def _tracking_machine(ref: ProviderRef, machine, credential_env: dict | None = None) -> TrackingMachine:
    _require_computer_cleanup(ref, machine.id)
    key = ref.on(machine.id)
    tracked = _tracked_machines.get(key)
    if tracked is None or tracked.machine is not machine or not tracked.valid:
        tracked = TrackingMachine(machine, credential_env)
        _tracked_machines[key] = tracked
    return tracked


async def _tracked(ref: ProviderRef, settings: ProviderSettings) -> TrackingMachine:
    """The provider's machine handle, with the sign-in Buddy gives its CLI.

    A credential change always quiesces the provider first, which drops the
    old handle, so a kept handle never carries an old token or key.
    """
    credential_mode = await _load_credential_mode(ref)
    await _require_allowed_credential(ref, credential_mode)
    machine = await get_machine(ref.owner_id, settings.machine_id)
    credential_env = await _credential_env(ref, credential_mode)
    provider = await get_provider(ref)
    if isinstance(provider, CodexProvider):
        provider.use_credential(credential_mode, await _codex_api_key(ref, credential_mode))
    else:
        provider.credential_mode = credential_mode
    return _tracking_machine(ref, machine, credential_env)


async def _quiesce_provider(ref: ProviderRef) -> None:
    """Called with its provider lock held, after committing disabled settings."""
    operation = asyncio.create_task(_quiesce_provider_safely(ref))
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await _finish_cancelled_cleanup(operation)
        raise


async def _quiesce_provider_safely(ref: ProviderRef) -> None:
    try:
        await _quiesce_provider_work(ref)
    except Exception:
        # Failed cleanup must never leave a provider enabled for new work.
        # Access, folder and CLI path stay, so turning it on retries cleanup.
        settings = await _load_settings(ref)
        settings.enable = False
        await Config.upsert({await _settings_key(ref): asdict(settings)})
        raise


async def _quiesce_provider_work(ref: ProviderRef) -> None:
    _provider_generations[ref] = _provider_generations.get(ref, 0) + 1
    invalidate(ref)
    tasks = _running_tasks(ref)
    for task in tasks:
        task.cancel()
    handles = [(key, tracked) for key, tracked in _tracked_machines.items() if key.ref == ref]
    cleanup_error = None
    revoked = await asyncio.gather(*(tracked.revoke() for _, tracked in handles), return_exceptions=True)
    stopped = []
    for (key, tracked), outcome in zip(handles, revoked):
        if outcome is None:
            stopped.append((key, tracked))
        elif not isinstance(outcome, SubscriptionError):
            raise outcome
        elif _waits_for_its_computer(tracked):
            # Kept with its error: new work there waits until the computer
            # connects again and the stop is confirmed.
            log.info('Cleanup on %s waits for it to connect again: %s', key.machine_id, outcome)
        else:
            cleanup_error = outcome
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=10)
        if pending:
            cleanup_error = SubscriptionError('An old provider operation could not confirm that it stopped.')
    provider = await get_provider(ref)
    await provider.cancel_login()
    _login_computers.pop(ref, None)
    if isinstance(provider, CodexProvider):
        provider.stop()
    if cleanup_error:
        raise cleanup_error
    for key, tracked in stopped:
        if _tracked_machines.get(key) is tracked:
            _tracked_machines.pop(key)


async def _quiesce_computer(ref: ProviderRef, machine_id: str) -> None:
    """Stop the provider's work on one computer: its sign-in, tasks, processes and Codex app-server there.

    Called with the provider lock held. Work on the plan's other computers
    keeps running. A stop that can't be confirmed stays on that computer's
    handle, which keeps refusing new work there until it is.
    """
    invalidate_computer(ref.owner_id, machine_id)
    tasks = _running_tasks(ref, machine_id)
    for task in tasks:
        task.cancel()
    provider = await get_provider(ref)
    if _login_computers.get(ref) == machine_id:
        await provider.cancel_login()
        _login_computers.pop(ref, None)
    key = ref.on(machine_id)
    tracked = _tracked_machines.get(key)
    cleanup_error = None
    if tracked is not None:
        try:
            await tracked.revoke()
        except SubscriptionError as error:
            cleanup_error = error
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=10)
        if pending:
            cleanup_error = SubscriptionError('An old provider operation could not confirm that it stopped.')
    if isinstance(provider, CodexProvider):
        provider.stop_machine(machine_id)
    if cleanup_error is not None:
        raise cleanup_error
    if tracked is not None and _tracked_machines.get(key) is tracked:
        _tracked_machines.pop(key)


def _has_work_on(ref: ProviderRef, machine_id: str) -> bool:
    return (
        ref.on(machine_id) in _tracked_machines
        or _login_computers.get(ref) == machine_id
        or bool(_running_tasks(ref, machine_id))
    )


async def _stop_other_work_on(owner_id: str, machine_id: str, plan_refs: list[ProviderRef]) -> None:
    """Before a computer is removed or replaced, stop the work other plans run there.

    Plans whose own computer it is are stopped whole by the caller. This
    runs while the computer is still connected, so stops can be confirmed.
    """
    for ref in _owner_refs(owner_id):
        if ref in plan_refs or not _has_work_on(ref, machine_id):
            continue
        try:
            await _quiesce_computer(ref, machine_id)
        except SubscriptionError as error:
            log.warning('Could not confirm that %s stopped on %s: %s', PROVIDER_LABELS[ref.provider_id], machine_id, error)


async def _quiesce_providers(refs: list[ProviderRef]) -> None:
    results = await asyncio.gather(*(_quiesce_provider(ref) for ref in refs), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _owner_state_dir(owner_id: str) -> Path:
    """Where a user's provider keeps its own files, such as Claude's session map."""
    if owner_id == await server_owner_id():
        return STATE_DIR
    return STATE_DIR / 'users' / owner_id


async def get_provider(ref: ProviderRef):
    provider = _providers.get(ref)
    if provider is not None:
        return provider
    check_provider_id(ref.provider_id)
    if ref.provider_id == 'claude':
        provider = ClaudeCodeProvider(await _owner_state_dir(ref.owner_id))
    else:
        provider = CodexProvider()
    # Another request may have created it while the state folder was looked up.
    return _providers.setdefault(ref, provider)


async def retry_machine_cleanups(machine_id: str) -> None:
    """A machine came back: retry stopping processes whose cleanup could not be confirmed."""
    for tracked in list(_tracked_machines.values()):
        if tracked.id == machine_id:
            try:
                await tracked.retry_cleanup()
            except Exception as error:
                log.info('Cleanup on %s is still unconfirmed: %s', machine_id, error)


# Machines


async def _machine_configs() -> list[dict]:
    stored = await Config.get(MACHINES_CONFIG_KEY, []) or []
    return [machine for machine in stored if isinstance(machine, dict) and machine.get('id')]


def _machine_owner(config: dict, server_owner: str | None) -> str | None:
    """A machine's owner; entries saved before machines were per user belong to the instance owner."""
    return config.get('user_id') or server_owner


async def _owned_machine_configs(owner_id: str) -> list[dict]:
    server_owner = await server_owner_id()
    return [machine for machine in await _machine_configs() if _machine_owner(machine, server_owner) == owner_id]


async def get_machine(owner_id: str, machine_id: str):
    if not machine_id:
        if await _may_use_server_machine(owner_id):
            return LOCAL_MACHINE
        raise SubscriptionError(NO_MACHINE_MESSAGE)
    if machine_id == LOCAL_MACHINE_ID:
        if await _may_use_server_machine(owner_id):
            return LOCAL_MACHINE
        raise SubscriptionError(SERVER_MACHINE_MESSAGE)

    config = next((machine for machine in await _owned_machine_configs(owner_id) if machine['id'] == machine_id), None)
    if not config:
        raise SubscriptionError('The selected machine no longer exists. Choose another in the subscription settings.')

    if config.get('kind') == MACHINE_KIND_RUNNER:
        machine = _tunnel_machines.get(machine_id)
        if machine is None:
            machine = TunnelMachine(machine_id, config['name'], HUB)
            _tunnel_machines[machine_id] = machine
        machine.name = config['name']
        return machine

    key = config.get('key') or ''
    if not key:
        # A config import keeps no key for a machine whose address changed.
        raise SubscriptionError(f'Enter the runner key for {config["name"]} again in the subscription settings.')

    cached = _remote_machines.get(machine_id)
    if cached and cached.matches(cached.name, config['url'], key):
        cached.name = config['name']
        return cached
    if cached:
        await cached.close()
    machine = RemoteMachine(machine_id, config['name'], config['url'], key)
    _remote_machines[machine_id] = machine
    return machine


async def describe_machines(owner_id: str) -> list[dict]:
    """A user's machines for the settings page; runner keys never leave the server."""
    machines = []
    if await _may_use_server_machine(owner_id):
        machines.append({'id': LOCAL_MACHINE_ID, 'name': LOCAL_MACHINE.name, 'url': None})
    for config in await _owned_machine_configs(owner_id):
        machines.append(_public_machine(config))
    return machines


async def describe_all_machines() -> list[dict]:
    """Every user's machines, for administrators: owners and status, never keys or settings."""
    server_owner = await server_owner_id()
    owner_names: dict[str, str] = {}
    machines = []
    for config in await _machine_configs():
        machine = _public_machine(config)
        owner_id = _machine_owner(config, server_owner)
        if owner_id and owner_id not in owner_names:
            owner = await Users.get_user_by_id(owner_id)
            owner_names[owner_id] = (getattr(owner, 'name', None) or getattr(owner, 'email', None) or owner_id)
        machine['owner_id'] = owner_id
        machine['owner_name'] = owner_names.get(owner_id, '')
        machines.append(machine)
    return machines


async def admin_delete_machine(machine_id: str) -> None:
    """An administrator removes anyone's machine, as its owner would."""
    server_owner = await server_owner_id()
    config = next((machine for machine in await _machine_configs() if machine['id'] == machine_id), None)
    if config is None:
        raise SubscriptionError('That machine no longer exists.')
    await delete_machine(_machine_owner(config, server_owner), machine_id)


def _public_machine(config: dict) -> dict:
    if config.get('kind') == MACHINE_KIND_RUNNER:
        return _public_runner_machine(config)
    # A config import can store a machine without its key; the form then asks for one.
    machine = {'id': config['id'], 'name': config['name'], 'url': config['url'], 'has_key': bool(config.get('key'))}
    if isinstance(config.get('revision'), str):
        machine['revision'] = config['revision']
    if isinstance(config.get('browser_url'), str):
        machine['browser_url'] = config['browser_url']
    observed_host = _observed_host(config)
    if observed_host is not None:
        machine['host'] = observed_host
    observed_capabilities = _observed_capabilities(config)
    if observed_capabilities is not None:
        machine['capabilities'] = observed_capabilities
    return machine


def _public_runner_machine(config: dict) -> dict:
    machine = {
        'id': config['id'],
        'name': config['name'],
        'kind': MACHINE_KIND_RUNNER,
        'url': None,
        'online': HUB.is_online(config['id']),
    }
    for field_name in ('revision', 'created_at', 'last_seen_at'):
        if config.get(field_name) is not None:
            machine[field_name] = config[field_name]
    observed_host = _observed_host(config)
    if observed_host is not None:
        machine['host'] = observed_host
    observed_capabilities = _observed_capabilities(config)
    if observed_capabilities is not None:
        machine['capabilities'] = observed_capabilities
    return machine


def redact_runner_keys(values: dict) -> dict:
    """Config values for export or display, with every runner key left out."""
    machines = values.get(MACHINES_CONFIG_KEY)
    if not isinstance(machines, list):
        return values
    redacted = []
    for machine in machines:
        if isinstance(machine, dict):
            machine = {field: value for field, value in machine.items() if field not in ('key', 'credential_hash')}
        redacted.append(machine)
    return {**values, MACHINES_CONFIG_KEY: redacted}


async def keep_runner_keys(imported: dict) -> dict:
    """Config values to import, with saved keys kept for machines exported without one.

    A saved key is kept only for the same machine at the same address; a
    machine whose address changed needs its key entered again.
    """
    machines = imported.get(MACHINES_CONFIG_KEY)
    if not isinstance(machines, list):
        return imported
    saved_machines = {machine['id']: machine for machine in await _machine_configs()}
    restored = []
    for machine in machines:
        if isinstance(machine, dict) and machine.get('kind') == MACHINE_KIND_RUNNER:
            # A connected computer keeps its credential only as that same machine.
            saved = saved_machines.get(machine.get('id')) or {}
            machine = {field: value for field, value in machine.items() if field != 'credential_hash'}
            if saved.get('kind') == MACHINE_KIND_RUNNER and saved.get('credential_hash'):
                machine['credential_hash'] = saved['credential_hash']
        elif isinstance(machine, dict) and not machine.get('key'):
            saved = saved_machines.get(machine.get('id')) or {}
            same_address = _runner_address(saved.get('url')) == _runner_address(machine.get('url'))
            if saved.get('key') and same_address:
                machine = {**machine, 'key': saved['key']}
        restored.append(machine)
    return {**imported, MACHINES_CONFIG_KEY: restored}


def _runner_address(url) -> str | None:
    """The canonical runner origin, or the text as saved for a legacy, unsafe address."""
    if not url:
        return url
    try:
        return normalize_runner_url(url)
    except SubscriptionError:
        return url


def _observed_host(info: dict) -> dict | None:
    """The host identity fields a runner reported, or None when it reported none."""
    host = info.get('host')
    if not isinstance(host, dict):
        return None
    return {field: host[field] for field in HOST_FIELDS if isinstance(host.get(field), str)}


def _observed_capabilities(info: dict) -> dict | None:
    """The capability flags a runner reported, or None when it reported none."""
    capabilities = info.get('capabilities')
    if not isinstance(capabilities, dict):
        return None
    return {field: capabilities[field] for field in CAPABILITY_FIELDS if isinstance(capabilities.get(field), bool)}


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


async def _taken_machine_ids(configs: list[dict]) -> set[str]:
    """IDs a new machine must not reuse: every machine, selection, and cleanup owner."""
    taken = {config['id'] for config in configs}
    for ref in set(_provider_generations) | set(_tracked_refs()) | set(_providers):
        taken.add((await _load_settings(ref)).machine_id)
    taken.update(tracked.id for tracked in _tracked_machines.values())
    return taken


async def verify_machine(url: str, key: str, browser_url: str | None = None) -> dict:
    """Check that the runner answers with the key. A browser address is validated as on save."""
    validate_browser_url(browser_url)
    url = normalize_runner_url(url)
    probe = RemoteMachine('verify', 'The runner', url, key)
    try:
        return await probe.info()
    finally:
        await probe.close()


async def save_machine(
    owner_id: str, machine_id: str | None, name: str, url: str, key: str | None, browser_url: str | None = None,
    *, browser_url_supplied: bool | None = None
) -> dict:
    """Preserve an omitted browser address; explicit null uses its supplied flag."""
    _require_single_worker()
    async with _machine_lock, _ProviderLocks(owner_id):
        return await _save_machine(
            owner_id, machine_id, name, url, key, browser_url, browser_url_supplied=browser_url_supplied
        )


async def _save_machine(
    owner_id: str, machine_id: str | None, name: str, url: str, key: str | None, browser_url: str | None = None,
    *, browser_url_supplied: bool | None = None
) -> dict:
    """Add or update one of the owner's runners after checking that it answers with the key."""
    name = name.strip()
    url = normalize_runner_url(url)
    if not name or not url:
        raise SubscriptionError('Enter a name and the runner address.')

    configs = await _machine_configs()
    owned = await _owned_machine_configs(owner_id)
    existing = None
    if machine_id:
        existing_id = next((config['id'] for config in owned if config['id'] == machine_id), None)
        existing = next((config for config in configs if config['id'] == existing_id), None)
    # Replacing a legacy, unsafe address must reset provider authority.
    previous_endpoint = _runner_address((existing or {}).get('url'))
    previous_key = (existing or {}).get('key')
    previous_instance = ((existing or {}).get('host') or {}).get('id')
    key = (key or '').strip()
    if not key and existing:
        if previous_endpoint != url:
            # A saved key is only ever sent to the address it was saved for.
            raise SubscriptionError('Enter the runner key for the new address. The saved key stays with the old one.')
        key = previous_key or ''
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
        # Deleted selections and retained cleanup owners must never reconnect
        # automatically when a different host registers under the same name.
        taken = await _taken_machine_ids(configs)
        for ref in _owner_refs(owner_id):
            taken.add((await _load_settings(ref)).machine_id)
        saved = {'id': _machine_id_from_name(name, taken), 'name': name, 'url': url}
        saved['key'] = key
        if owner_id != await server_owner_id():
            saved['user_id'] = owner_id
        configs.append(saved)
    if browser_url_supplied:
        saved['browser_url'] = browser_url
    # Only key-free, observed metadata reaches the UI. Paths in health are not grants.
    observed_host = _observed_host(info)
    if observed_host is None:
        saved.pop('host', None)
    else:
        saved['host'] = observed_host
    observed_capabilities = _observed_capabilities(info)
    if observed_capabilities is None:
        saved.pop('capabilities', None)
    else:
        saved['capabilities'] = observed_capabilities
    updates = {MACHINES_CONFIG_KEY: configs}
    host_changed = bool(existing) and (
        previous_endpoint != url or previous_key != key
        or (previous_instance and previous_instance != saved.get('host', {}).get('id'))
    )
    if host_changed or not isinstance(saved.get('revision'), str) or not saved['revision']:
        saved['revision'] = uuid.uuid4().hex
    affected = []
    if host_changed:
        for ref in _owner_refs(owner_id):
            settings = await _load_settings(ref)
            if settings.machine_id == saved['id']:
                _forget_machine_authority(settings)
                updates[await _settings_key(ref)] = asdict(settings)
                affected.append(ref)
    await Config.upsert(updates)
    try:
        if host_changed:
            await _stop_other_work_on(owner_id, saved['id'], affected)
        await _quiesce_providers(affected)
    finally:
        if host_changed:
            cached = _remote_machines.pop(saved['id'], None)
            if cached:
                await cached.close()
    for ref in _owner_refs(owner_id):
        invalidate(ref)
    return _public_machine(saved)


async def delete_machine(owner_id: str, machine_id: str) -> None:
    _require_single_worker()
    async with _machine_lock, _ProviderLocks(owner_id):
        await _delete_machine(owner_id, machine_id)


async def _delete_machine(owner_id: str, machine_id: str) -> None:
    if machine_id == LOCAL_MACHINE_ID:
        raise SubscriptionError('This server cannot be removed.')
    server_owner = await server_owner_id()
    all_configs = await _machine_configs()
    removed = next(
        (config for config in all_configs
         if config['id'] == machine_id and _machine_owner(config, server_owner) == owner_id),
        None,
    )
    configs = [config for config in all_configs if config is not removed]
    updates = {MACHINES_CONFIG_KEY: configs}
    affected = []
    for ref in _owner_refs(owner_id):
        settings = await _load_settings(ref)
        if settings.machine_id == machine_id:
            _forget_machine_authority(settings)
            # Keep the missing ID so execution cannot silently move to another host.
            updates[await _settings_key(ref)] = asdict(settings)
            affected.append(ref)
    await Config.upsert(updates)
    try:
        await _stop_other_work_on(owner_id, machine_id, affected)
        await _quiesce_providers(affected)
    finally:
        cached = _remote_machines.pop(machine_id, None)
        if cached:
            await cached.close()
        if removed is not None and removed.get('kind') == MACHINE_KIND_RUNNER:
            _tunnel_machines.pop(machine_id, None)
            await HUB.disconnect(machine_id, CLOSE_UNKNOWN_MACHINE, REMOVED_MACHINE_MESSAGE)


async def rename_machine(owner_id: str, machine_id: str, name: str) -> dict:
    """Rename one of the user's computers; nothing about its authority changes."""
    name = (name or '').strip()[:80]
    if not name:
        raise SubscriptionError('Enter a name for this computer.')
    async with _machine_lock:
        configs = await _machine_configs()
        server_owner = await server_owner_id()
        config = next(
            (machine for machine in configs
             if machine['id'] == machine_id and _machine_owner(machine, server_owner) == owner_id),
            None,
        )
        if config is None:
            raise SubscriptionError('That computer no longer exists.')
        config['name'] = name
        await Config.upsert({MACHINES_CONFIG_KEY: configs})
    return _public_machine(config)


# Connecting computers through the Buddy app


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def _forget_expired_enrollments() -> None:
    now = time.monotonic()
    for digest, enrollment in list(_enrollments.items()):
        if enrollment['expires_at'] <= now:
            _enrollments.pop(digest, None)


async def create_enrollment(user) -> dict:
    """A single-use token the user's Buddy app trades for a credential for this computer."""
    _require_single_worker()
    if not await may_use_machines(user):
        raise SubscriptionError(NOT_ALLOWED_MESSAGE)
    # Checked again when the token is redeemed: several tokens can be pending at once.
    await _require_room_for_another_computer(user.id, await _machine_configs())
    _forget_expired_enrollments()
    if len(_enrollments) >= MAX_PENDING_ENROLLMENTS:
        raise SubscriptionError('Too many computers are connecting right now. Try again in a minute.')
    token = 'bde1_' + secrets.token_urlsafe(32)
    _enrollments[_digest(token)] = {'owner_id': user.id, 'expires_at': time.monotonic() + ENROLLMENT_SECONDS}
    return {'token': token, 'expires_in': ENROLLMENT_SECONDS}


async def _require_room_for_another_computer(owner_id: str, configs: list[dict]) -> None:
    """Refuse once the owner has MAX_MACHINES_PER_USER computers connected through the Buddy app."""
    server_owner = await server_owner_id()
    connected = [
        config for config in configs
        if config.get('kind') == MACHINE_KIND_RUNNER and _machine_owner(config, server_owner) == owner_id
    ]
    if len(connected) >= MAX_MACHINES_PER_USER:
        raise SubscriptionError(MACHINE_LIMIT_MESSAGE)


async def redeem_enrollment(token: str, name: str | None, host: dict | None) -> dict:
    """Trade an enrollment token, once, for a new machine and its credential.

    The credential is returned only here; Buddy keeps just its digest.
    """
    _require_single_worker()
    _forget_expired_enrollments()
    enrollment = _enrollments.pop(_digest(token or ''), None)
    if enrollment is None:
        raise SubscriptionError('This connection request expired. Choose Connect this computer again.')
    owner = await Users.get_user_by_id(enrollment['owner_id'])
    if owner is None or not await may_use_machines(owner):
        raise SubscriptionError(NOT_ALLOWED_MESSAGE)
    credential = 'bdr1_' + secrets.token_urlsafe(32)
    observed_host = _observed_host({'host': host}) if isinstance(host, dict) else None
    clean_name = (name or '').strip()[:80]
    if not clean_name and observed_host:
        clean_name = (observed_host.get('name') or '').strip()[:80]
    if not clean_name:
        clean_name = 'My computer'
    async with _machine_lock:
        configs = await _machine_configs()
        # Counted with the list it is added to, under the lock. The token is
        # already used up, so a refused computer needs a new one.
        await _require_room_for_another_computer(owner.id, configs)
        taken = await _taken_machine_ids(configs)
        machine_id = uuid.uuid4().hex
        while machine_id in taken:
            machine_id = uuid.uuid4().hex
        saved = {
            'id': machine_id,
            'user_id': owner.id,
            'kind': MACHINE_KIND_RUNNER,
            'name': clean_name,
            'credential_hash': _digest(credential),
            # Fixed for the machine's lifetime: a Runner restart keeps the
            # access and folders approved for it.
            'revision': uuid.uuid4().hex,
            'created_at': int(time.time()),
        }
        if observed_host:
            saved['host'] = observed_host
        configs.append(saved)
        await Config.upsert({MACHINES_CONFIG_KEY: configs})
    for ref in _owner_refs(owner.id):
        invalidate(ref)
    return {'machine_id': machine_id, 'credential': credential, 'name': clean_name}


async def relay_connection(owner_id: str, machine_id: str):
    """The connection to one of the user's own computers, for relaying project requests."""
    config = next((machine for machine in await _owned_machine_configs(owner_id) if machine['id'] == machine_id), None)
    if config is None or config.get('kind') != MACHINE_KIND_RUNNER:
        raise SubscriptionError('That computer no longer exists.')
    connection = HUB.connection(machine_id)
    if connection is None:
        raise SubscriptionError(f'{config["name"]} is offline. Open the Buddy app on that computer.')
    return connection


async def authenticate_runner(machine_id, credential) -> dict | None:
    """The connected computer's machine entry, with its owner, when the credential matches."""
    if not isinstance(machine_id, str) or not isinstance(credential, str) or not credential:
        return None
    config = next((machine for machine in await _machine_configs() if machine['id'] == machine_id), None)
    if config is None or config.get('kind') != MACHINE_KIND_RUNNER:
        return None
    expected = config.get('credential_hash')
    if not isinstance(expected, str) or not hmac.compare_digest(expected, _digest(credential)):
        return None
    return {**config, 'user_id': _machine_owner(config, await server_owner_id())}


async def unenroll_runner(machine_id, credential) -> bool:
    """A computer removes itself with its own credential, as if its owner removed it.

    The Buddy app does this when it can't keep a credential it was just given, and
    before it moves to another Buddy server. False when no computer matches, so a
    removal that was already done reads the same as one that never could be.
    """
    machine = await authenticate_runner(machine_id, credential)
    if machine is None:
        return False
    await delete_machine(machine['user_id'], machine['id'])
    return True


async def record_runner_connected(machine_id: str, info: dict) -> None:
    """Save what a connecting computer reports about itself, and when it was seen."""
    async with _machine_lock:
        configs = await _machine_configs()
        config = next((machine for machine in configs if machine['id'] == machine_id), None)
        if config is None:
            return
        observed_host = _observed_host(info)
        if observed_host is not None:
            config['host'] = observed_host
        observed_capabilities = _observed_capabilities(info)
        if observed_capabilities is not None:
            config['capabilities'] = observed_capabilities
        config['last_seen_at'] = int(time.time())
        await Config.upsert({MACHINES_CONFIG_KEY: configs})
    invalidate_computer(_machine_owner(config, await server_owner_id()), machine_id)


async def record_runner_disconnected(machine_id: str) -> None:
    async with _machine_lock:
        configs = await _machine_configs()
        config = next((machine for machine in configs if machine['id'] == machine_id), None)
        if config is None:
            return
        config['last_seen_at'] = int(time.time())
        await Config.upsert({MACHINES_CONFIG_KEY: configs})


# Provider settings


async def _settings_key(ref: ProviderRef) -> str:
    if ref.owner_id == await server_owner_id():
        # The instance owner's settings predate per-user machines and keep their keys.
        return f'subscriptions.{ref.provider_id}'
    return f'{USER_SETTINGS_PREFIX}.{ref.owner_id}.{ref.provider_id}'


async def _load_settings(ref: ProviderRef) -> ProviderSettings:
    stored = await Config.get(await _settings_key(ref), {}) or {}
    access = stored.get('access')
    if access not in ACCESS_LEVELS:
        access = ACCESS_CHAT
    default_machine_id = ''
    if ref.owner_id == await server_owner_id():
        default_machine_id = LOCAL_MACHINE_ID
    return ProviderSettings(
        enable=bool(stored.get('enable', False)),
        access=access,
        workspace=str(stored.get('workspace') or '').strip(),
        cli_path=str(stored.get('cli_path') or '').strip(),
        machine_id=str(stored.get('machine_id') or default_machine_id),
    )


async def get_settings(owner_id: str, provider_id: str) -> ProviderSettings:
    return await _load_settings(_ref(owner_id, provider_id))


async def _saved_computer_settings(owner_id: str) -> dict[str, 'computer_settings.ComputerSettings']:
    return computer_settings.parse(await Config.get(computer_settings.settings_key(owner_id), {}))


async def _settings_for(ref: ProviderRef, settings: ProviderSettings, machine_id: str) -> ProviderSettings:
    """The plan's settings for work on one computer, with that computer's start folder and CLI path.

    A computer without saved settings finds its CLI itself and has no start
    folder, except the plan's own computer: it keeps the start folder and
    CLI path saved with the plan before computers had their own.
    """
    saved = await _saved_computer_settings(ref.owner_id)
    entry = computer_settings.current(saved, machine_id, await _machine_revision(ref.owner_id, machine_id))
    if entry is not None:
        return replace(
            settings, machine_id=machine_id, workspace=entry.start_folder, cli_path=entry.cli_path(ref.provider_id)
        )
    if machine_id == settings.machine_id:
        return settings
    return replace(settings, machine_id=machine_id, workspace='', cli_path='')


async def describe_computer_settings(owner_id: str, machine_id: str) -> dict:
    """A computer's own start folder and CLI paths, and what plans saved for it before it had its own."""
    revision = await _machine_revision(owner_id, machine_id)
    if revision is None:
        raise SubscriptionError(REMOVED_COMPUTER_MESSAGE)
    saved = computer_settings.current(await _saved_computer_settings(owner_id), machine_id, revision)
    start_folder = ''
    cli_paths = {}
    from_plans = {}
    for provider_id in PROVIDER_IDS:
        cli_paths[provider_id] = ''
        if saved is not None:
            cli_paths[provider_id] = saved.cli_path(provider_id)
            continue
        settings = await _load_settings(_ref(owner_id, provider_id))
        if settings.machine_id == machine_id and (settings.workspace or settings.cli_path):
            # Turns there still use these; each plan kept its own.
            from_plans[provider_id] = {'start_folder': settings.workspace, 'cli_path': settings.cli_path}
    if saved is not None:
        start_folder = saved.start_folder
    return {
        'machine_id': machine_id,
        'revision': revision,
        'saved': saved is not None,
        'start_folder': start_folder,
        'cli_paths': cli_paths,
        'from_plans': from_plans,
    }


async def save_computer_settings(
    owner_id: str, machine_id: str, expected_revision: str, start_folder, cli_paths: dict
) -> dict:
    """Save where plans start on one computer and which CLIs they run, checked on that computer."""
    _require_single_worker()
    revision = await _machine_revision(owner_id, machine_id)
    if revision is None:
        raise SubscriptionError(REMOVED_COMPUTER_MESSAGE)
    if expected_revision != revision:
        raise HTTPException(status_code=409, detail='That computer was set up again. Refresh before trying again.')
    # Also refuses This server while the policy has it off.
    machine = await get_machine(owner_id, machine_id)
    folder = ''
    if isinstance(start_folder, str) and start_folder.strip():
        folder = project_folders.clean_project_path(start_folder.strip(), await _machine_platform(owner_id, machine_id))
        if not await machine.path_exists(folder, 'dir'):
            raise SubscriptionError(f'The start folder does not exist on {machine.name}: {folder}')
    paths = {}
    for provider_id in PROVIDER_IDS:
        path = computer_settings.clean_cli_path((cli_paths or {}).get(provider_id))
        if path and not await machine.path_exists(path, 'file'):
            raise SubscriptionError(f'The CLI path does not exist on {machine.name}: {path}')
        if path:
            paths[provider_id] = path

    async with _computer_settings_lock:
        # The paths were checked on the computer without the lock; it or its owner may be gone since.
        if await _machine_revision(owner_id, machine_id) != revision or await Users.get_user_by_id(owner_id) is None:
            raise SubscriptionError(REMOVED_COMPUTER_MESSAGE)
        saved = await _saved_computer_settings(owner_id)
        # Entries for removed computers are dropped here, the next time anything is saved.
        kept = await _owned_machine_ids(owner_id)
        saved = {kept_id: entry for kept_id, entry in saved.items() if kept_id in kept}
        saved[machine_id] = computer_settings.ComputerSettings(revision, folder, paths)
        await Config.upsert({computer_settings.settings_key(owner_id): computer_settings.serialize(saved)})
    invalidate_computer(owner_id, machine_id)
    return await describe_computer_settings(owner_id, machine_id)


def _forget_machine_authority(settings: ProviderSettings) -> None:
    """Turn the provider off and drop the access and paths approved on its machine."""
    settings.enable = False
    settings.access = ACCESS_CHAT
    settings.workspace = ''
    settings.cli_path = ''


async def save_settings(
    owner_id: str, provider_id: str, updates: dict, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None
) -> ProviderSettings:
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    async with _provider_lock(ref):
        return await _save_settings(ref, updates, expected_machine_id, expected_machine_revision)


async def _save_settings(
    ref: ProviderRef, updates: dict, expected_machine_id: str | None, expected_machine_revision: str | None
) -> ProviderSettings:
    previous = await _load_settings(ref)
    if expected_machine_id is None:
        expected_machine_id = updates.get('expected_machine_id')
    if expected_machine_revision is None:
        expected_machine_revision = updates.get('expected_machine_revision')
    requested_machine_id = updates.get('machine_id')
    recover_missing_machine = False
    if (
        _selects_computer(previous)
        and expected_machine_id == previous.machine_id
        and isinstance(requested_machine_id, str)
        and requested_machine_id
        and requested_machine_id != previous.machine_id
    ):
        # A deleted registry entry has no revision left to confirm. Only an
        # explicit change away from its retained ID can recover the selection.
        recover_missing_machine = not any(
            machine['id'] == previous.machine_id for machine in await _owned_machine_configs(ref.owner_id)
        )
    if recover_missing_machine:
        # Nothing else can retry cleanup of a removed machine's old process.
        # The move saves the provider turned off and quiesces it below, which
        # retries that cleanup. An unknown startup process cannot be retried.
        for tracked in _tracked_handles(ref):
            tracked._check_startup_cleanup()
    else:
        await _check_expected_machine(
            ref.owner_id, previous, expected_machine_id, expected_machine_revision, mutation=True
        )
    current = asdict(previous)
    for setting in fields(ProviderSettings):
        if updates.get(setting.name) is not None:
            current[setting.name] = updates[setting.name]

    settings = ProviderSettings(**current)
    if settings.machine_id != previous.machine_id:
        # Paths and access belong to the old machine. A separate save is required
        # to enable a provider or approve access on the newly selected machine.
        _forget_machine_authority(settings)
    settings.workspace = settings.workspace.strip()
    settings.cli_path = settings.cli_path.strip()
    if settings.access not in ACCESS_LEVELS:
        raise SubscriptionError('Choose chat only, read files, or full access.')
    changes_paths = settings.workspace != previous.workspace or settings.cli_path != previous.cli_path
    if settings.machine_id == previous.machine_id and changes_paths:
        # Turns there use the computer's own settings, so changing the plan's would do nothing.
        revision = await _machine_revision(ref.owner_id, settings.machine_id)
        saved = await _saved_computer_settings(ref.owner_id)
        if computer_settings.current(saved, settings.machine_id, revision) is not None:
            raise SubscriptionError(OWN_COMPUTER_SETTINGS_MESSAGE)

    # Turning a provider off needs nothing from its machine, which may still be
    # waiting for its runner key after a config import.
    only_turns_off = settings == replace(previous, enable=False)
    if not only_turns_off:
        machine = await get_machine(ref.owner_id, settings.machine_id)
        if settings.workspace and not await machine.path_exists(settings.workspace, 'dir'):
            raise SubscriptionError(f'The start folder does not exist on {machine.name}: {settings.workspace}')
        if settings.cli_path and not await machine.path_exists(settings.cli_path, 'file'):
            raise SubscriptionError(f'The CLI path does not exist on {machine.name}: {settings.cli_path}')

    await Config.upsert({await _settings_key(ref): asdict(settings)})
    if settings != previous:
        await _quiesce_provider(ref)
    invalidate(ref)
    return settings


async def working_directory(settings: ProviderSettings, access: str, machine, project_path: str = '') -> str:
    """Where a turn starts.

    Chat-only turns run in an empty folder. Otherwise the chat folder's project
    folder wins; without one, the plan's start folder (settings.workspace) or
    the computer's default workspace is used.
    """
    if access == ACCESS_CHAT:
        return await machine.chat_dir()
    if project_path:
        return project_path
    if settings.workspace:
        return settings.workspace
    return await machine.default_workspace()


# --- Project folders for chat folders ----------------------------------------------
#
# Each person chooses, per chat folder, where Claude and ChatGPT start on each
# of their computers. The choice itself lives in project_folders.py; these
# functions add what the service knows about computers and plans.


def _folder_computer_kind(machine_id: str, config: dict | None) -> str:
    if machine_id == LOCAL_MACHINE_ID:
        return 'server'
    if (config or {}).get('kind') == MACHINE_KIND_RUNNER:
        return 'app'
    return 'address'


async def _machine_platform(owner_id: str, machine_id: str) -> str | None:
    """The computer's sys.platform as it last reported it, without asking it again."""
    if machine_id == LOCAL_MACHINE_ID:
        return sys.platform
    config = next((machine for machine in await _owned_machine_configs(owner_id) if machine['id'] == machine_id), None)
    platform = ((config or {}).get('host') or {}).get('platform')
    if isinstance(platform, str) and platform:
        return platform
    return None


async def _owned_machine_ids(owner_id: str) -> set[str]:
    """The computers whose saved project folders are kept when a choice is saved.

    This server is always kept: the policy may turn it off only for a while,
    and turns check the policy themselves.
    """
    machine_ids = {config['id'] for config in await _owned_machine_configs(owner_id)}
    machine_ids.add(LOCAL_MACHINE_ID)
    return machine_ids


async def check_machine_folder(user, machine_id: str, path: str) -> str:
    """The tidy form of a folder that exists on one of the user's computers."""
    if not machine_id:
        raise SubscriptionError('Choose a computer.')
    machine = await get_machine(user.id, machine_id)
    cleaned = project_folders.clean_project_path(path, await _machine_platform(user.id, machine_id))
    if not await machine.path_exists(cleaned, 'dir'):
        raise SubscriptionError(f'{machine.name} has no folder at that path.')
    return cleaned


# What each computer last said about folder browsing: 'on', 'off' or 'unsupported'.
_folder_browsing: dict[tuple[str, str], str] = {}
# Folders Claude Code and Codex worked in, per person, computer and revision, for a minute.
FOLDER_SUGGESTIONS_CACHE_SECONDS = 60
_folder_suggestions: dict[tuple[str, str, str], tuple[float, list]] = {}


def _folder_browsing_refusal(error: RunnerCallError, key: tuple[str, str], machine_name: str) -> SubscriptionError:
    """Turns the Runner's refusal into what the person can do about it, and remembers it."""
    if error.status == 403:
        _folder_browsing[key] = 'off'
        return SubscriptionError(
            f'Turn on Folder suggestions in the Buddy app on {machine_name}. You can still type a path.'
        )
    if error.status == 404:
        _folder_browsing[key] = 'unsupported'
        return SubscriptionError(f'Update the Buddy app on {machine_name} to browse its folders. You can still type a path.')
    return error


async def list_machine_folders(user, machine_id: str, path: str, show_hidden: bool, prefix: str) -> dict:
    """Folder names in one folder of the person's computer, or its drives and places for an empty path."""
    if not machine_id:
        raise SubscriptionError('Choose a computer.')
    machine = await get_machine(user.id, machine_id)
    platform = await _machine_platform(user.id, machine_id)
    listed = ''
    if path:
        listed = folder_listing.clean_browse_path(path, platform)
    key = (user.id, machine_id)
    try:
        reply = await machine.list_folders(listed, show_hidden, (prefix or '')[:folder_listing.MAX_NAME_CHARS])
    except RunnerCallError as error:
        raise _folder_browsing_refusal(error, key, machine.name) from None
    _folder_browsing[key] = 'on'
    if not listed:
        return folder_listing.checked_roots(reply, platform)
    return folder_listing.checked_listing(reply, listed, platform)


async def suggested_machine_folders(user, machine_id: str) -> dict:
    """Folders Claude Code and Codex worked in on that computer: {state, folders}.

    state is 'ok', 'off' (not allowed in the Buddy app there), 'offline', or
    'unsupported' (no Buddy app, or an older one).
    """
    if not machine_id:
        raise SubscriptionError('Choose a computer.')
    machine = await get_machine(user.id, machine_id)
    config = next((saved for saved in await _owned_machine_configs(user.id) if saved['id'] == machine_id), None)
    if _folder_computer_kind(machine_id, config) != 'app':
        return {'state': 'unsupported', 'folders': []}
    if not HUB.is_online(machine_id):
        return {'state': 'offline', 'folders': []}
    revision = await _machine_revision(user.id, machine_id) or ''
    cache_key = (user.id, machine_id, revision)
    cached = _folder_suggestions.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < FOLDER_SUGGESTIONS_CACHE_SECONDS:
        return {'state': 'ok', 'folders': cached[1]}
    key = (user.id, machine_id)
    try:
        reply = await machine.suggest_folders()
    except RunnerCallError as error:
        _folder_browsing_refusal(error, key, machine.name)
        if error.status in (403, 404):
            return {'state': _folder_browsing[key], 'folders': []}
        raise
    _folder_browsing[key] = 'on'
    folders = folder_listing.checked_suggestions(reply, await _machine_platform(user.id, machine_id))
    _folder_suggestions[cache_key] = (time.monotonic(), folders)
    return {'state': 'ok', 'folders': folders}


async def _readable_chat_folder(user, folder_id: str):
    folder = await project_folders.readable_folder(user.id, folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='That folder no longer exists.')
    return folder


def _folder_browsing_state(owner_id: str, machine_id: str, kind: str, online: bool | None, config: dict | None) -> str:
    """Whether that computer's folders can be browsed: 'on', 'off', 'offline' or 'unsupported'.

    Only the Buddy app can list folders. Whether the person allowed it there is
    learned from its answer, since the app can change it at any time.
    """
    if kind != 'app':
        return 'unsupported'
    capabilities = (config or {}).get('capabilities') or {}
    if capabilities.get('folderBrowsing') is not True:
        return 'unsupported'
    if not online:
        return 'offline'
    if _folder_browsing.get((owner_id, machine_id)) == 'off':
        return 'off'
    return 'on'


async def _describe_folder_computer(owner_id: str, machine_id: str, config: dict | None, choices: dict) -> dict | None:
    kind = _folder_computer_kind(machine_id, config)
    online = None
    can_browse = False
    if kind == 'server':
        if not await _may_use_server_machine(owner_id):
            return None
        name = 'This server'
        online = True
    elif config is None:
        return None
    else:
        name = config['name']
    if kind == 'app':
        online = HUB.is_online(machine_id)
        capabilities = config.get('capabilities') or {}
        can_browse = online and capabilities.get('directories') is not False
    revision = await _machine_revision(owner_id, machine_id)
    return {
        'machine_id': machine_id,
        'name': name,
        'kind': kind,
        'online': online,
        'platform': await _machine_platform(owner_id, machine_id),
        'can_browse': can_browse,
        'folders': _folder_browsing_state(owner_id, machine_id, kind, online, config),
        'recent_paths': project_folders.recent_paths(choices, machine_id, revision),
    }


async def _folder_computers(user, choices: dict, named_machine_ids: set[str]) -> list[dict]:
    """Every computer the person's plans can run on, and those already named in this folder's choice.

    Each lists the plans that are on, with the access new chats start with.
    """
    plans = []
    for provider_id in PROVIDER_IDS:
        settings = await _load_settings(ProviderRef(user.id, provider_id))
        if settings.enable:
            plans.append({'id': provider_id, 'name': PROVIDER_LABELS[provider_id], 'access': settings.access})
    configs = {config['id']: config for config in await _owned_machine_configs(user.id)}
    machine_ids = []
    if await _may_use_server_machine(user.id):
        machine_ids.append(LOCAL_MACHINE_ID)
    machine_ids.extend(configs)
    for machine_id in sorted(named_machine_ids):
        if machine_id not in machine_ids:
            machine_ids.append(machine_id)
    computers = []
    for machine_id in machine_ids:
        computer = await _describe_folder_computer(user.id, machine_id, configs.get(machine_id), choices)
        if computer is None:
            continue
        computer['providers'] = list(plans)
        computers.append(computer)
    return computers


def _describe_saved_path(saved, revision: str) -> dict:
    problem = None
    if saved.revision != revision:
        problem = 'computer_changed'
    return {'path': saved.path, 'problem': problem}


def _describe_folder_choice(choice, revisions: dict[str, str]) -> dict | None:
    if choice is None:
        return None
    paths = {}
    for machine_id, saved in choice.paths.items():
        # A computer that was removed has no revision, and is left out.
        if machine_id in revisions:
            paths[machine_id] = _describe_saved_path(saved, revisions[machine_id])
    offered = {}
    for machine_id, entries in choice.offered.items():
        if machine_id in revisions:
            offered[machine_id] = [_describe_saved_path(saved, revisions[machine_id]) for saved in entries]
    return {
        'folder_id': choice.folder_id,
        'folder_name': choice.folder_name,
        'mode': choice.mode,
        'paths': paths,
        # What new chats in the folder start with; '' follows the usual default.
        'machine_id': choice.machine_id,
        'access': choice.access,
        'offered': offered,
    }


async def describe_folder_project(user, folder_id: str) -> dict:
    """This person's project folder for a chat folder, and the computers it can be on. Contacts no computer."""
    folder = await _readable_chat_folder(user, folder_id)
    choices = await project_folders.load_choices(user.id)
    own, inherited = await project_folders.resolve_folder(user.id, folder, choices)
    # Instructions someone else can change keep chats chat-only until the person chooses here.
    shared_instructions = await project_folders.has_shared_instructions(user.id, folder)
    # Instructions added in the folder dialog would be shared too.
    editable_by_others = await project_folders.others_can_edit(user.id, folder)
    return await _describe_project(
        user, folder.id, folder.name, own, inherited, shared_instructions, editable_by_others, choices
    )


async def describe_new_folder_project(user, parent_id: str | None) -> dict:
    """What a chat folder about to be created starts with: its parent's choice, if any,
    and the computers it can be on. Contacts no computer."""
    choices = await project_folders.load_choices(user.id)
    inherited = None
    # A new top-level folder is this person's alone until they share it.
    editable_by_others = False
    if parent_id:
        parent = await _readable_chat_folder(user, parent_id)
        parent_own, parent_inherited = await project_folders.resolve_folder(user.id, parent, choices)
        inherited = parent_own or parent_inherited
        # Whoever may edit the parent may edit a folder created in it.
        editable_by_others = await project_folders.others_can_edit(user.id, parent, levels_below=1)
    # A new folder has no instructions of its own yet.
    return await _describe_project(user, '', '', None, inherited, False, editable_by_others, choices)


async def _describe_project(user, folder_id: str, folder_name: str, own, inherited, shared_instructions: bool,
                            editable_by_others: bool, choices: dict) -> dict:
    named_machine_ids = set()
    for choice in (own, inherited):
        if choice is not None:
            named_machine_ids.update(choice.paths)
            named_machine_ids.update(choice.offered)
    revisions = {}
    for machine_id in named_machine_ids:
        revision = await _machine_revision(user.id, machine_id)
        if revision is not None:
            revisions[machine_id] = revision
    has_plans = False
    for provider_id in PROVIDER_IDS:
        if (await _load_settings(ProviderRef(user.id, provider_id))).enable:
            has_plans = True
    return {
        'folder_id': folder_id,
        'folder_name': folder_name,
        'own': _describe_folder_choice(own, revisions),
        'inherited': _describe_folder_choice(inherited, revisions),
        'shared_instructions': shared_instructions,
        'others_can_edit': editable_by_others,
        'has_plans': has_plans,
        'computers': await _folder_computers(user, choices, named_machine_ids),
    }


async def _checked_project_path(user, machine_id: str, path, current) -> 'project_folders.SavedPath':
    """A path to save, checked on its computer unless it is the same as the one already saved there."""
    previous = None
    if current is not None:
        previous = current.paths.get(machine_id)
    return await _checked_saved_path(user, machine_id, path, previous)


async def _checked_offered_paths(user, offered, current) -> dict[str, list['project_folders.SavedPath']]:
    """More folders to offer new chats on each computer, each checked there unless already saved."""
    if not isinstance(offered, dict):
        raise SubscriptionError('Choose the folders to offer on each computer.')
    checked = {}
    for machine_id, paths in offered.items():
        if not isinstance(paths, list):
            raise SubscriptionError('Choose the folders to offer on each computer.')
        if len(paths) > project_folders.MAX_OFFERED_PER_COMPUTER:
            raise SubscriptionError(
                f'Offer up to {project_folders.MAX_OFFERED_PER_COMPUTER} folders on each computer.'
            )
        already_saved = {}
        if current is not None:
            for saved in current.offered.get(machine_id, []):
                already_saved[saved.path] = saved
        entries = []
        for path in paths:
            entries.append(await _checked_saved_path(user, machine_id, path, already_saved.get(path)))
        checked[machine_id] = entries
    return checked


async def _checked_saved_path(user, machine_id: str, path, previous) -> 'project_folders.SavedPath':
    if not isinstance(path, str):
        raise SubscriptionError("Enter the folder's full path.")
    revision = await _machine_revision(user.id, machine_id)
    if previous is not None and previous.path == path and previous.revision == revision:
        # Unchanged, so it is kept even while the policy has This server off for a
        # while; turns still check the policy before using it.
        return previous
    # Confirms the computer is the user's and, for This server, that the policy allows it.
    await get_machine(user.id, machine_id)
    if revision is None:
        raise SubscriptionError(REMOVED_COMPUTER_MESSAGE)
    cleaned = project_folders.clean_project_path(path, await _machine_platform(user.id, machine_id))
    if previous is not None and previous.path == cleaned and previous.revision == revision:
        # Checked when it was chosen, so it saves even while that computer is offline.
        return previous
    cleaned = await check_machine_folder(user, machine_id, cleaned)
    return project_folders.SavedPath(cleaned, revision)


async def save_folder_project(
    user, folder_id: str, mode: str, paths: dict, machine_id: str = project_folders.FOLLOW_DEFAULT,
    access: str = project_folders.FOLLOW_DEFAULT, offered: dict | None = None
) -> dict:
    """Replace this person's project folder choice for a chat folder, with what new chats in it start with."""
    _require_single_worker()
    folder = await _readable_chat_folder(user, folder_id)
    if mode not in project_folders.MODES:
        raise SubscriptionError('Choose how chats in this folder work.')
    current, _ = await project_folders.resolve_folder(user.id, folder)
    saved_paths = {}
    if mode == project_folders.MODE_FOLDER:
        for path_machine_id, path in (paths or {}).items():
            saved_paths[path_machine_id] = await _checked_project_path(user, path_machine_id, path, current)
    saved_offered = {}
    if mode != project_folders.MODE_CHAT:
        saved_offered = await _checked_offered_paths(user, offered or {}, current)
    await project_folders.save_choice(
        user.id,
        folder.id,
        mode,
        saved_paths,
        await _owned_machine_ids(user.id),
        machine_id=machine_id,
        access=access,
        offered=saved_offered,
    )
    return await describe_folder_project(user, folder.id)


async def clear_folder_project(user, folder_id: str) -> dict:
    """Forget this person's choice for a chat folder, so it follows its parent again."""
    _require_single_worker()
    folder = await _readable_chat_folder(user, folder_id)
    await project_folders.clear_choice(user.id, folder.id, await _owned_machine_ids(user.id))
    return await describe_folder_project(user, folder.id)


async def _sign_in_settings(
    ref: ProviderRef, settings: ProviderSettings, machine_id: str | None, expected_machine_id: str | None,
    expected_machine_revision: str | None, mutation: bool
) -> ProviderSettings:
    """The plan's settings on the computer a sign-in action is for.

    Without a chosen computer it is the plan's own, as the page last showed
    it. A chosen computer must be one of the user's, at the revision the page
    showed; an unknown computer and someone else's get the same answer.
    """
    if machine_id is None:
        await _check_expected_machine(ref.owner_id, settings, expected_machine_id, expected_machine_revision, mutation)
        return await _settings_for(ref, settings, settings.machine_id)
    if not machine_id:
        raise SubscriptionError(NO_MACHINE_MESSAGE)
    on_computer = await _settings_for(ref, settings, machine_id)
    await _check_expected_machine(ref.owner_id, on_computer, machine_id, expected_machine_revision, mutation)
    return on_computer


async def _computer_name(owner_id: str, machine_id: str) -> str:
    try:
        return (await get_machine(owner_id, machine_id)).name
    except SubscriptionError:
        return 'another computer'


async def _computer_still_usable(owner_id: str, machine_id: str) -> bool:
    if machine_id == LOCAL_MACHINE_ID:
        return await _may_use_server_machine(owner_id)
    return await _machine_revision(owner_id, machine_id) is not None


async def _require_login_on(ref: ProviderRef, machine_id: str) -> None:
    """A code or cancel for one computer never reaches a sign-in still waiting on another."""
    running_on = _login_computers.get(ref)
    if running_on is None or running_on == machine_id:
        return
    provider = await get_provider(ref)
    if provider.login_state().get('state') != 'waiting':
        return
    if not await _computer_still_usable(ref.owner_id, running_on):
        # Its computer was removed or turned off, so nothing can finish it.
        await provider.cancel_login()
        _login_computers.pop(ref, None)
        return
    name = await _computer_name(ref.owner_id, running_on)
    raise HTTPException(status_code=409, detail=f'Finish signing in on {name} first.')


async def _stop_for_sign_in(ref: ProviderRef, settings: ProviderSettings, machine_id: str) -> None:
    """Before signing in or out on a computer, stop the work the change affects.

    On the plan's own computer that is the whole plan, as before. On another
    computer it is only that computer's work. A sign-in still waiting
    elsewhere is cancelled too: a provider signs in on one computer at a time.
    """
    if machine_id == settings.machine_id:
        await _quiesce_provider(ref)
        return
    await _quiesce_computer(ref, machine_id)
    if ref in _login_computers:
        await (await get_provider(ref)).cancel_login()
        _login_computers.pop(ref, None)


# --- Where chats run -----------------------------------------------------------------
#
# Each chat runs on one of the person's computers, in an exact folder or where
# the AI chooses, or Detached. The choice is made once, when the chat starts,
# and saved in chat_run_target; run_targets.py holds the rules.

API_KEY_TARGET_MESSAGE = "Sign in to Buddy to choose where a chat runs; API keys can't."
# A temporary chat has no saved target; its turns carry the bar's choice under this metadata key.
REQUESTED_RUN_TARGET_KEY = 'requested_run_target'
# When Buddy first ran with chat targets. Chats created before it keep running where their plan ran.
RUN_TARGETS_STARTED_CONFIG_KEY = 'subscriptions.run_targets_started_at'

_run_targets_started_at: int | None = None


async def mark_run_targets_started() -> int:
    """When chats started having targets: saved the first time Buddy starts with them, then kept."""
    global _run_targets_started_at
    if _run_targets_started_at is not None:
        return _run_targets_started_at
    stored = await Config.get(RUN_TARGETS_STARTED_CONFIG_KEY)
    if isinstance(stored, int) and not isinstance(stored, bool) and stored > 0:
        _run_targets_started_at = stored
        return stored
    started_at = int(time.time())
    await Config.upsert({RUN_TARGETS_STARTED_CONFIG_KEY: started_at})
    _run_targets_started_at = started_at
    return started_at


async def _started_before_targets(user, chat_id: str) -> bool:
    """Whether the person's chat is older than chat targets, so its first turn binds where its plan ran.

    Chats Buddy creates without the bar, such as automations, are newer and get
    the default like any new chat.
    """
    created_at = await Chats.get_chat_created_at(chat_id, user.id)
    if created_at is None:
        return False
    return created_at < await mark_run_targets_started()


@dataclass(frozen=True)
class _ChatFolderDefaults:
    choice: 'run_targets.DefaultFolderChoice | None'
    folder: Any
    # The folder's instructions can be changed by someone else, and the person has no choice of their own.
    shared_instructions: bool
    # More folders new chats here can pick from, per computer.
    offered: dict = field(default_factory=dict)


async def _current_paths(owner_id: str, saved_paths: dict) -> dict:
    """Saved paths whose computer has not been set up again since they were chosen."""
    current = {}
    for machine_id, saved in saved_paths.items():
        if saved.revision == await _machine_revision(owner_id, machine_id):
            current[machine_id] = saved.path
    return current


async def _chat_folder_defaults(user, folder_id) -> _ChatFolderDefaults:
    folder = await project_folders.readable_folder(user.id, folder_id)
    if folder is None:
        return _ChatFolderDefaults(None, None, False)
    own, inherited = await project_folders.resolve_folder(user.id, folder)
    shared_instructions = own is None and await project_folders.has_shared_instructions(user.id, folder)
    choice = own or inherited
    if choice is None:
        return _ChatFolderDefaults(None, folder, shared_instructions)
    paths = await _current_paths(user.id, choice.paths)
    offered = {}
    for machine_id, entries in choice.offered.items():
        revision = await _machine_revision(user.id, machine_id)
        current = [saved.path for saved in entries if saved.revision == revision]
        if current:
            offered[machine_id] = current
    defaults = run_targets.DefaultFolderChoice(choice.mode, choice.machine_id, choice.access, paths)
    return _ChatFolderDefaults(defaults, folder, shared_instructions, offered)


async def _ready_on(owner_id: str, machine_id: str) -> bool:
    """A plan last reported being signed in on the computer."""
    for provider_id in PROVIDER_IDS:
        if uses_plan(status_cache.cached(_ref(owner_id, provider_id).on(machine_id)) or {}):
            return True
    return False


async def _default_computers(owner_id: str) -> tuple['run_targets.DefaultComputer', ...]:
    computers = []
    if await _may_use_server_machine(owner_id):
        computers.append(run_targets.DefaultComputer(LOCAL_MACHINE_ID, await _ready_on(owner_id, LOCAL_MACHINE_ID)))
    for config in await _owned_machine_configs(owner_id):
        ready = await _ready_on(owner_id, config['id'])
        if config.get('kind') == MACHINE_KIND_RUNNER and not HUB.is_online(config['id']):
            ready = False
        computers.append(run_targets.DefaultComputer(config['id'], ready))
    return tuple(computers)


async def _plan_machine_id(owner_id: str, provider_id: str | None) -> str | None:
    """The computer a plan ran on before chats chose their own: the given plan's, or an enabled one's."""
    provider_ids = PROVIDER_IDS
    if provider_id is not None:
        provider_ids = (provider_id,)
    for candidate in provider_ids:
        settings = await _load_settings(_ref(owner_id, candidate))
        if settings.machine_id and (settings.enable or provider_id is not None):
            return settings.machine_id
    return None


async def _plan_access(owner_id: str, provider_id: str | None) -> str:
    """The access new chats start with: the given plan's, or the lowest of the enabled plans'."""
    if provider_id is not None:
        return (await _load_settings(_ref(owner_id, provider_id))).access
    access = None
    for candidate in PROVIDER_IDS:
        settings = await _load_settings(_ref(owner_id, candidate))
        if not settings.enable:
            continue
        if access is None:
            access = settings.access
        else:
            access = run_targets.lowest_access(access, settings.access)
    return access or ACCESS_CHAT


async def default_run_target(
    user, folder_id, this_machine_id: str | None = None, provider_id: str | None = None,
    started_before_targets: bool = False
) -> 'run_targets.RunTargetDefault':
    """Where a chat in this chat folder runs when nobody chose. Contacts no computer."""
    folder_defaults = await _chat_folder_defaults(user, folder_id)
    last_used = await ChatRunTargets.last_for_user(user.id)
    last_used_machine_id = None
    if last_used is not None:
        last_used_machine_id = last_used.machine_id
    inputs = run_targets.DefaultInputs(
        folder_choice=folder_defaults.choice,
        shared_instructions=folder_defaults.shared_instructions,
        computers=await _default_computers(user.id),
        this_machine_id=this_machine_id,
        last_used_machine_id=last_used_machine_id,
        plan_machine_id=await _plan_machine_id(user.id, provider_id),
    )
    return run_targets.choose_default(inputs, started_before_targets)


def _detached_row(user, chat_id: str, source: str, note: str | None = None) -> ChatRunTargetModel:
    return ChatRunTargetModel(
        chat_id=chat_id,
        user_id=user.id,
        kind=run_targets.KIND_DETACHED,
        folder_mode='none',
        access=ACCESS_CHAT,
        source=source,
        note=note,
        created_at=int(time.time()),
    )


async def _row_from_default(user, chat_id: str, default, provider_id: str | None) -> ChatRunTargetModel:
    if default.kind == run_targets.KIND_DETACHED:
        return _detached_row(user, chat_id, SOURCE_DEFAULT)
    access = default.access
    if access is None:
        access = await _plan_access(user.id, provider_id)
    return ChatRunTargetModel(
        chat_id=chat_id,
        user_id=user.id,
        kind=run_targets.KIND_COMPUTER,
        machine_id=default.machine_id,
        machine_revision=await _machine_revision(user.id, default.machine_id),
        folder_mode=default.folder_mode,
        path=default.path,
        access=access,
        source=SOURCE_DEFAULT,
        # Where the default came from, such as the chat folder or the plan's own computer.
        note=default.came_from,
        created_at=int(time.time()),
    )


class _RefusedTarget(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def _checked_computer(user, machine_id: str, expected_revision: str | None) -> str:
    """The computer's current revision. Unknown computers and other people's are refused alike."""
    if machine_id == LOCAL_MACHINE_ID:
        if not await _may_use_server_machine(user.id):
            raise _RefusedTarget(run_targets.REFUSED_SERVER_OFF)
    elif machine_id not in {config['id'] for config in await _owned_machine_configs(user.id)}:
        raise _RefusedTarget(run_targets.REFUSED_COMPUTER_REMOVED)
    revision = await _machine_revision(user.id, machine_id)
    if revision is None:
        raise _RefusedTarget(run_targets.REFUSED_COMPUTER_REMOVED)
    if expected_revision is not None and expected_revision != revision:
        raise _RefusedTarget(run_targets.REFUSED_COMPUTER_RESET)
    return revision


async def _row_from_request(user, chat_id: str, raw, folder_id) -> ChatRunTargetModel:
    """The bar's choice, checked without contacting any computer, so it works while one is offline."""
    request = run_targets.parse_request(raw)
    if request.kind == run_targets.KIND_DETACHED:
        return _detached_row(user, chat_id, SOURCE_BAR)
    revision = await _checked_computer(user, request.machine_id, request.expected_revision)
    path = None
    if request.folder.mode == run_targets.FOLDER_PATH:
        platform = await _machine_platform(user.id, request.machine_id)
        try:
            path = project_folders.clean_project_path(request.folder.path, platform)
        except SubscriptionError:
            raise _RefusedTarget(run_targets.REFUSED_BAD_PATH)
    acknowledged = None
    if request.access != ACCESS_CHAT:
        folder_defaults = await _chat_folder_defaults(user, folder_id)
        if folder_defaults.shared_instructions:
            if request.acknowledged_instructions_folder_id != folder_defaults.folder.id:
                raise _RefusedTarget(run_targets.REFUSED_NEEDS_ACKNOWLEDGEMENT)
            acknowledged = folder_defaults.folder.id
    return ChatRunTargetModel(
        chat_id=chat_id,
        user_id=user.id,
        kind=run_targets.KIND_COMPUTER,
        machine_id=request.machine_id,
        machine_revision=revision,
        folder_mode=request.folder.mode,
        path=path,
        access=request.access,
        acknowledged_instructions_folder_id=acknowledged,
        source=SOURCE_BAR,
        created_at=int(time.time()),
    )


async def _target_row(
    user, chat_id: str, raw, folder_id, provider_id: str | None, started_before_targets: bool = False
) -> ChatRunTargetModel:
    """The target a chat starts with: the bar's choice, checked, or the default when nobody chose."""
    if raw is None:
        default = await default_run_target(
            user, folder_id, provider_id=provider_id, started_before_targets=started_before_targets
        )
        return await _row_from_default(user, chat_id, default, provider_id)
    try:
        return await _row_from_request(user, chat_id, raw, folder_id)
    except _RefusedTarget as refused:
        return _detached_row(user, chat_id, SOURCE_REFUSED, refused.code)
    except SubscriptionError:
        return _detached_row(user, chat_id, SOURCE_REFUSED, run_targets.REFUSED_BAD_REQUEST)


async def bind_chat_target(
    user, chat_id: str, raw=None, provider_id: str | None = None, started_before_targets: bool = False
):
    """Save where a new chat runs: the bar's choice, checked, or the default when nobody chose.

    A chat that already has a target keeps it. A choice that can't be used
    makes the chat Detached and chat only, never anything more than asked.
    Callers refuse a choice sent with an API key before creating the chat.
    """
    if not is_saved_chat_id(chat_id) or not await may_use_machines(user):
        return None
    if not await Chats.is_chat_owner(chat_id, user.id):
        return None
    existing = await ChatRunTargets.get(chat_id)
    if existing is not None:
        return existing
    folder_id = await Chats.get_chat_folder_id(chat_id, user.id)
    row = await _target_row(user, chat_id, raw, folder_id, provider_id, started_before_targets)
    return await ChatRunTargets.insert_if_absent(row)


async def copy_chat_target(user, source_chat_id: str, new_chat_id: str):
    """A fork or clone of the person's own chat runs where that chat runs, or where its next turn would bind it.

    A copy of a chat from before targets is newer than targets itself, so it is
    bound now, the way its source would be. Otherwise an unbound copy gets the
    default on its first turn, like its source.
    """
    copied = await ChatRunTargets.copy_for_owner(source_chat_id, new_chat_id, user.id)
    if copied is not None:
        return copied
    if not await Chats.is_chat_owner(source_chat_id, user.id):
        return None
    if not await _started_before_targets(user, source_chat_id):
        return None
    provider_id = await _chat_provider(user, source_chat_id)
    return await bind_chat_target(user, new_chat_id, None, provider_id, started_before_targets=True)


async def bind_imported_chat(user, chat_id: str):
    """An imported chat keeps its old date but starts like a new chat.

    Its row is saved even while its owner may not use computers, so its date
    never makes it look like a chat from before targets later.
    """
    row = await bind_chat_target(user, chat_id)
    if row is not None:
        return row
    if not is_saved_chat_id(chat_id) or not await Chats.is_chat_owner(chat_id, user.id):
        return None
    return await ChatRunTargets.insert_if_absent(_detached_row(user, chat_id, SOURCE_DEFAULT))


def _provider_for_model_id(model_id) -> str | None:
    """The plan behind one of Buddy's own plan model ids, such as 'claude-code.opus'."""
    if not isinstance(model_id, str):
        return None
    for provider_id, prefix in MODEL_ID_PREFIXES.items():
        if model_id.startswith(f'{prefix}.'):
            return provider_id
    return None


async def _chat_provider(user, chat_id: str) -> str | None:
    """The plan of the chat's first model, when it is one of Buddy's own plan models."""
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if chat is None or not isinstance(chat.chat, dict):
        return None
    models = chat.chat.get('models')
    if not isinstance(models, list) or not models:
        return None
    return _provider_for_model_id(models[0])


def _bar_plan_state(provider_status: dict | None, online: bool | None) -> str:
    """A plan's state on one computer for the composer's bar, from what it last reported."""
    if online is False:
        return 'offline'
    state = _sign_in_state(provider_status)
    if state == 'not_checked':
        return 'unknown'
    if state == 'signed_out':
        return 'sign_in'
    return state


def _describe_default(default) -> dict:
    folder = None
    if default.folder_mode == run_targets.FOLDER_PATH:
        folder = {'mode': run_targets.FOLDER_PATH, 'path': default.path}
    elif default.folder_mode == run_targets.FOLDER_AUTO:
        folder = {'mode': run_targets.FOLDER_AUTO}
    return {
        'kind': default.kind,
        'machine_id': default.machine_id,
        'folder': folder,
        # None means each plan's own access.
        'access': default.access,
        'from': default.came_from,
        'access_locked': default.access_locked,
    }


async def run_target_options(user, folder_id=None, this_machine_id: str | None = None) -> dict:
    """What the composer's bar offers for a new chat. Contacts no computer.

    The person's computers with each plan's last known state there, recent
    folders, the chat folder's defaults, and where the chat runs if nothing
    is changed.
    """
    if not await may_use_machines(user):
        return {'allowed': False, 'has_plans': False}
    plan_access = {}
    for provider_id in PROVIDER_IDS:
        settings = await _load_settings(_ref(user.id, provider_id))
        if settings.enable:
            plan_access[provider_id] = settings.access
    configs = {config['id']: config for config in await _owned_machine_configs(user.id)}
    machine_ids = []
    if await _may_use_server_machine(user.id):
        machine_ids.append(LOCAL_MACHINE_ID)
    machine_ids.extend(configs)
    choices = await project_folders.load_choices(user.id)
    computers = []
    revisions = {}
    for machine_id in machine_ids:
        computer = await _describe_folder_computer(user.id, machine_id, configs.get(machine_id), choices)
        if computer is None:
            continue
        revisions[machine_id] = await _machine_revision(user.id, machine_id)
        computer['revision'] = revisions[machine_id]
        providers = {}
        for provider_id in plan_access:
            provider_status = status_cache.cached(_ref(user.id, provider_id).on(machine_id))
            providers[provider_id] = {
                'state': _bar_plan_state(provider_status, computer['online']),
                'message': (provider_status or {}).get('message'),
            }
        computer['providers'] = providers
        computers.append(computer)

    recent = []
    for row in await ChatRunTargets.recent_paths(user.id):
        # Only folders on computers still the person's, chosen since they were last set up.
        if row.machine_id in revisions and row.machine_revision == revisions[row.machine_id]:
            recent.append({'machine_id': row.machine_id, 'path': row.path, 'last_used': row.created_at})

    folder_defaults = await _chat_folder_defaults(user, folder_id)
    chat_folder = None
    if folder_defaults.folder is not None:
        chat_folder = {
            'id': folder_defaults.folder.id,
            'name': folder_defaults.folder.name,
            'mode': None,
            'paths': {},
            'offered': folder_defaults.offered,
            'shared_instructions': folder_defaults.shared_instructions,
        }
        if folder_defaults.choice is not None:
            chat_folder['mode'] = folder_defaults.choice.mode
            chat_folder['paths'] = folder_defaults.choice.paths

    if this_machine_id not in revisions:
        this_machine_id = None
    default = await default_run_target(user, folder_id, this_machine_id=this_machine_id)
    return {
        'allowed': True,
        'has_plans': bool(plan_access),
        'plan_access': plan_access,
        'computers': computers,
        'recent': recent,
        'chat_folder': chat_folder,
        'default': _describe_default(default),
    }


async def run_target_readiness(user, provider_id: str, machine_id: str) -> dict:
    """A fresh check of one plan on one of the person's computers, for the bar's choice: {state, message}.

    It starts the CLI there, as the chat would.
    """
    check_provider_id(provider_id)
    if not await may_use_machines(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_ALLOWED_MESSAGE)
    ref = _ref(user.id, provider_id)
    settings = await _settings_for(ref, await _load_settings(ref), machine_id)
    try:
        await _require_verified_machine(ref, settings)
    except SubscriptionError as error:
        # An unknown computer and someone else's get the same answer.
        return {'state': 'not_allowed', 'message': str(error)}
    config = next((saved for saved in await _owned_machine_configs(user.id) if saved['id'] == machine_id), None)
    if _folder_computer_kind(machine_id, config) == 'app' and not HUB.is_online(machine_id):
        return {'state': 'offline', 'message': f'{config["name"]} is offline. Open the Buddy app on it.'}
    provider_status = await status_cache.get(ref.on(machine_id), fresh=True)
    if provider_status is None:
        # Still answering; the bar asks again.
        return {'state': 'checking', 'message': None}
    return {'state': _bar_plan_state(provider_status, True), 'message': provider_status.get('message')}


async def _target_problem(user, row, kind: str, online: bool | None) -> str | None:
    """Why a chat's computer can't run its turns now, if it can't."""
    if row.machine_id == LOCAL_MACHINE_ID and not await _may_use_server_machine(user.id):
        return 'server_off'
    revision = await _machine_revision(user.id, row.machine_id)
    if revision is None:
        return 'removed'
    if row.machine_revision and revision != row.machine_revision:
        return 'reset'
    if kind == 'app' and not online:
        return 'offline'
    return None


async def _describe_target_row(user, row) -> dict:
    described = {
        'kind': row.kind,
        'machine_id': row.machine_id,
        'computer': None,
        'folder_mode': row.folder_mode,
        'path': row.path,
        'access': row.access,
        'source': row.source,
        'note': row.note,
        'problem': None,
    }
    if row.source == SOURCE_REFUSED:
        described['note_message'] = run_targets.REFUSAL_MESSAGES.get(row.note)
    if row.kind != run_targets.KIND_COMPUTER:
        return described
    config = next((saved for saved in await _owned_machine_configs(user.id) if saved['id'] == row.machine_id), None)
    kind = _folder_computer_kind(row.machine_id, config)
    online = None
    name = 'Removed computer'
    if kind == 'server':
        name = 'This server'
        online = True
    elif config is not None:
        name = config['name']
    if kind == 'app' and config is not None:
        online = HUB.is_online(row.machine_id)
    described['computer'] = {'name': name, 'kind': kind, 'online': online}
    described['problem'] = await _target_problem(user, row, kind, online)
    return described


async def describe_chat_run_target(user, chat_id: str) -> dict:
    """Where the person's chat runs, or where it will when its next turn binds it. Only its owner sees it."""
    if not is_saved_chat_id(chat_id) or not await Chats.is_chat_owner(chat_id, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='That chat no longer exists.')
    row = await ChatRunTargets.get(chat_id)
    if row is not None:
        return {'bound': True, **await _describe_target_row(user, row)}
    # Not bound yet: its next turn binds where its plan ran if it is older than targets, else the default.
    folder_id = await Chats.get_chat_folder_id(chat_id, user.id)
    started_before_targets = await _started_before_targets(user, chat_id)
    provider_id = await _chat_provider(user, chat_id)
    default = await default_run_target(
        user, folder_id, provider_id=provider_id, started_before_targets=started_before_targets
    )
    would_bind = await _row_from_default(user, chat_id, default, provider_id)
    return {'bound': False, 'would_bind': await _describe_target_row(user, would_bind)}


# Reading agent files never waits longer than this on a slow disk.
AGENT_READ_TIMEOUT_SECONDS = 15


async def _chose_server_path(user, path: str) -> bool:
    """Whether the person already chose this folder on This server, for a chat folder or a chat."""
    for choice in (await project_folders.load_choices(user.id)).values():
        saved = choice.paths.get(LOCAL_MACHINE_ID)
        if saved is not None and saved.path == path:
            return True
    return await ChatRunTargets.has_path(user.id, LOCAL_MACHINE_ID, path)


async def server_agent_files(user, machine_id: str, path) -> dict:
    """The agent files in a folder on This server, for the composer's agent menu.

    The folder must be one the person already chose there: a chat folder's project
    folder, or the exact folder one of their chats runs in. Agents on Buddy app
    computers are read through the relay instead. Only someone who may use This
    server, which the policy limits to its owner, reads it, and only the agent
    folders inside it are read.
    """
    if machine_id != LOCAL_MACHINE_ID:
        raise SubscriptionError('Agents on that computer are read through the Buddy app.')
    if not await _may_use_server_machine(user.id):
        raise SubscriptionError('This server is not available to you.')
    if not isinstance(path, str):
        raise SubscriptionError("Enter the folder's full path.")
    cleaned = project_folders.clean_project_path(path, await _machine_platform(user.id, LOCAL_MACHINE_ID))
    if not await _chose_server_path(user, cleaned):
        raise SubscriptionError('Choose this folder for the chat or its chat folder first, then pick an agent.')
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(project_agents.read_agent_files, cleaned), AGENT_READ_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        raise SubscriptionError('That folder no longer exists on This server.')
    except asyncio.TimeoutError:
        raise SubscriptionError('This server took too long to read the agent files. Try again.')
    except OSError:
        raise SubscriptionError('This server could not read that folder.')


async def start_login(
    owner_id: str, provider_id: str, method: str, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None, machine_id: str | None = None
) -> dict:
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    async with _provider_lock(ref):
        settings = await _load_settings(ref)
        on_computer = await _sign_in_settings(
            ref, settings, machine_id, expected_machine_id, expected_machine_revision, mutation=True
        )
        await _stop_for_sign_in(ref, settings, on_computer.machine_id)
        machine = await _tracked(ref, on_computer)
        login = await (await get_provider(ref)).start_login(on_computer, machine, method)
        _login_computers[ref] = on_computer.machine_id
        return login


async def login_state(
    owner_id: str, provider_id: str, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None, machine_id: str | None = None
) -> dict:
    ref = _ref(owner_id, provider_id)
    async with _provider_lock(ref):
        settings = await _load_settings(ref)
        on_computer = await _sign_in_settings(
            ref, settings, machine_id, expected_machine_id, expected_machine_revision, mutation=False
        )
        if UVICORN_WORKERS != 1:
            return {'state': 'idle', 'message': SINGLE_WORKER_MESSAGE}
        try:
            await _require_verified_machine(ref, on_computer)
            _require_computer_cleanup(ref, on_computer.machine_id)
        except SubscriptionError as error:
            return {'state': 'idle', 'message': str(error)}
        running_on = _login_computers.get(ref)
        if running_on is not None and running_on != on_computer.machine_id:
            return {'state': 'idle'}
        login = (await get_provider(ref)).login_state()
        if login.get('state') == 'success':
            invalidate(ref)
        return login


async def submit_login_code(
    owner_id: str, provider_id: str, code: str, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None, machine_id: str | None = None
) -> dict:
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    async with _provider_lock(ref):
        settings = await _load_settings(ref)
        on_computer = await _sign_in_settings(
            ref, settings, machine_id, expected_machine_id, expected_machine_revision, mutation=True
        )
        await _require_login_on(ref, on_computer.machine_id)
        # A code never finishes a sign-in on a computer the person may no longer use.
        await _require_verified_machine(ref, on_computer)
        _require_computer_cleanup(ref, on_computer.machine_id)
        login = await (await get_provider(ref)).submit_login_code(code)
        if login.get('state') == 'success':
            invalidate(ref)
        return login


async def cancel_login(
    owner_id: str, provider_id: str, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None, machine_id: str | None = None
) -> dict:
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    async with _provider_lock(ref):
        settings = await _load_settings(ref)
        on_computer = await _sign_in_settings(
            ref, settings, machine_id, expected_machine_id, expected_machine_revision, mutation=True
        )
        await _require_login_on(ref, on_computer.machine_id)
        if on_computer.machine_id == settings.machine_id:
            await _quiesce_provider(ref)
        else:
            # Only the sign-in stops; chats on that computer keep running.
            await (await get_provider(ref)).cancel_login()
            _login_computers.pop(ref, None)
        return {'state': 'idle'}


async def logout(
    owner_id: str, provider_id: str, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None, machine_id: str | None = None
) -> None:
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    async with _provider_lock(ref):
        settings = await _load_settings(ref)
        on_computer = await _sign_in_settings(
            ref, settings, machine_id, expected_machine_id, expected_machine_revision, mutation=True
        )
        credential_mode = await _load_credential_mode(ref)
        if credential_mode != CREDENTIAL_MACHINE:
            # The token or key is Buddy's to forget, and every computer uses
            # it, so the whole plan stops. The computer's own CLI sign-in is
            # not touched.
            await _quiesce_provider(ref)
            await SubscriptionCredentials.delete(owner_id, provider_id, credential_mode)
            await Config.upsert({await _credential_mode_key(ref): CREDENTIAL_MACHINE})
            invalidate(ref)
            return
        await _stop_for_sign_in(ref, settings, on_computer.machine_id)
        machine = await _tracked(ref, on_computer)
        try:
            await (await get_provider(ref)).logout(on_computer, machine)
        finally:
            invalidate(ref)


async def _tracked_turn(ref: ProviderRef, provider, turn: TurnRequest, generation: int | None = None):
    if generation is None:
        generation = _provider_generations.get(ref, 0)
    task = asyncio.current_task()
    async with _provider_lock(ref):
        error = None
        if generation != _provider_generations.get(ref, 0):
            error = 'The provider machine changed; refresh before starting another turn.'
        else:
            try:
                _require_single_worker()
                await _require_verified_machine(ref, turn.settings)
                _require_computer_cleanup(ref, turn.settings.machine_id)
            except SubscriptionError as failure:
                error = str(failure)
            if error is None:
                _track_task(ref, turn.settings.machine_id, task)
    if error is not None:
        async for event in failure_events(error):
            yield event
        return
    try:
        async for event in provider.run_turn(turn):
            yield event
    finally:
        _untrack_task(ref, task)


async def forget_user(user_id: str) -> None:
    """A deleted user's providers stop, and their machines and settings are removed."""
    async with _machine_lock, _ProviderLocks(user_id):
        refs = _owner_refs(user_id)
        try:
            await _quiesce_providers(refs)
        except SubscriptionError as error:
            log.warning('Cleanup for deleted user %s is unconfirmed: %s', user_id, error)
        server_owner = await server_owner_id()
        all_configs = await _machine_configs()
        configs = [config for config in all_configs if _machine_owner(config, server_owner) != user_id]
        removed_runner_ids = [
            config['id'] for config in all_configs
            if _machine_owner(config, server_owner) == user_id and config.get('kind') == MACHINE_KIND_RUNNER
        ]
        updates = {MACHINES_CONFIG_KEY: configs}
        if user_id != server_owner:
            for ref in refs:
                updates[await _settings_key(ref)] = {}
        for ref in refs:
            updates[await _credential_mode_key(ref)] = CREDENTIAL_MACHINE
        updates[project_folders.choices_key(user_id)] = {}
        updates[computer_settings.settings_key(user_id)] = {}
        # Taken last, after the machine and provider locks, as everywhere else.
        async with _computer_settings_lock:
            await Config.upsert(updates)
        await SubscriptionCredentials.delete_user(user_id)
        for ref in refs:
            _providers.pop(ref, None)
            invalidate(ref)
    for machine_id in removed_runner_ids:
        _tunnel_machines.pop(machine_id, None)
        await HUB.disconnect(machine_id, CLOSE_UNKNOWN_MACHINE, REMOVED_MACHINE_MESSAGE)


async def forget_deleted_user(user_id: str) -> None:
    """forget_user for an account that is already deleted, from any delete route.

    The account is gone either way, so a failed cleanup is only logged: its
    machines simply stop being usable.
    """
    try:
        await forget_user(user_id)
    except Exception as error:
        log.warning('Could not clean up the machines of deleted user %s: %s', user_id, error)


# Stored sign-ins and API keys


async def _credential_mode_key(ref: ProviderRef) -> str:
    return f'{await _settings_key(ref)}.credential'


async def _load_credential_mode(ref: ProviderRef) -> str:
    stored = await Config.get(await _credential_mode_key(ref), CREDENTIAL_MACHINE)
    if stored in CREDENTIAL_MODES:
        return stored
    return CREDENTIAL_MACHINE


async def _require_allowed_credential(ref: ProviderRef, credential_mode: str) -> None:
    """Refuse a sign-in the administrator's policy turned off."""
    policy = await get_policy()
    if credential_mode == CREDENTIAL_API_KEY:
        if not policy.api_keys:
            raise SubscriptionError('API keys are turned off on this Buddy. Ask an administrator.')
        return
    if not policy.plan_sign_in:
        raise SubscriptionError(
            f'Signing in with a {PROVIDER_LABELS[ref.provider_id]} plan is turned off on this Buddy. '
            f'Add your own {API_KEY_LABELS[ref.provider_id]} in Settings → Machines.'
        )


async def _credential_env(ref: ProviderRef, credential_mode: str) -> dict:
    """Environment variables that sign Claude Code in with what Buddy keeps for the user."""
    if ref.provider_id != 'claude' or credential_mode == CREDENTIAL_MACHINE:
        return {}
    secret = await SubscriptionCredentials.secret(ref.owner_id, ref.provider_id, credential_mode)
    if not secret:
        raise SubscriptionError('Your stored Claude sign-in is missing. Add it again in Settings → Machines.')
    if credential_mode == CREDENTIAL_PLAN_TOKEN:
        return {'CLAUDE_CODE_OAUTH_TOKEN': secret}
    return {'ANTHROPIC_API_KEY': secret}


async def _codex_api_key(ref: ProviderRef, credential_mode: str) -> str | None:
    if credential_mode != CREDENTIAL_API_KEY:
        return None
    secret = await SubscriptionCredentials.secret(ref.owner_id, ref.provider_id, KIND_API_KEY)
    if not secret:
        raise SubscriptionError('Your OpenAI API key is missing. Add it again in Settings → Machines.')
    return secret


async def _describe_credential(ref: ProviderRef) -> dict:
    """Which sign-in the provider uses and what Buddy keeps for it: fingerprints and expiry, never secrets."""
    stored = {}
    now = int(time.time())
    for credential in await SubscriptionCredentials.describe(ref.owner_id):
        if credential.provider != ref.provider_id:
            continue
        entry = {'fingerprint': credential.fingerprint, 'expires_at': credential.expires_at}
        if credential.expires_at is not None:
            entry['expires_soon'] = credential.expires_at - now <= CREDENTIAL_EXPIRY_WARNING_DAYS * 86400
        stored[credential.kind] = entry
    return {'mode': await _load_credential_mode(ref), 'stored': stored}


def _clean_secret(secret: str, what: str) -> str:
    secret = (secret or '').strip()
    if not secret or len(secret) > MAX_SECRET_LENGTH or any(character.isspace() for character in secret):
        raise SubscriptionError(f'Paste the whole {what}, without spaces.')
    return secret


async def _use_credential(ref: ProviderRef, credential_mode: str) -> None:
    """Switch the provider to a sign-in; called with its lock held. Running work stops first."""
    await Config.upsert({await _credential_mode_key(ref): credential_mode})
    await _quiesce_provider(ref)
    invalidate(ref)


async def save_api_key(owner_id: str, provider_id: str, api_key: str) -> dict:
    """Keep the user's own API key and use it for this provider on all their machines."""
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    await _require_allowed_credential(ref, CREDENTIAL_API_KEY)
    api_key = _clean_secret(api_key, API_KEY_LABELS[provider_id])
    async with _provider_lock(ref):
        await SubscriptionCredentials.save(owner_id, provider_id, KIND_API_KEY, api_key)
        await _use_credential(ref, CREDENTIAL_API_KEY)
    return await _describe_credential(ref)


async def save_plan_token(owner_id: str, token: str) -> dict:
    """Keep a one-year Claude token from ``claude setup-token`` and use it on all the user's machines."""
    ref = _ref(owner_id, 'claude')
    _require_single_worker()
    await _require_allowed_credential(ref, CREDENTIAL_PLAN_TOKEN)
    token = _clean_secret(token, 'Claude token')
    if not token.startswith('sk-ant-oat'):
        raise SubscriptionError('That is not a Claude token. Run claude setup-token and paste the token it prints.')
    expires_at = int(time.time()) + PLAN_TOKEN_DAYS * 86400
    async with _provider_lock(ref):
        await SubscriptionCredentials.save(owner_id, 'claude', KIND_PLAN_TOKEN, token, expires_at)
        await _use_credential(ref, CREDENTIAL_PLAN_TOKEN)
    return await _describe_credential(ref)


async def delete_credential(owner_id: str, provider_id: str, kind: str) -> dict:
    """Forget a stored token or key; a provider using it goes back to the computer's own sign-in."""
    ref = _ref(owner_id, provider_id)
    if kind not in (KIND_API_KEY, KIND_PLAN_TOKEN):
        raise SubscriptionError('Unknown kind of sign-in.')
    _require_single_worker()
    async with _provider_lock(ref):
        await SubscriptionCredentials.delete(owner_id, provider_id, kind)
        if await _load_credential_mode(ref) == kind:
            await _use_credential(ref, CREDENTIAL_MACHINE)
    return await _describe_credential(ref)


async def use_stored_credential(owner_id: str, provider_id: str, kind: str) -> dict:
    """Use a token or key Buddy already keeps for this provider, without pasting it again."""
    ref = _ref(owner_id, provider_id)
    if kind not in (KIND_API_KEY, KIND_PLAN_TOKEN) or (kind == KIND_PLAN_TOKEN and provider_id != 'claude'):
        raise SubscriptionError('Unknown kind of sign-in.')
    _require_single_worker()
    await _require_allowed_credential(ref, kind)
    async with _provider_lock(ref):
        if not await SubscriptionCredentials.secret(owner_id, provider_id, kind):
            raise SubscriptionError('Nothing is stored for that sign-in. Paste it first.')
        if await _load_credential_mode(ref) != kind:
            await _use_credential(ref, kind)
    return await _describe_credential(ref)


async def use_machine_sign_in(owner_id: str, provider_id: str) -> dict:
    """Use the CLI's own sign-in on the computer, keeping any stored token or key for later."""
    ref = _ref(owner_id, provider_id)
    _require_single_worker()
    async with _provider_lock(ref):
        if await _load_credential_mode(ref) != CREDENTIAL_MACHINE:
            await _use_credential(ref, CREDENTIAL_MACHINE)
    return await _describe_credential(ref)


# Cached status and model lists


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class CachedLoader:
    """Caches one value per user's provider on each computer and serves the old value while refreshing.

    Model lists are requested on most page loads, and the CLIs take a few
    seconds to answer, so only the first load waits for them. A caller that
    must not act on an old value passes ``serve_expired=False`` and waits too.
    """

    def __init__(self, ttl_seconds: float, load: Callable[[Any], Awaitable[Any]]):
        self._ttl_seconds = ttl_seconds
        self._load = load
        self._entries: dict[Any, _CacheEntry] = {}
        self._refreshes: dict[Any, asyncio.Task] = {}
        # Bumped on invalidate so a refresh started earlier cannot store old results.
        self._generations: dict[Any, int] = {}

    def invalidate(self, key) -> None:
        self._entries.pop(key, None)
        refresh = self._refreshes.pop(key, None)
        self._generations[key] = self._generations.get(key, 0) + 1
        if refresh is not None and not refresh.done():
            refresh.cancel()

    def invalidate_where(self, matches: Callable[[Any], bool]) -> None:
        keys = set(self._entries) | set(self._refreshes) | set(self._generations)
        for key in keys:
            if matches(key):
                self.invalidate(key)

    def cached(self, key) -> Any:
        """The last loaded value, even if expired, without loading; None when there is none."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        return entry.value

    async def _refresh(self, key) -> Any:
        generation = self._generations.get(key, 0)
        value = await self._load(key)
        if self._generations.get(key, 0) != generation:
            return None
        self._entries[key] = _CacheEntry(value, time.monotonic() + self._ttl_seconds)
        return value

    def _start_refresh(self, key) -> asyncio.Task:
        task = self._refreshes.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._refresh(key))
            task.add_done_callback(_log_refresh_failure)
            self._refreshes[key] = task
        return task

    async def get(self, key, fresh: bool = False, serve_expired: bool = True) -> Any:
        generation = self._generations.get(key, 0)
        entry = self._entries.get(key)
        if entry and not fresh and entry.expires_at > time.monotonic():
            return entry.value

        task = self._start_refresh(key)
        if entry and not fresh and serve_expired:
            return entry.value
        try:
            value = await asyncio.wait_for(asyncio.shield(task), FIRST_LOAD_WAIT_SECONDS)
            if generation != self._generations.get(key, 0):
                return None
            return value
        except TimeoutError:
            log.warning('Subscription provider %s is slow to answer; continuing without it', key)
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


async def _load_status(machine_ref: MachineRef) -> dict:
    ref = machine_ref.ref
    task = asyncio.current_task()
    try:
        _require_single_worker()
        async with _provider_lock(ref):
            settings = await _settings_for(ref, await _load_settings(ref), machine_ref.machine_id)
            await _require_verified_machine(ref, settings)
            _require_computer_cleanup(ref, machine_ref.machine_id)
            machine = await _tracked(ref, settings)
            _track_task(ref, machine_ref.machine_id, task)
        return await (await get_provider(ref)).status(settings, machine)
    except (SubscriptionError, OSError) as error:
        return {'installed': True, 'signed_in': False, 'message': str(error)}
    except Exception as error:
        # For example a runner connection that closed during the check. The
        # user's model list can wait on this refresh, so report signed out.
        log.warning('Could not check the %s sign-in: %r', PROVIDER_LABELS[ref.provider_id], error)
        return {'installed': True, 'signed_in': False, 'message': f'Could not check the sign-in: {error}'}
    finally:
        _untrack_task(ref, task)


async def _load_models(machine_ref: MachineRef) -> list[ProviderModel]:
    ref = machine_ref.ref
    task = asyncio.current_task()
    try:
        _require_single_worker()
        async with _provider_lock(ref):
            settings = await _settings_for(ref, await _load_settings(ref), machine_ref.machine_id)
            await _require_verified_machine(ref, settings)
            _require_computer_cleanup(ref, machine_ref.machine_id)
            machine = await _tracked(ref, settings)
            _track_task(ref, machine_ref.machine_id, task)
        return await (await get_provider(ref)).list_models(settings, machine)
    except Exception as error:
        # Includes a runner connection that closed during the request.
        log.warning('Could not list %s models: %s', PROVIDER_LABELS[ref.provider_id], error)
        return []
    finally:
        _untrack_task(ref, task)


status_cache = CachedLoader(STATUS_TTL_SECONDS, _load_status)
models_cache = CachedLoader(MODELS_TTL_SECONDS, _load_models)


def uses_plan(provider_status: dict) -> bool:
    """Signed in the way Buddy expects: a plan, or the user's own API key when they chose one.

    Providers flag any other sign-in with api_billing, for example an API
    account on a machine where a plan was expected.
    """
    return bool(provider_status.get('signed_in')) and not provider_status.get('api_billing')


def invalidate(ref: ProviderRef) -> None:
    """Forget the provider's status and models on every computer."""

    def of_provider(key: MachineRef) -> bool:
        return key.ref == ref

    status_cache.invalidate_where(of_provider)
    models_cache.invalidate_where(of_provider)


def invalidate_computer(owner_id: str, machine_id: str) -> None:
    """Forget what the user's providers reported on one computer, which may have changed while it was away."""

    def on_that_computer(key: MachineRef) -> bool:
        return key.owner_id == owner_id and key.machine_id == machine_id

    status_cache.invalidate_where(on_that_computer)
    models_cache.invalidate_where(on_that_computer)


async def describe_provider(
    owner_id: str, provider_id: str, fresh: bool = False, expected_machine_id: str | None = None,
    expected_machine_revision: str | None = None, check_computers: bool = False
) -> dict:
    """Everything the settings page shows for one of the user's providers.

    fresh checks the plan's own computer again. check_computers, for the
    person's own Check again, asks every other reachable computer too.
    """
    ref = _ref(owner_id, provider_id)
    for _ in range(2):
        result = await _describe_provider_snapshot(
            ref, fresh, expected_machine_id, expected_machine_revision, check_computers
        )
        if result is not None:
            return result
    raise HTTPException(status_code=409, detail='The selected machine changed. Refresh before trying again.')


async def _describe_provider_snapshot(
    ref: ProviderRef, fresh: bool, expected_machine_id: str | None, expected_machine_revision: str | None,
    check_computers: bool
) -> dict | None:
    provider = await get_provider(ref)
    async with _provider_lock(ref):
        settings = await _load_settings(ref)
        await _check_expected_machine(ref.owner_id, settings, expected_machine_id, expected_machine_revision)
        revision = await _machine_revision(ref.owner_id, settings.machine_id)
        generation = _provider_generations.get(ref, 0)
        machine = None
        execution_ready = UVICORN_WORKERS == 1
        readiness_message = SINGLE_WORKER_MESSAGE
        if execution_ready:
            try:
                await _require_verified_machine(ref, settings)
            except SubscriptionError as error:
                execution_ready = False
                readiness_message = str(error)
        if execution_ready:
            try:
                _require_computer_cleanup(ref, settings.machine_id)
                machine = await _tracked(ref, settings)
            except SubscriptionError as error:
                execution_ready = False
                readiness_message = str(error)
                log.info('Could not describe the machine for %s: %s', ref.provider_id, error)
    on_plan_computer = ref.on(settings.machine_id)
    if execution_ready:
        provider_status = await status_cache.get(on_plan_computer, fresh=fresh)
    else:
        provider_status = {'installed': True, 'signed_in': False, 'message': readiness_message}
    if provider_status is None:
        # The CLI is still answering; the page shows "Checking" and asks again.
        provider_status = {'checking': True}
    models = []
    if settings.enable and uses_plan(provider_status):
        for model in await models_cache.get(on_plan_computer, fresh=fresh) or []:
            models.append({'id': _model_id(ref.provider_id, model), 'name': model.name})

    machine_name = 'Unavailable machine'
    if not settings.machine_id:
        machine_name = 'No computer chosen'
    default_workspace = ''
    try:
        if machine is not None:
            machine_name = machine.name
            default_workspace = await machine.default_workspace()
    except SubscriptionError as error:
        log.info('Could not describe the machine for %s: %s', ref.provider_id, error)

    computers = await _describe_plan_computers(ref, settings, check=check_computers and execution_ready)
    if generation != _provider_generations.get(ref, 0):
        return None
    public_machine = {'id': settings.machine_id, 'name': machine_name}
    if revision is not None:
        public_machine['revision'] = revision
    credential = await _describe_credential(ref)
    return {
        'id': ref.provider_id,
        'name': PROVIDER_LABELS[ref.provider_id],
        'settings': asdict(settings),
        'status': provider_status,
        'login': provider.login_state() if execution_ready else {'state': 'idle'},
        # The computer the sign-in in progress runs on, when there is one.
        'login_machine_id': _login_computers.get(ref),
        'computers': computers,
        'models': models,
        'machine': public_machine,
        'default_workspace': default_workspace,
        'credential': credential,
    }


# Models and chat


def _model_id(provider_id: str, model: ProviderModel) -> str:
    return f'{MODEL_ID_PREFIXES[provider_id]}.{model.key}'


def _model_entry(provider_id: str, model: ProviderModel, credential_mode: str = CREDENTIAL_MACHINE) -> dict:
    tag = MODEL_TAGS[provider_id]
    if credential_mode == CREDENTIAL_API_KEY:
        tag = API_KEY_MODEL_TAGS[provider_id]
    return {
        'id': _model_id(provider_id, model),
        'name': model.name,
        'object': 'model',
        'created': 0,
        'owned_by': SUBSCRIPTION_OWNED_BY,
        'connection_type': 'external',
        'tags': [{'name': tag}],
        'subscription': {
            'provider': provider_id,
            'model': model.value,
            'description': model.description,
            'efforts': model.efforts,
            # Whether the CLI can run Ultra with this model; the composer offers Ultra only then.
            'ultra': model.ultra,
        },
    }


@dataclass(frozen=True)
class _PlanComputer:
    machine_id: str
    name: str
    # False for a Buddy app computer that is offline. Buddy never asks it
    # anything then, and it keeps what it last reported.
    reachable: bool
    is_plan_computer: bool

    @property
    def may_load_models(self) -> bool:
        """Listing models starts the CLI, and Codex keeps its app-server running afterwards.

        So a model list asks only the plan's own computer. The others offer
        the models they last reported, for example during a turn there.
        """
        return self.is_plan_computer and self.reachable


async def _plan_computers(ref: ProviderRef, settings: ProviderSettings) -> list[_PlanComputer]:
    """Every computer the user's plan can run on, the plan's own computer first."""
    names = {}
    app_machine_ids = set()
    if await _may_use_server_machine(ref.owner_id):
        names[LOCAL_MACHINE_ID] = LOCAL_MACHINE.name
    for config in await _owned_machine_configs(ref.owner_id):
        names[config['id']] = config['name']
        if config.get('kind') == MACHINE_KIND_RUNNER:
            app_machine_ids.add(config['id'])
    machine_ids = list(names)
    if settings.machine_id in names:
        machine_ids.remove(settings.machine_id)
        machine_ids.insert(0, settings.machine_id)

    computers = []
    for machine_id in machine_ids:
        try:
            await _require_verified_machine(ref, replace(settings, machine_id=machine_id))
        except SubscriptionError:
            continue
        reachable = True
        if machine_id in app_machine_ids:
            reachable = HUB.is_online(machine_id)
        is_plan_computer = machine_id == settings.machine_id
        computers.append(_PlanComputer(machine_id, names[machine_id], reachable, is_plan_computer))
    return computers


async def _computer_models(ref: ProviderRef, computer: _PlanComputer) -> list[ProviderModel]:
    try:
        _require_computer_cleanup(ref, computer.machine_id)
    except SubscriptionError:
        return []
    on_computer = ref.on(computer.machine_id)
    may_load = computer.may_load_models
    provider_status = await _cached_for_model_list(status_cache, on_computer, may_load) or {}
    if not uses_plan(provider_status):
        return []
    return await _cached_for_model_list(models_cache, on_computer, may_load) or []


def _sign_in_state(provider_status: dict | None) -> str:
    if provider_status is None:
        return 'not_checked'
    if not provider_status.get('installed', True):
        return 'not_installed'
    if uses_plan(provider_status):
        return 'ready'
    if provider_status.get('signed_in'):
        return 'api_billing'
    return 'signed_out'


async def _describe_plan_computers(ref: ProviderRef, settings: ProviderSettings, check: bool) -> list[dict]:
    """Whether the plan is signed in on each of the user's computers, for the Machines page.

    The plan's own computer was just checked by the description. The others
    show what they last reported, and are asked again only when the person
    asks for a check and they are reachable.
    """

    async def describe(computer: _PlanComputer) -> dict:
        on_computer = ref.on(computer.machine_id)
        if check and computer.reachable and not computer.is_plan_computer:
            provider_status = await status_cache.get(on_computer, fresh=True)
        else:
            provider_status = status_cache.cached(on_computer)
        entry = {
            'machine_id': computer.machine_id,
            'name': computer.name,
            'online': computer.reachable,
            'state': _sign_in_state(provider_status),
        }
        message = (provider_status or {}).get('message')
        if isinstance(message, str) and message:
            entry['message'] = message
        return entry

    computers = await _plan_computers(ref, settings)
    return list(await asyncio.gather(*(describe(computer) for computer in computers)))


async def _provider_models(ref: ProviderRef) -> list[dict]:
    """The models the user's plan offers on any of their computers where it is signed in."""
    # Every model list runs this, so it does not take the provider lock.
    # Changes save settings before bumping the generation, so reading the
    # generation first means a change made meanwhile empties this result.
    generation = _provider_generations.get(ref, 0)
    settings = await _load_settings(ref)
    try:
        _require_single_worker()
    except SubscriptionError:
        return []
    if not settings.enable:
        return []
    credential_mode = await _load_credential_mode(ref)
    try:
        await _require_allowed_credential(ref, credential_mode)
    except SubscriptionError:
        return []
    computers = await _plan_computers(ref, settings)
    found = await asyncio.gather(*(_computer_models(ref, computer) for computer in computers))
    if generation != _provider_generations.get(ref, 0):
        return []
    # The same model on several computers is listed once, as the plan's own computer offers it.
    entries = {}
    for models in found:
        for model in models:
            entries.setdefault(_model_id(ref.provider_id, model), _model_entry(ref.provider_id, model, credential_mode))
    return list(entries.values())


async def _cached_for_model_list(cache: CachedLoader, machine_ref: MachineRef, may_load: bool = True) -> Any:
    """Serve only what is cached while the user's own action holds the provider lock.

    Sign-in, sign-out and runner checks can hold it for up to a minute, and a
    refresh started now would wait behind them.
    """
    if not may_load or _provider_lock(machine_ref.ref).locked():
        return cache.cached(machine_ref)
    return await cache.get(machine_ref)


async def _listed_models(ref: ProviderRef) -> list[dict]:
    """A model list includes these, so a provider failure adds no models instead of failing it."""
    try:
        return await _provider_models(ref)
    except Exception:
        log.exception('Could not list %s subscription models', PROVIDER_LABELS[ref.provider_id])
        return []


# Every subscription model any user has offered. Model IDs are the same for
# every user, such as "claude-code.opus", so the shared model registry can
# route them; who sees a model, and which CLI model a turn uses, comes from
# the requesting user's own providers.
_catalog: dict[str, dict] = {}


async def get_user_subscription_models(user) -> list[dict]:
    """The user's own models: their providers, signed in on their machines."""
    if user is None or not await may_use_machines(user):
        return []
    claude_models, codex_models = await asyncio.gather(
        _listed_models(_ref(user.id, 'claude')), _listed_models(_ref(user.id, 'codex'))
    )
    models = claude_models + codex_models
    for model in models:
        _catalog[model['id']] = model
    return models


async def get_subscription_catalog(user=None) -> list[dict]:
    """Registry entries for every known subscription model, refreshed with the user's own."""
    await get_user_subscription_models(user)
    return [dict(model) for model in _catalog.values()]


def _preset_base_model_id(model: dict) -> str | None:
    """The model a Workspace preset runs on, from its saved row."""
    info = model.get('info') or {}
    return info.get('base_model_id')


async def personalize_models(models: list[dict], user) -> list[dict]:
    """Keep only the subscription models this user's providers offer, with their own details.

    A Workspace preset based on a subscription model runs on the user's own
    plan, so it is kept, unchanged, when their providers offer its base model.
    Its own access grants still decide whether the user may see it.
    """
    own = {model['id']: model for model in await get_user_subscription_models(user)}
    personalized = []
    for model in models:
        if model.get('preset'):
            if _preset_base_model_id(model) in own:
                personalized.append(model)
            continue
        entry = own.get(model.get('id'))
        if entry is None:
            continue
        personalized.append({**model, 'tags': entry['tags'], 'subscription': entry['subscription']})
    return personalized


async def user_offers_model(user, model_id: str) -> bool:
    return any(model['id'] == model_id for model in await get_user_subscription_models(user))


def _require_plan(provider_id: str, provider_status: dict | None) -> None:
    """Start a turn only when the CLI is known to use a plan, so it never bills the API."""
    label = PROVIDER_LABELS[provider_id]
    if provider_status is None:
        raise SubscriptionError(f'Still checking the {label} sign-in. Try again in a moment.')
    if provider_status.get('api_billing'):
        # Refuse rather than bill the API under a "plan" model name.
        raise SubscriptionError(provider_status.get('message') or 'This account bills API usage, not a plan.')
    if not uses_plan(provider_status):
        raise SubscriptionError(
            provider_status.get('message')
            or f'{label} is not signed in. Sign in under Settings → Machines.'
        )


def is_conflicting_preset_row(model: dict | None, model_info) -> bool:
    """Whether a Workspace row based on another model reuses a subscription model's own ID.

    The model list shows the subscription model itself in that case, so the
    row is not its settings, and following it would let whoever created the
    row choose the model and system prompt of a turn on someone's plan. A
    subscription model's own settings are saved without a base model, which
    only administrators can do.
    """
    if not model or not model_info:
        return False
    is_subscription_base_model = bool(model.get('subscription')) and not model.get('preset')
    return is_subscription_base_model and bool(model_info.base_model_id)


async def _apply_preset(request, payload: dict, metadata: dict, user, models: dict) -> tuple[str, dict, bool]:
    """Apply a Workspace model preset's params and system prompt, like the OpenAI route.

    Also returns whether someone other than the user wrote the preset. Such a
    preset runs chat-only, so it cannot steer tools on the user's machine.
    """
    model_id = payload.get('model')
    model_info = await Models.get_model_by_id(model_id)
    if not model_info:
        return model_id, payload, False
    if is_conflicting_preset_row(models.get(model_id), model_info):
        log.warning('Ignoring the Workspace model %s: it reuses a subscription model ID', model_id)
        return model_id, payload, False

    written_by_someone_else = getattr(model_info, 'user_id', None) != getattr(user, 'id', None)
    if model_info.base_model_id:
        model_id = model_info.base_model_id
    params = model_info.params.model_dump()
    if params:
        system = params.pop('system', None)
        payload = apply_model_params_to_body_openai(params, payload)
        if not getattr(request.state, 'bypass_system_prompt', False):
            payload = await apply_system_prompt_to_body(system, payload, metadata, user)
    return model_id, payload, written_by_someone_else


def _turn_model(machine_ref: MachineRef, model_id: str, subscription: dict) -> tuple[str, list[str]]:
    """The CLI model and reasoning levels a turn uses.

    Once the model list of the computer running the turn is loaded, it
    decides: a model it does not offer is refused. Until then, for example
    right after a restart, the registry entry is used; providers still check
    the reasoning levels against the CLI that runs the turn.
    """
    provider_id = machine_ref.provider_id
    own_models = models_cache.cached(machine_ref)
    if own_models is not None:
        for model in own_models:
            if _model_id(provider_id, model) == model_id:
                return model.value, _effort_names(model.efforts)
        raise SubscriptionError(
            f'{PROVIDER_LABELS[provider_id]} on your computer does not offer this model. Choose another model.'
        )
    return subscription['model'], _effort_names(subscription.get('efforts'))


def _effort_names(efforts) -> list[str]:
    """A model's reasoning levels, leaving out anything that is not a level's name."""
    if not isinstance(efforts, list):
        return []
    return [effort for effort in efforts if isinstance(effort, str) and effort]


ULTRA_UNSUPPORTED_REASON = "{label} on your computer can't run workers with {model}."
ULTRA_UNLISTED_REASON = "{label} on your computer hasn't listed this chat's model yet."
ULTRA_WORKER_PROVIDER_MESSAGE = "Ultra's workers run on this chat's {label} plan. Choose one of its models for them."
ULTRA_WORKER_MISSING_MESSAGE = "{label} on your computer does not offer the workers' model. Choose another worker model."


async def _ultra_models(machine_ref: MachineRef) -> dict[str, dict]:
    """The models an Ultra turn may use, as model list entries by model ID.

    Ultra writes the workers' model and its name into the turn's
    instructions, so they come only from the person's own model lists, never
    from the shared model registry: it holds what any user's computer last
    reported under the same model IDs. As in _turn_model, the computer running
    the turn decides once its list is loaded. Until then, for example right
    after a restart or on a computer other than the plan's own, which model
    lists never ask, the person's model list stands in: the models their plan
    offers on their computers.
    """
    provider_id = machine_ref.provider_id
    own_models = models_cache.cached(machine_ref)
    if own_models is not None:
        entries = [_model_entry(provider_id, model) for model in own_models]
    else:
        entries = await _listed_models(machine_ref.ref)
    return {entry['id']: entry for entry in entries}


def _runs_ultra(entry: dict) -> bool:
    return entry['subscription'].get('ultra') is True


async def _turn_ultra(machine_ref: MachineRef, metadata: dict, model_id: str) -> tuple[UltraOptions | None, str | None]:
    """The person's Ultra choice for this turn, resolved on the computer that runs it.

    Returns the options, or None when the turn runs without Ultra, and the
    reason when the person turned Ultra on but the CLI can't run it with the
    chosen models. Then the reply runs normally and says why.

    A worker model from another provider, or one the computer does not offer,
    is refused. A worker thinking level the workers' model does not offer is
    dropped. None for the model or the level means the workers use the chat's own.
    The models, their levels and their names come from _ultra_models.
    """
    choice = metadata.get('ultra')
    if metadata.get('task') or not isinstance(choice, dict):
        # Titles, tags and follow-up suggestions never start workers.
        return None, None
    provider_id = machine_ref.provider_id
    label = PROVIDER_LABELS[provider_id]
    models = await _ultra_models(machine_ref)
    chat_entry = models.get(model_id)
    if chat_entry is None:
        return None, ULTRA_UNLISTED_REASON.format(label=label)
    if not _runs_ultra(chat_entry):
        return None, ULTRA_UNSUPPORTED_REASON.format(label=label, model=chat_entry['name'])

    worker_model = None
    worker_label = None
    worker_efforts = _effort_names(chat_entry['subscription'].get('efforts'))
    worker_model_id = choice.get('worker_model')
    if worker_model_id:
        if _provider_for_model_id(worker_model_id) != provider_id:
            raise SubscriptionError(ULTRA_WORKER_PROVIDER_MESSAGE.format(label=label))
        worker_entry = models.get(worker_model_id)
        if worker_entry is None:
            raise SubscriptionError(ULTRA_WORKER_MISSING_MESSAGE.format(label=label))
        worker_label = worker_entry['name']
        if not _runs_ultra(worker_entry):
            return None, ULTRA_UNSUPPORTED_REASON.format(label=label, model=worker_label)
        worker_model = worker_entry['subscription']['model']
        worker_efforts = _effort_names(worker_entry['subscription'].get('efforts'))

    worker_effort = choice.get('worker_effort')
    if worker_effort not in worker_efforts:
        # Not a level the workers' model offers, so they use the chat's own.
        worker_effort = None
    options = UltraOptions(
        worker_model=worker_model,
        worker_label=worker_label,
        worker_effort=worker_effort,
        max_workers=ultra.max_workers(),
    )
    return options, None


@dataclass(frozen=True)
class _TurnTarget:
    machine_id: str
    access: str
    folder_mode: str
    path: str = ''
    # Detached turns run chat only, on whichever computer is available.
    detached: bool = False
    # The chat's target it came from; turns outside chats have none.
    row: ChatRunTargetModel | None = None


DETACHED_NO_COMPUTER_MESSAGE = (
    '{label} has no computer to run this chat on. Open the Buddy app on one of your computers, '
    'or connect one in Settings → Machines.'
)
REMOVED_TARGET_MESSAGE = 'The computer this chat ran on was removed. Start a new chat to choose another.'
SERVER_TARGET_MESSAGE = 'Buddy server is no longer available to your account. Start a new chat to run elsewhere.'
RESET_TARGET_MESSAGE = '{name} was set up again after this chat started. Start a new chat to work there.'
MISSING_FOLDER_MESSAGE = 'The folder this chat works in no longer exists on {name}. Start a new chat to choose another.'


async def _detached_host(user, ref: ProviderRef, preferred: str | None = None) -> str:
    """A computer for a Detached turn, which runs chat only.

    The first reachable one where the plan last reported being signed in,
    trying the preferred computer, the last one a chat used and the plan's
    own computer first; otherwise the first reachable one.
    """
    settings = await _load_settings(ref)
    computers = {computer.machine_id: computer for computer in await _plan_computers(ref, settings)}
    candidates = []
    if preferred:
        candidates.append(preferred)
    last_used = await ChatRunTargets.last_for_user(user.id)
    if last_used is not None and last_used.machine_id:
        candidates.append(last_used.machine_id)
    candidates.extend(computers)
    reachable = []
    for machine_id in dict.fromkeys(candidates):
        computer = computers.get(machine_id)
        if computer is None or not computer.reachable:
            continue
        try:
            _require_computer_cleanup(ref, machine_id)
        except SubscriptionError:
            continue
        reachable.append(machine_id)
    for machine_id in reachable:
        if uses_plan(status_cache.cached(ref.on(machine_id)) or {}):
            return machine_id
    if reachable:
        return reachable[0]
    raise SubscriptionError(DETACHED_NO_COMPUTER_MESSAGE.format(label=PROVIDER_LABELS[ref.provider_id]))


async def _row_turn_target(user, ref: ProviderRef, row: 'ChatRunTargetModel') -> _TurnTarget:
    """Where a chat's turns run, as its target says, refused when its computer is gone or was set up again."""
    if row.kind == run_targets.KIND_DETACHED:
        return _TurnTarget(await _detached_host(user, ref), ACCESS_CHAT, 'none', detached=True, row=row)
    if row.machine_id == LOCAL_MACHINE_ID and not await _may_use_server_machine(user.id):
        raise SubscriptionError(SERVER_TARGET_MESSAGE)
    revision = await _machine_revision(user.id, row.machine_id)
    if revision is None:
        raise SubscriptionError(REMOVED_TARGET_MESSAGE)
    if row.machine_revision and revision != row.machine_revision:
        raise SubscriptionError(RESET_TARGET_MESSAGE.format(name=await _computer_name(user.id, row.machine_id)))
    return _TurnTarget(row.machine_id, row.access, row.folder_mode, row.path or '', row=row)


async def _detached_turn(user, ref: ProviderRef, preferred: str | None = None) -> _TurnTarget:
    return _TurnTarget(await _detached_host(user, ref, preferred), ACCESS_CHAT, 'none', detached=True)


async def _turn_target(user, ref: ProviderRef, metadata: dict) -> _TurnTarget:
    """Where this turn runs. Contacts no computer."""
    chat_id = metadata.get('chat_id')
    if not chat_id:
        # Outside chats, such as API calls without chat management: where the plan runs, as before.
        settings = await _load_settings(ref)
        return _TurnTarget(settings.machine_id, settings.access, run_targets.FOLDER_AUTO)
    if not isinstance(chat_id, str) or chat_id.startswith(CHANNEL_CHAT_ID_PREFIX):
        # Every member reads a channel reply's prompt and answer, so it runs chat only.
        return await _detached_turn(user, ref)
    if is_temporary_chat_id(chat_id):
        # A temporary chat has no saved target; each turn carries the bar's choice.
        raw = metadata.get(REQUESTED_RUN_TARGET_KEY)
        row = await _target_row(user, chat_id, raw, metadata.get('folder_id'), ref.provider_id)
        return await _row_turn_target(user, ref, row)
    if not await Chats.is_chat_owner(chat_id, user.id):
        # Someone else's chat, such as an admin answering in it: chat only, on the
        # poster's own computers. The owner's target is never used.
        return await _detached_turn(user, ref)
    row = await ChatRunTargets.get(chat_id)
    if metadata.get('task'):
        # Titles, tags and follow-up suggestions run chat only, and never fix where a chat runs.
        preferred = None
        if row is not None:
            preferred = row.machine_id
        return await _detached_turn(user, ref, preferred)
    if row is None:
        started_before_targets = await _started_before_targets(user, chat_id)
        row = await bind_chat_target(
            user, chat_id, None, ref.provider_id, started_before_targets=started_before_targets
        )
    if row is None:
        return await _detached_turn(user, ref)
    return await _row_turn_target(user, ref, row)


async def _turn_access(user, target: _TurnTarget, metadata: dict, foreign_preset: bool) -> str:
    """The target's access, lowered for turns that must run chat only."""
    if target.detached or foreign_preset:
        # A preset someone else wrote can't steer tools on the person's computer.
        return ACCESS_CHAT
    held = await project_folders.instructions_hold(user.id, metadata)
    if held is not None:
        acknowledged = target.row is not None and target.row.acknowledged_instructions_folder_id == held.folder_id
        if not acknowledged:
            return ACCESS_CHAT
    return target.access


async def _require_chat_folder(machine, path: str) -> None:
    """Refuse rather than start somewhere else when the chat's folder is gone. The message never names the path."""
    if not await machine.path_exists(path, 'dir'):
        raise SubscriptionError(MISSING_FOLDER_MESSAGE.format(name=machine.name))


def _with_start_note(conversation, cwd: str):
    """Tell a Full-access plan it chooses where to work, ahead of the chat's own system prompt."""
    note = project_folders.START_FOLDER_NOTE.format(cwd=cwd)
    system = note
    if conversation.system:
        system = f'{note}\n\n{conversation.system}'
    return replace(conversation, system=system)


def _with_ultra_note(conversation, provider_id: str, access: str, options: UltraOptions):
    """Tell an Ultra turn to orchestrate workers, ahead of the rest of its instructions."""
    system = ultra.with_orchestrator_note(conversation.system, provider_id, access, options)
    return replace(conversation, system=system)


async def _after_status(description: str, events):
    """A turn's events, after a status line that starts the reply."""
    yield StatusUpdate(description)
    async for event in events:
        yield event


# Live Ultra turns


# Entries compare by identity, as providers find a turn by its very request:
# two turns with equal requests are still two turns.
@dataclass(eq=False)
class _LiveUltraTurn:
    """An Ultra turn in a chat that is still running, so the person can stop its workers one by one."""

    chat_id: str
    # The reply's message, or None when the request named none.
    message_id: str | None
    owner_id: str
    provider_id: str
    # The provider that runs the turn, which stops its workers.
    provider: Any
    # The request passed to the provider's run_turn. The provider finds the
    # turn's workers by this very object.
    turn: TurnRequest


# Each chat's live Ultra turns, by chat id. Buddy runs one backend worker
# (see _require_single_worker), so every turn runs in this process.
#
# The chat page sends one turn per chat at a time. By default a message sent
# while a reply runs waits in the composer's queue until the reply is done,
# and an Ultra reply is done only once its workers have ended; otherwise the
# page stops the reply first. The server doesn't refuse a second turn,
# though. A request that names several models runs one turn per model, each
# with its own message id, and a second tab can send meanwhile. A Stop that
# goes through Redis returns before the stopped turn has stopped its workers,
# so the next message may start while it does. So a chat holds a list, and
# stopping a worker asks each of the chat's turns.
_live_ultra_turns: dict[str, list[_LiveUltraTurn]] = {}


def _live_ultra_turn(user, ref: ProviderRef, provider, turn: TurnRequest, metadata: dict) -> _LiveUltraTurn | None:
    """The registry entry for an Ultra turn in a chat, or None for any other turn.

    A turn outside a chat has no chat to stop its workers from; stopping the
    reply still stops them all.
    """
    chat_id = metadata.get('chat_id')
    if turn.ultra is None or not chat_id:
        return None
    return _LiveUltraTurn(
        chat_id=chat_id,
        message_id=metadata.get('message_id'),
        owner_id=user.id,
        provider_id=ref.provider_id,
        provider=provider,
        turn=turn,
    )


async def _while_live(live_turn: _LiveUltraTurn, events):
    """An Ultra turn's events, with the turn in the registry from its start until they end.

    The provider's events end only when the turn really has: all its workers
    have ended and its last result is read, or it failed, or it was stopped,
    in which case the provider has stopped its workers first. However the
    events end, the turn leaves the registry.
    """
    _live_ultra_turns.setdefault(live_turn.chat_id, []).append(live_turn)
    try:
        async for event in events:
            yield event
    finally:
        chat_turns = _live_ultra_turns[live_turn.chat_id]
        chat_turns.remove(live_turn)
        if not chat_turns:
            del _live_ultra_turns[live_turn.chat_id]


async def stop_worker(user, chat_id: str, worker_id: str) -> bool:
    """Stop one running worker of the person's live Ultra turn in a chat.

    Asks the provider that runs the turn to stop it; the provider then
    reports the worker stopped in the turn's own events. Returns False when
    the person runs no Ultra turn in the chat, or none of theirs has that
    worker running, so the stop route can answer that it isn't running.
    """
    # A copy, since a turn may end while its provider is asked.
    for live_turn in list(_live_ultra_turns.get(chat_id, [])):
        if live_turn.owner_id != user.id:
            continue
        if await live_turn.provider.stop_worker(live_turn.turn, worker_id):
            return True
    return False


async def generate_subscription_chat_completion(request, form_data: dict, user, models):
    if not await may_use_machines(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_ALLOWED_MESSAGE)

    payload = {**form_data}
    metadata = payload.pop('metadata', None) or {}
    model_id, payload, foreign_preset = await _apply_preset(request, payload, metadata, user, models)
    base_model = models.get(model_id) or {}
    subscription = base_model.get('subscription')
    if not subscription or subscription.get('provider') not in PROVIDER_IDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Model not found')

    ref = ProviderRef(user.id, subscription['provider'])
    provider = await get_provider(ref)
    is_task = bool(metadata.get('task'))

    try:
        _require_single_worker()
        target = await _turn_target(user, ref, metadata)
        async with _provider_lock(ref):
            settings = await _settings_for(ref, await _load_settings(ref), target.machine_id)
            await _require_verified_machine(ref, settings)
            _require_computer_cleanup(ref, target.machine_id)
            generation = _provider_generations.get(ref, 0)
        if not settings.enable:
            raise SubscriptionError(
                f'{PROVIDER_LABELS[ref.provider_id]} subscription models are turned off in Settings → Machines.'
            )
        # A status older than its TTL may predate a switch to API billing on
        # the machine, so a turn waits for a current one.
        on_computer = ref.on(target.machine_id)
        _require_plan(ref.provider_id, await status_cache.get(on_computer, serve_expired=False))
        cli_model, model_efforts = _turn_model(on_computer, model_id, subscription)
        access = await _turn_access(user, target, metadata, foreign_preset)
        ultra_options, ultra_off_reason = await _turn_ultra(on_computer, metadata, model_id)
        async with _provider_lock(ref):
            if generation != _provider_generations.get(ref, 0):
                raise SubscriptionError('The provider machine changed; refresh before starting another turn.')
            machine = await _tracked(ref, settings)
            project_path = ''
            if access != ACCESS_CHAT and target.folder_mode == run_targets.FOLDER_PATH:
                project_path = target.path
            cwd = await working_directory(settings, access, machine, project_path)
            conversation = parse_messages(payload.get('messages'))
            # Chats from before targets keep the prompt they started with.
            lets_ai_choose = (
                target.row is not None
                and target.folder_mode == run_targets.FOLDER_AUTO
                and target.row.note != run_targets.FROM_PLAN
            )
            if access == ACCESS_FULL and lets_ai_choose:
                conversation = _with_start_note(conversation, cwd)
            if ultra_options is not None:
                # Added on the server for this turn only, so it is never saved with the chat.
                conversation = _with_ultra_note(conversation, ref.provider_id, access, ultra_options)
            turn = TurnRequest(
                model=cli_model,
                conversation=conversation,
                settings=replace(settings, access=access),
                cwd=cwd,
                effort=requested_effort(payload),
                is_task=is_task,
                machine=machine,
                efforts=model_efforts,
                ultra_options=ultra_options,
            )
        # Checked outside the provider lock, so a slow computer never holds it.
        if project_path:
            await _require_chat_folder(machine, project_path)
    except SubscriptionError as error:
        events = failure_events(str(error))
    else:
        events = _tracked_turn(ref, provider, turn, generation)
        live_turn = _live_ultra_turn(user, ref, provider, turn, metadata)
        if live_turn is not None:
            events = _while_live(live_turn, events)
        if ultra_off_reason:
            events = _after_status(ultra.OFF_STATUS.format(reason=ultra_off_reason), events)

    response_model_id = form_data.get('model') or model_id
    if payload.get('stream'):
        return StreamingResponse(stream_events(events, response_model_id), media_type='text/event-stream')
    return await collect_events(events, response_model_id)
