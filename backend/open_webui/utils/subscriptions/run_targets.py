"""Where a chat runs: reading a requested target, and working out the default one.

Pure rules with no storage and no computers contacted. The service checks a
request against the person's computers and saves the result for the chat.

A chat runs either on one of the person's computers, in an exact folder or
in a start folder where the AI chooses, or Detached: chat only, on whichever
computer is available, with no folder.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from open_webui.utils.subscriptions.common import ACCESS_CHAT, ACCESS_LEVELS
from open_webui.utils.subscriptions.events import SubscriptionError

KIND_COMPUTER = 'computer'
KIND_DETACHED = 'detached'

FOLDER_PATH = 'path'
FOLDER_AUTO = 'auto'

# A chat folder's modes, as project_folders stores them.
CHOICE_AI_CHOOSES = 'auto'
CHOICE_EXACT_FOLDER = 'folder'
CHOICE_NO_FILES = 'chat'

# Where a default came from, for the bar to explain it.
FROM_CHAT_FOLDER = 'chat_folder'
FROM_THIS_PC = 'this_pc'
FROM_LAST_USED = 'last_used'
FROM_PLAN = 'plan'
FROM_FIRST_READY = 'first_ready'
FROM_NONE = 'none'

# Why a requested target was not used; the chat then runs Detached, chat only.
REFUSED_COMPUTER_REMOVED = 'computer_removed'
REFUSED_COMPUTER_RESET = 'computer_reset'
REFUSED_SERVER_OFF = 'server_off'
REFUSED_BAD_PATH = 'bad_path'
REFUSED_NEEDS_ACKNOWLEDGEMENT = 'needs_acknowledgement'

REFUSAL_MESSAGES = {
    REFUSED_COMPUTER_REMOVED: 'That computer is no longer one of yours, so this chat runs Detached.',
    REFUSED_COMPUTER_RESET: 'That computer was set up again, so this chat runs Detached.',
    REFUSED_SERVER_OFF: 'Buddy server is not available to your account, so this chat runs Detached.',
    REFUSED_BAD_PATH: "That folder can't be used, so this chat runs Detached.",
    REFUSED_NEEDS_ACKNOWLEDGEMENT: (
        "This chat folder's instructions can be changed by someone else, so this chat runs Detached."
    ),
}


class FolderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    mode: Literal['path', 'auto']
    path: Optional[str] = None


class RunTargetRequest(BaseModel):
    """What the composer's bar sends when a chat starts."""

    model_config = ConfigDict(extra='forbid')

    kind: Literal['computer', 'detached']
    machine_id: Optional[str] = None
    # The computer's revision the bar showed; compared, never stored as sent.
    expected_revision: Optional[str] = None
    folder: Optional[FolderRequest] = None
    access: Literal['chat', 'read', 'full'] = ACCESS_CHAT
    # Set when the person chose to use files despite a chat folder's shared instructions.
    acknowledged_instructions_folder_id: Optional[str] = None


def parse_request(value) -> RunTargetRequest:
    """A requested target in its checked shape. Detached is always chat only, with no folder."""
    try:
        request = RunTargetRequest.model_validate(value)
    except ValidationError:
        raise SubscriptionError('Choose where this chat runs again.')
    if request.kind == KIND_DETACHED:
        return RunTargetRequest(kind=KIND_DETACHED, access=ACCESS_CHAT)
    if not request.machine_id:
        raise SubscriptionError('Choose a computer for this chat.')
    if request.folder is None or request.folder.mode == FOLDER_AUTO:
        # No folder chosen lets the AI choose, from the computer's start folder.
        return request.model_copy(update={'folder': FolderRequest(mode=FOLDER_AUTO)})
    if not request.folder.path:
        raise SubscriptionError('Choose a folder for this chat.')
    return request


def lowest_access(first: str, second: str) -> str:
    """The lower of two access levels; anything unknown counts as chat only."""
    if first not in ACCESS_LEVELS or second not in ACCESS_LEVELS:
        return ACCESS_CHAT
    return ACCESS_LEVELS[min(ACCESS_LEVELS.index(first), ACCESS_LEVELS.index(second))]


@dataclass(frozen=True)
class DefaultComputer:
    machine_id: str
    ready: bool


@dataclass(frozen=True)
class DefaultFolderChoice:
    """A chat folder's defaults for new chats, own or followed from a parent."""

    mode: str
    machine_id: str
    access: str
    # Exact folders per computer, for the "folder" mode.
    paths: dict[str, str]


@dataclass(frozen=True)
class DefaultInputs:
    folder_choice: Optional[DefaultFolderChoice]
    # The chat folder's instructions can be changed by someone else.
    shared_instructions: bool
    # The computers the person can run on, in the order they are listed.
    computers: tuple[DefaultComputer, ...]
    # The computer showing the page, when it is one of them.
    this_machine_id: Optional[str]
    # The computer the person's most recent chat ran on.
    last_used_machine_id: Optional[str]
    # The computer the person's plans ran on before chats chose their own.
    plan_machine_id: Optional[str]


@dataclass(frozen=True)
class RunTargetDefault:
    kind: str
    machine_id: Optional[str]
    folder_mode: Optional[str]
    path: Optional[str]
    # None means the plan's access.
    access: Optional[str]
    came_from: str
    # The shared instructions keep it chat only until the person acknowledges them.
    access_locked: bool


def _detached(came_from: str, shared_instructions: bool) -> RunTargetDefault:
    return RunTargetDefault(KIND_DETACHED, None, None, None, ACCESS_CHAT, came_from, shared_instructions)


def _known(inputs: DefaultInputs, machine_id: Optional[str]) -> bool:
    return any(computer.machine_id == machine_id for computer in inputs.computers)


def _computer_for_auto_folder(inputs: DefaultInputs) -> tuple[Optional[str], str]:
    """A computer for a chat folder that lets the AI choose but names no computer."""
    if _known(inputs, inputs.this_machine_id):
        return inputs.this_machine_id, FROM_THIS_PC
    if _known(inputs, inputs.last_used_machine_id):
        return inputs.last_used_machine_id, FROM_LAST_USED
    if _known(inputs, inputs.plan_machine_id):
        return inputs.plan_machine_id, FROM_PLAN
    for computer in inputs.computers:
        if computer.ready:
            return computer.machine_id, FROM_FIRST_READY
    return None, FROM_NONE


def _computer_for_exact_folder(inputs: DefaultInputs, choice: DefaultFolderChoice) -> Optional[str]:
    """A computer that has the chat folder's exact folder, preferring the folder's own and then this one."""
    if choice.machine_id in choice.paths and _known(inputs, choice.machine_id):
        return choice.machine_id
    if inputs.this_machine_id in choice.paths and _known(inputs, inputs.this_machine_id):
        return inputs.this_machine_id
    if inputs.plan_machine_id in choice.paths and _known(inputs, inputs.plan_machine_id):
        return inputs.plan_machine_id
    for computer in inputs.computers:
        if computer.machine_id in choice.paths:
            return computer.machine_id
    return None


def _from_folder_choice(inputs: DefaultInputs, choice: DefaultFolderChoice) -> RunTargetDefault:
    access = choice.access or None
    if inputs.shared_instructions:
        access = ACCESS_CHAT
    if choice.mode == CHOICE_NO_FILES:
        return _detached(FROM_CHAT_FOLDER, inputs.shared_instructions)
    if choice.mode == CHOICE_EXACT_FOLDER:
        machine_id = _computer_for_exact_folder(inputs, choice)
        if machine_id is None:
            return _detached(FROM_NONE, inputs.shared_instructions)
        return RunTargetDefault(
            KIND_COMPUTER, machine_id, FOLDER_PATH, choice.paths[machine_id], access, FROM_CHAT_FOLDER,
            inputs.shared_instructions,
        )
    machine_id = None
    came_from = FROM_CHAT_FOLDER
    if _known(inputs, choice.machine_id):
        machine_id = choice.machine_id
    else:
        machine_id, came_from = _computer_for_auto_folder(inputs)
    if machine_id is None:
        return _detached(FROM_NONE, inputs.shared_instructions)
    return RunTargetDefault(
        KIND_COMPUTER, machine_id, FOLDER_AUTO, None, access, came_from, inputs.shared_instructions
    )


def choose_default(inputs: DefaultInputs, started_before_targets: bool = False) -> RunTargetDefault:
    """Where a chat runs when nobody chose: its chat folder's defaults, or Detached.

    A chat started before chats had targets keeps running where its plan ran,
    letting the AI choose the folder, as it did then.
    """
    if inputs.folder_choice is not None:
        return _from_folder_choice(inputs, inputs.folder_choice)
    if started_before_targets and _known(inputs, inputs.plan_machine_id):
        access = None
        if inputs.shared_instructions:
            access = ACCESS_CHAT
        return RunTargetDefault(
            KIND_COMPUTER, inputs.plan_machine_id, FOLDER_AUTO, None, access, FROM_PLAN, inputs.shared_instructions
        )
    return _detached(FROM_NONE, inputs.shared_instructions)
