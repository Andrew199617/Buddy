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
    """A short progress line shown above the reply.

    The chat hides a reply's lines once it ends, unless one is marked keep:
    the latest such line then stays shown.
    """

    description: str
    done: bool = False
    keep: bool = False


WORKER_RUNNING = 'running'
WORKER_DONE = 'done'
WORKER_FAILED = 'failed'
WORKER_STOPPED = 'stopped'
WORKER_STATES = (WORKER_RUNNING, WORKER_DONE, WORKER_FAILED, WORKER_STOPPED)


@dataclass
class WorkerUpdate:
    """The latest state of one Ultra worker, listed under its chat with its own Stop."""

    worker_id: str
    # The worker that started this one; None for the orchestrator's own workers.
    parent_id: str | None
    # 1 for the orchestrator's workers, 2 for the sub-workers they start.
    depth: int
    label: str
    # One of WORKER_STATES.
    state: str
    model: str | None = None
    effort: str | None = None
    # What the worker is doing now, in one short line.
    activity: str | None = None
    # The worker's report once it ends.
    summary: str | None = None
    tokens: int | None = None


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
