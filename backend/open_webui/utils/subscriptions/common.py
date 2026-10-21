"""Settings and request shapes shared by the subscription providers."""

import re
from dataclasses import dataclass, field
from typing import Any

from open_webui.utils.subscriptions.conversation import Conversation

# ``owned_by`` of subscription models. Each runs on one user's own plan and
# machine, so only that user sees and uses it.
SUBSCRIPTION_OWNED_BY = 'subscription'

ACCESS_CHAT = 'chat'
ACCESS_READ = 'read'
ACCESS_FULL = 'full'
ACCESS_LEVELS = (ACCESS_CHAT, ACCESS_READ, ACCESS_FULL)

# How a provider signs in on the user's machines.
# The CLI's own sign-in on that machine, which must be a plan.
CREDENTIAL_MACHINE = 'machine'
# A one-year Claude token, from ``claude setup-token``, that Buddy keeps for the user.
CREDENTIAL_PLAN_TOKEN = 'plan_token'
# The user's own Anthropic or OpenAI API key, billed per use.
CREDENTIAL_API_KEY = 'api_key'
CREDENTIAL_MODES = (CREDENTIAL_MACHINE, CREDENTIAL_PLAN_TOKEN, CREDENTIAL_API_KEY)

# Set by the chat middleware to the chat folder whose own system prompt or
# knowledge files reached a turn. Built on the server, never taken from the request.
FOLDER_INSTRUCTIONS_KEY = 'folder_instructions_id'


def folder_adds_instructions(data) -> bool:
    """A chat folder's data adds a system prompt or knowledge files to its chats."""
    if not isinstance(data, dict):
        return False
    prompt = data.get('system_prompt')
    if isinstance(prompt, str) and prompt.strip():
        return True
    files = data.get('files')
    return isinstance(files, list) and len(files) > 0


# Shown to the model in chat-only mode, ahead of the chat's own system prompt.
CHAT_INSTRUCTIONS = (
    'You are chatting with the user in Buddy, their personal chat app. '
    'Reply conversationally and format answers with Markdown. '
    'You have no access to their files or terminal in this chat.'
)


@dataclass
class ProviderSettings:
    enable: bool = False
    access: str = ACCESS_CHAT
    workspace: str = ''
    cli_path: str = ''
    # Which machine runs the CLI: one of the user's computers, 'local' (this
    # server, for the instance owner), or '' before one is chosen.
    machine_id: str = 'local'


@dataclass
class ProviderModel:
    key: str
    value: str
    name: str
    description: str = ''
    efforts: list[str] = field(default_factory=list)
    vision: bool = True
    # The CLI on this computer can run Ultra turns with this model.
    ultra: bool = False


@dataclass(frozen=True)
class UltraOptions:
    """The person's Ultra choices for one turn, where the plan turn orchestrates workers.

    None for the model or effort means the workers use the chat's own.
    """

    worker_model: str | None = None
    # The worker model's display name, for the orchestrator's instructions.
    worker_label: str | None = None
    worker_effort: str | None = None
    # The admin's cap on workers at once; None means no limit.
    max_workers: int | None = None


@dataclass
class TurnRequest:
    model: str
    conversation: Conversation
    settings: ProviderSettings
    cwd: str
    effort: str | None = None
    is_task: bool = False
    # LocalMachine or RemoteMachine from subscriptions.machines.
    machine: Any = None
    # Model-entry reasoning levels travel with the request. Providers verify
    # them against the executing app-server before resolving the user's effort.
    efforts: list[str] = field(default_factory=list)
    # Set while the person has Ultra on. Providers read ``ultra``, not this field.
    ultra_options: UltraOptions | None = None

    @property
    def access(self) -> str:
        if self.is_task:
            return ACCESS_CHAT
        return self.settings.access

    @property
    def ultra(self) -> UltraOptions | None:
        # Background tasks, such as titles and follow-ups, never start workers.
        if self.is_task:
            return None
        return self.ultra_options


def model_key(text: str) -> str:
    """Lowercase id fragment such as 'opus' or 'gpt-6-astra'."""
    key = re.sub(r'[^a-z0-9.]+', '-', text.lower()).strip('-.')
    return key or 'model'


def requested_effort(payload: dict) -> str | None:
    """Read the reasoning level from the thinking chip or Chat Controls."""
    effort = payload.get('reasoning_effort')
    reasoning = payload.get('reasoning')
    if not effort and isinstance(reasoning, dict):
        effort = reasoning.get('effort')
    if not isinstance(effort, str):
        return None
    effort = effort.strip().lower()
    return effort or None
