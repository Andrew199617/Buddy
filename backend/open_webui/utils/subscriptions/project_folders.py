"""Each person's project folder for a chat folder: where Claude and ChatGPT start for its chats.

The choice is stored per person in Config, never in the chat folder's own row.
A path only means something on one person's computer, and someone who can edit
a shared chat folder must not be able to point another person's agent at a
folder. Subfolders follow their nearest parent that has a choice.
"""

import asyncio
import time
from dataclasses import dataclass, field, replace

from open_webui.models.access_grants import AccessGrants
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.folders import Folders
from open_webui.utils.access_control.folders import has_folder_access
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.subscriptions.common import ACCESS_LEVELS, FOLDER_INSTRUCTIONS_KEY, folder_adds_instructions
from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.project_paths import MAX_PATH_CHARS, clean_project_path  # noqa: F401

# "Let the AI choose": start in the plan's start folder; the agent finds the project.
MODE_AUTO = 'auto'
# An exact folder on each computer.
MODE_FOLDER = 'folder'
# No files: plans run chat-only for chats in this folder, and new chats here run Detached.
MODE_CHAT = 'chat'
MODES = (MODE_AUTO, MODE_FOLDER, MODE_CHAT)

# A choice's computer or access left empty follows the person's usual default for new chats.
FOLLOW_DEFAULT = ''

MAX_FOLDER_CHOICES = 500
MAX_PARENT_DEPTH = 32
MAX_RECENT_PATHS = 5
MAX_OFFERED_PER_COMPUTER = 5
CHOICES_VERSION = 2

# Told to Full-access plans in a chat folder set to "Let the AI choose", ahead of the chat's own system prompt.
START_FOLDER_NOTE = (
    'No project folder is set for this chat, so you choose where to work. You start in {cwd}. '
    'If the request is about an existing project on this computer, find it and use absolute paths. '
    'Create new projects as subfolders of {cwd}. When you change files, say which folder you worked in.'
)



@dataclass
class SavedPath:
    path: str
    # The computer's revision when the path was chosen; a new one means the computer was set up again.
    revision: str


@dataclass
class FolderChoice:
    folder_id: str
    mode: str
    paths: dict[str, SavedPath] = field(default_factory=dict)
    updated_at: int = 0
    # Filled in when resolved, for messages.
    folder_name: str = ''
    # The computer new chats in the folder run on, or FOLLOW_DEFAULT.
    machine_id: str = FOLLOW_DEFAULT
    # The access new chats in the folder start with, or FOLLOW_DEFAULT for the plan's.
    access: str = FOLLOW_DEFAULT
    # More folders a new chat here can pick from, per computer.
    offered: dict[str, list[SavedPath]] = field(default_factory=dict)


# --- Storage ---------------------------------------------------------------------

_user_locks: dict[str, asyncio.Lock] = {}


def _user_lock(user_id: str) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def choices_key(user_id: str) -> str:
    return f'subscriptions.users.{user_id}.project_folders'


def _parse_saved_path(saved) -> SavedPath | None:
    if not isinstance(saved, dict):
        return None
    path = saved.get('path')
    revision = saved.get('revision')
    if isinstance(path, str) and path and isinstance(revision, str):
        return SavedPath(path, revision)
    return None


def _parse_paths(value) -> dict[str, SavedPath]:
    if not isinstance(value, dict):
        return {}
    paths = {}
    for machine_id, saved in value.items():
        parsed = _parse_saved_path(saved)
        if isinstance(machine_id, str) and parsed is not None:
            paths[machine_id] = parsed
    return paths


def _parse_offered(value) -> dict[str, list[SavedPath]]:
    if not isinstance(value, dict):
        return {}
    offered = {}
    for machine_id, entries in value.items():
        if not isinstance(machine_id, str) or not isinstance(entries, list):
            continue
        parsed = [_parse_saved_path(entry) for entry in entries[:MAX_OFFERED_PER_COMPUTER]]
        parsed = [entry for entry in parsed if entry is not None]
        if parsed:
            offered[machine_id] = parsed
    return offered


def _parse_choices(value) -> dict[str, FolderChoice]:
    """Anything unreadable reads as no choice."""
    folders = value.get('folders') if isinstance(value, dict) else None
    if not isinstance(folders, dict):
        return {}
    choices = {}
    for folder_id, entry in folders.items():
        if not isinstance(folder_id, str) or not isinstance(entry, dict) or entry.get('mode') not in MODES:
            continue
        paths = {}
        if entry['mode'] == MODE_FOLDER:
            # May be empty when its computers were removed; such a choice refuses turns.
            paths = _parse_paths(entry.get('paths'))
        updated_at = entry.get('updated_at')
        if not isinstance(updated_at, int):
            updated_at = 0
        # Version 1 entries have none of these, and read as following the usual defaults.
        machine_id = entry.get('machine_id')
        if not isinstance(machine_id, str):
            machine_id = FOLLOW_DEFAULT
        access = entry.get('access')
        if access not in ACCESS_LEVELS:
            access = FOLLOW_DEFAULT
        choices[folder_id] = FolderChoice(
            folder_id,
            entry['mode'],
            paths,
            updated_at,
            machine_id=machine_id,
            access=access,
            offered=_parse_offered(entry.get('offered')),
        )
    return choices


def _serialize_saved_path(saved: SavedPath) -> dict:
    return {'path': saved.path, 'revision': saved.revision}


def _serialize(choices: dict[str, FolderChoice]) -> dict:
    """Version 2 keeps mode and paths as version 1 wrote them, so older Buddy versions still read them."""
    folders = {}
    for folder_id, choice in choices.items():
        paths = {}
        for machine_id, saved in choice.paths.items():
            paths[machine_id] = _serialize_saved_path(saved)
        offered = {}
        for machine_id, entries in choice.offered.items():
            offered[machine_id] = [_serialize_saved_path(saved) for saved in entries]
        folders[folder_id] = {
            'mode': choice.mode,
            'machine_id': choice.machine_id,
            'access': choice.access,
            'paths': paths,
            'offered': offered,
            'updated_at': choice.updated_at,
        }
    return {'version': CHOICES_VERSION, 'folders': folders}


async def load_choices(user_id: str) -> dict[str, FolderChoice]:
    return _parse_choices(await Config.get(choices_key(user_id), None))


async def _without_missing_folders(choices: dict[str, FolderChoice]) -> dict[str, FolderChoice]:
    if not choices:
        return choices
    existing = {folder.id for folder in await Folders.get_folders_by_ids(list(choices))}
    return {folder_id: choice for folder_id, choice in choices.items() if folder_id in existing}


def _without_removed_computers(choices: dict[str, FolderChoice], machine_ids: set[str]) -> dict[str, FolderChoice]:
    """Forget paths on computers the person no longer has.

    A folder left with none keeps its choice, so its chats keep refusing until
    the person chooses again, instead of quietly starting somewhere else.
    """
    kept = {}
    for folder_id, choice in choices.items():
        paths = {machine_id: saved for machine_id, saved in choice.paths.items() if machine_id in machine_ids}
        offered = {machine_id: saved for machine_id, saved in choice.offered.items() if machine_id in machine_ids}
        machine_id = choice.machine_id
        if machine_id not in machine_ids:
            machine_id = FOLLOW_DEFAULT
        kept[folder_id] = replace(choice, paths=paths, offered=offered, machine_id=machine_id)
    return kept


async def _store(user_id: str, choices: dict[str, FolderChoice], machine_ids: set[str]) -> None:
    choices = await _without_missing_folders(choices)
    choices = _without_removed_computers(choices, machine_ids)
    if len(choices) > MAX_FOLDER_CHOICES:
        raise SubscriptionError(f'You can set project folders for up to {MAX_FOLDER_CHOICES} chat folders.')
    await Config.upsert({choices_key(user_id): _serialize(choices)})


def _checked_offered(offered: dict[str, list[SavedPath]] | None, machine_ids: set[str]) -> dict[str, list[SavedPath]]:
    checked = {}
    for machine_id, entries in (offered or {}).items():
        if machine_id not in machine_ids:
            raise SubscriptionError('That computer was removed. Choose another.')
        if len(entries) > MAX_OFFERED_PER_COMPUTER:
            raise SubscriptionError(f'Offer up to {MAX_OFFERED_PER_COMPUTER} folders on each computer.')
        unique = []
        seen = set()
        for saved in entries:
            if saved.path in seen:
                continue
            seen.add(saved.path)
            unique.append(saved)
        if unique:
            checked[machine_id] = unique
    return checked


async def save_choice(
    user_id: str,
    folder_id: str,
    mode: str,
    paths: dict[str, SavedPath],
    machine_ids: set[str],
    machine_id: str = FOLLOW_DEFAULT,
    access: str = FOLLOW_DEFAULT,
    offered: dict[str, list[SavedPath]] | None = None,
) -> None:
    """Replace this person's choice for one chat folder.

    machine_ids are the computers the person has now; paths on any other
    computer are forgotten, in every entry. machine_id and access are what
    new chats in the folder start with, and offered lists more folders they
    can pick from.
    """
    if mode not in MODES:
        raise SubscriptionError('Choose how chats in this folder work.')
    if access != FOLLOW_DEFAULT and access not in ACCESS_LEVELS:
        raise SubscriptionError('Choose chat only, read files, or full access.')
    if machine_id != FOLLOW_DEFAULT and machine_id not in machine_ids:
        raise SubscriptionError('That computer was removed. Choose another.')
    offered = _checked_offered(offered, machine_ids)
    if mode == MODE_CHAT:
        # New chats here run Detached, with no computer, folder or file access to choose.
        paths = {}
        offered = {}
        machine_id = FOLLOW_DEFAULT
        access = FOLLOW_DEFAULT
    elif mode == MODE_AUTO:
        paths = {}
    elif not paths:
        raise SubscriptionError('Choose a folder on at least one computer.')
    elif machine_id != FOLLOW_DEFAULT and machine_id not in paths:
        raise SubscriptionError('Choose a project folder on the computer new chats here run on.')
    choice = FolderChoice(
        folder_id,
        mode,
        dict(paths),
        int(time.time()),
        machine_id=machine_id,
        access=access,
        offered=offered,
    )
    async with _user_lock(user_id):
        choices = await load_choices(user_id)
        choices[folder_id] = choice
        await _store(user_id, choices, machine_ids | set(paths))


async def clear_choice(user_id: str, folder_id: str, machine_ids: set[str]) -> None:
    """Forget this person's choice, so the folder follows its parent again."""
    async with _user_lock(user_id):
        choices = await load_choices(user_id)
        choices.pop(folder_id, None)
        await _store(user_id, choices, machine_ids)


# --- Resolving -------------------------------------------------------------------


async def readable_folder(user_id: str, folder_id):
    """The chat folder, when it still exists and this person may read it."""
    if not isinstance(folder_id, str) or not folder_id:
        return None
    folder = await Folders.get_folder_by_id(folder_id)
    if folder is None:
        return None
    if not await has_folder_access(user_id, folder, 'read', None):
        return None
    return folder


async def resolve_folder(user_id: str, folder, choices: dict[str, FolderChoice] | None = None):
    """This person's own choice for the folder, and the one it would follow from a parent folder.

    Returns (own, inherited); each is None when absent. Only this person's
    entries are read, never anyone else's. Inheriting stops at a parent the
    person can't read, and at a parent with another owner: subfolders always
    belong to their parent's owner, so such a link comes from someone moving
    their own folder under it, and must not pull in this person's choice.
    """
    if choices is None:
        choices = await load_choices(user_id)
    own = choices.get(folder.id)
    if own is not None:
        own.folder_name = folder.name
    inherited = None
    seen = {folder.id}
    parent_id = folder.parent_id
    depth = 0
    while parent_id and parent_id not in seen and depth < MAX_PARENT_DEPTH:
        seen.add(parent_id)
        depth += 1
        parent = await Folders.get_folder_by_id(parent_id)
        if parent is None or parent.user_id != folder.user_id:
            break
        if not await has_folder_access(user_id, parent, 'read', None):
            break
        entry = choices.get(parent.id)
        if entry is not None:
            entry.folder_name = parent.name
            inherited = entry
            break
        parent_id = parent.parent_id
    return own, inherited


async def _others_can_edit(user_id: str, folder) -> bool:
    """Someone besides this person owns the folder or one above it, or may edit one of them."""
    folder_ids = []
    seen = set()
    current = folder
    while current is not None and current.id not in seen:
        if len(folder_ids) == MAX_PARENT_DEPTH:
            # Deeper than Buddy follows: write access may come from a folder further up.
            return True
        if current.user_id != user_id:
            return True
        seen.add(current.id)
        folder_ids.append(current.id)
        if not current.parent_id:
            break
        current = await Folders.get_folder_by_id(current.parent_id)
    grants = await AccessGrants.get_grants_by_resources('folder', folder_ids)
    for folder_grants in grants.values():
        for grant in folder_grants:
            if grant.permission != 'write':
                continue
            if grant.principal_type == 'user' and grant.principal_id == user_id:
                continue
            return True
    return False


async def has_shared_instructions(user_id: str, folder) -> bool:
    """The folder adds instructions to this person's chats that someone else can change.

    Like a preset written by someone else, those chats then stay chat-only
    until this person chooses the folder's project folder themselves.
    """
    if not folder_adds_instructions(folder.data):
        return False
    return await _others_can_edit(user_id, folder)


async def _held_back_by_received_instructions(user_id: str, metadata: dict, choices: dict) -> FolderChoice | None:
    """Chat-only when the turn received a folder's instructions that someone else can change.

    The middleware records the folder whose prompt or knowledge it applied, so
    this holds even if that text was removed since, and for admins, who receive
    it from folders they can't otherwise read. The person's own choice for that
    folder lifts it.
    """
    folder_id = metadata.get(FOLDER_INSTRUCTIONS_KEY)
    if not isinstance(folder_id, str) or not folder_id or folder_id in choices:
        return None
    folder = await Folders.get_folder_by_id(folder_id)
    if folder is None:
        # Deleted since; its text still reached this turn.
        return FolderChoice(folder_id, MODE_CHAT)
    if not await _others_can_edit(user_id, folder):
        return None
    return FolderChoice(folder.id, MODE_CHAT, folder_name=folder.name)


async def instructions_hold(user_id: str, metadata: dict) -> FolderChoice | None:
    """The folder whose instructions this turn received and someone else can change, keeping it chat only.

    None when there is none, or the person chose that folder's project folder
    themselves.
    """
    return await _held_back_by_received_instructions(user_id, metadata, await load_choices(user_id))


async def choice_for_turn(user_id: str, metadata: dict) -> FolderChoice | None:
    """The project folder choice for the chat a turn belongs to, or None outside chat folders.

    A chat folder with no choice of its own or from a parent lets the AI choose.
    """
    chat_id = metadata.get('chat_id')
    if is_saved_chat_id(chat_id):
        folder_id = await Chats.get_chat_folder_id(chat_id, user_id)
    else:
        folder_id = metadata.get('folder_id')
    choices = await load_choices(user_id)
    held_back = await _held_back_by_received_instructions(user_id, metadata, choices)
    folder = await readable_folder(user_id, folder_id)
    if folder is None:
        return held_back
    own, inherited = await resolve_folder(user_id, folder, choices)
    if own is not None:
        return own
    if held_back is not None:
        return held_back
    if await has_shared_instructions(user_id, folder):
        return FolderChoice(folder.id, MODE_CHAT, folder_name=folder.name)
    if inherited is not None:
        return inherited
    return FolderChoice(folder.id, MODE_AUTO, folder_name=folder.name)


def turn_project_path(choice: FolderChoice, machine_id: str, machine_name: str, provider_label: str, revision) -> str:
    """The folder this turn starts in. Messages name the chat folder and computer, never the path:
    chat errors are stored and can end up in shared links."""
    if not choice.paths:
        raise SubscriptionError(
            f'The project folder for “{choice.folder_name}” was on a computer you removed. Choose one again '
            'in the folder\'s settings, or move this chat out of the folder.'
        )
    saved = choice.paths.get(machine_id)
    if saved is None:
        raise SubscriptionError(
            f'The chat folder “{choice.folder_name}” has a project folder on another computer, not on '
            f'{machine_name} where {provider_label} runs. Choose one for {machine_name} in the folder\'s '
            f'settings, or change where {provider_label} runs in Settings → Machines.'
        )
    if saved.revision != revision:
        raise SubscriptionError(
            f'{machine_name} was set up again after the project folder for “{choice.folder_name}” was '
            'chosen. Choose it again in the folder\'s settings.'
        )
    return saved.path


def missing_project_folder_message(choice: FolderChoice, machine_name: str) -> str:
    return (
        f'The project folder for “{choice.folder_name}” no longer exists on {machine_name}. '
        'Choose another in the folder\'s settings, or move this chat out of the folder.'
    )


def recent_paths(choices: dict[str, FolderChoice], machine_id: str, revision) -> list[str]:
    """Paths already chosen on this computer, newest first."""
    ordered = sorted(choices.values(), key=lambda choice: choice.updated_at, reverse=True)
    recent = []
    for choice in ordered:
        saved = choice.paths.get(machine_id)
        if saved is None or saved.revision != revision or saved.path in recent:
            continue
        recent.append(saved.path)
        if len(recent) == MAX_RECENT_PATHS:
            break
    return recent
