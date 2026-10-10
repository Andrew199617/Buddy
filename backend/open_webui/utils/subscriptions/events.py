"""Events a provider reports while it answers one chat turn."""

from dataclasses import dataclass


@dataclass
class TextDelta:
    """Reply text shown in the message."""

    text: str


@dataclass
class ReasoningDelta:
    """Thinking and agent activity shown in the collapsible reasoning block."""

    text: str


@dataclass
class StatusUpdate:
    """A short progress line shown above the reply."""

    description: str
    done: bool = False


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    def to_openai(self) -> dict:
        return {
            'prompt_tokens': self.input_tokens,
            'completion_tokens': self.output_tokens,
            'total_tokens': self.input_tokens + self.output_tokens,
            'prompt_tokens_details': {'cached_tokens': self.cached_input_tokens},
            'completion_tokens_details': {'reasoning_tokens': self.reasoning_tokens},
        }


@dataclass
class TurnFailed:
    message: str


class SubscriptionError(Exception):
    """A problem the user can act on, shown in the chat as the error text."""
