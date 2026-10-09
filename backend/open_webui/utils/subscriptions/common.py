"""Settings and request shapes shared by the subscription providers."""

import re
from dataclasses import dataclass, field

from open_webui.utils.subscriptions.conversation import Conversation

ACCESS_CHAT = 'chat'
ACCESS_READ = 'read'
ACCESS_FULL = 'full'
ACCESS_LEVELS = (ACCESS_CHAT, ACCESS_READ, ACCESS_FULL)

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


@dataclass
class ProviderModel:
    key: str
    value: str
    name: str
    description: str = ''
    efforts: list[str] = field(default_factory=list)
    vision: bool = True


@dataclass
class TurnRequest:
    model: str
    conversation: Conversation
    settings: ProviderSettings
    cwd: str
    effort: str | None = None
    is_task: bool = False

    @property
    def access(self) -> str:
        if self.is_task:
            return ACCESS_CHAT
        return self.settings.access


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
