"""What a person keeps for each of their computers: where plans start, and which CLI each plan runs.

Stored under ``subscriptions.users.{id}.computers``::

    {"version": 1, "computers": {"<machine id>": {
        "revision": "...", "start_folder": "", "cli_paths": {"claude": "", "codex": ""}}}}

An entry counts only while its revision is the computer's current one, so a
computer that is set up again starts with nothing saved.
"""

from dataclasses import dataclass, field

from open_webui.utils.subscriptions.events import SubscriptionError
from open_webui.utils.subscriptions.project_paths import MAX_PATH_CHARS

VERSION = 1


@dataclass(frozen=True)
class ComputerSettings:
    revision: str
    start_folder: str = ''
    cli_paths: dict[str, str] = field(default_factory=dict)

    def cli_path(self, provider_id: str) -> str:
        return self.cli_paths.get(provider_id, '')


def settings_key(user_id: str) -> str:
    return f'subscriptions.users.{user_id}.computers'


def _parse_entry(value) -> ComputerSettings | None:
    if not isinstance(value, dict):
        return None
    revision = value.get('revision')
    if not isinstance(revision, str) or not revision:
        return None
    start_folder = value.get('start_folder')
    if not isinstance(start_folder, str):
        start_folder = ''
    cli_paths = {}
    stored_paths = value.get('cli_paths')
    if isinstance(stored_paths, dict):
        for provider_id, path in stored_paths.items():
            if isinstance(provider_id, str) and isinstance(path, str) and path:
                cli_paths[provider_id] = path
    return ComputerSettings(revision, start_folder, cli_paths)


def parse(value) -> dict[str, ComputerSettings]:
    """Anything unreadable reads as nothing saved."""
    computers = value.get('computers') if isinstance(value, dict) else None
    if not isinstance(computers, dict):
        return {}
    parsed = {}
    for machine_id, entry in computers.items():
        settings = _parse_entry(entry)
        if isinstance(machine_id, str) and settings is not None:
            parsed[machine_id] = settings
    return parsed


def serialize(computers: dict[str, ComputerSettings]) -> dict:
    stored = {}
    for machine_id, settings in computers.items():
        stored[machine_id] = {
            'revision': settings.revision,
            'start_folder': settings.start_folder,
            'cli_paths': dict(settings.cli_paths),
        }
    return {'version': VERSION, 'computers': stored}


def current(computers: dict[str, ComputerSettings], machine_id: str, revision: str | None) -> ComputerSettings | None:
    """The computer's entry, unless it was saved before the computer was set up again."""
    entry = computers.get(machine_id)
    if entry is None or revision is None or entry.revision != revision:
        return None
    return entry


def clean_cli_path(path) -> str:
    """A CLI path as typed, or '' to let Buddy find the CLI. Its computer checks that it exists."""
    if path is None:
        return ''
    if not isinstance(path, str):
        raise SubscriptionError("Enter the CLI's full path.")
    path = path.strip()
    if len(path) > MAX_PATH_CHARS:
        raise SubscriptionError('That path is too long.')
    if any(ord(character) < 32 for character in path):
        raise SubscriptionError("That path has characters Buddy can't use.")
    return path
