"""Claude models through the Claude Code CLI, billed to the user's Claude plan.

Each turn runs ``claude -p`` with stream-json input and output. Claude Code
signs in with its own ``claude auth login`` flow and stores its credentials in
its own config folder. Anthropic counts ``claude -p`` usage against the Claude
subscription's limits.
"""

from open_webui.utils.subscriptions.common import (
    ProviderModel,
    model_key,
)
from open_webui.utils.subscriptions.events import (
    ReasoningDelta,
    StatusUpdate,
    TextDelta,
    TokenUsage,
)

NOT_SIGNED_IN_MESSAGE = 'Claude is not signed in. Sign in under Admin Settings → Connections → Subscriptions.'


def _shorten(text, limit: int = 160) -> str:
    text = ' '.join(str(text or '').split())
    if len(text) > limit:
        return text[: limit - 1] + '…'
    return text


def describe_tool(name: str, tool_input: dict) -> str:
    """One line describing a Claude Code tool call."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if name in ('Bash', 'PowerShell'):
        return f'Running `{_shorten(tool_input.get("command"))}`'
    if name == 'Read':
        return f'Reading {_shorten(tool_input.get("file_path"))}'
    if name in ('Edit', 'MultiEdit', 'Write', 'NotebookEdit'):
        path = tool_input.get('file_path') or tool_input.get('notebook_path')
        return f'Editing {_shorten(path)}'
    if name == 'Grep':
        return f'Searching files for `{_shorten(tool_input.get("pattern"), 80)}`'
    if name == 'Glob':
        return f'Finding files matching `{_shorten(tool_input.get("pattern"), 80)}`'
    if name == 'WebSearch':
        return f'Searching the web for {_shorten(tool_input.get("query"), 100)}'
    if name == 'WebFetch':
        return f'Reading {_shorten(tool_input.get("url"), 120)}'
    if name in ('Task', 'Agent'):
        return f'Starting a subagent: {_shorten(tool_input.get("description"), 100)}'
    if name == 'TodoWrite':
        return 'Updating the task list'
    return f'Using {name}'


def _usage_from_result(usage: dict) -> TokenUsage:
    cache_read = int(usage.get('cache_read_input_tokens') or 0)
    cache_write = int(usage.get('cache_creation_input_tokens') or 0)
    uncached = int(usage.get('input_tokens') or 0)
    return TokenUsage(
        input_tokens=uncached + cache_read + cache_write,
        output_tokens=int(usage.get('output_tokens') or 0),
        cached_input_tokens=cache_read,
    )


def _readable_error(text: str) -> str:
    if 'not logged in' in text.lower() or '/login' in text:
        return NOT_SIGNED_IN_MESSAGE
    return text


class ClaudeStreamParser:
    """Turns Claude Code stream-json messages into turn events."""

    def __init__(self):
        self.session_id: str | None = None
        self.reply_text = ''
        self.error: str | None = None
        self.finished = False
        self.rate_limit: dict | None = None
        self._text_block_started = False
        self._reasoning_text = ''
        self._reasoning_block_started = False
        self._streamed_text_since_message = False

    def handle(self, message: dict) -> list:
        kind = message.get('type')
        if kind == 'system':
            return self._handle_system(message)
        if kind == 'stream_event':
            return self._handle_stream_event(message)
        if kind == 'assistant':
            return self._handle_assistant(message)
        if kind == 'result':
            return self._handle_result(message)
        if kind == 'rate_limit_event':
            self.rate_limit = message.get('rate_limit_info') or None
        return []

    def _text(self, text: str) -> list:
        if not text:
            return []
        if self._text_block_started:
            self._text_block_started = False
            if self.reply_text and not self.reply_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self.reply_text += text
        self._streamed_text_since_message = True
        return [TextDelta(text)]

    def _reasoning(self, text: str) -> list:
        if not text:
            return []
        if self._reasoning_block_started:
            self._reasoning_block_started = False
            if self._reasoning_text and not self._reasoning_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self._reasoning_text += text
        return [ReasoningDelta(text)]

    def _handle_system(self, message: dict) -> list:
        subtype = message.get('subtype')
        if subtype == 'init':
            self.session_id = message.get('session_id') or self.session_id
        elif subtype == 'api_retry':
            return [StatusUpdate('Claude is busy, retrying…')]
        elif subtype == 'compact_boundary':
            return [StatusUpdate('Summarized earlier messages to stay within the context window')]
        return []

    def _handle_stream_event(self, message: dict) -> list:
        if message.get('parent_tool_use_id'):
            return []
        event = message.get('event') or {}
        event_type = event.get('type')
        if event_type == 'message_start':
            self._streamed_text_since_message = False
        elif event_type == 'content_block_start':
            block_type = (event.get('content_block') or {}).get('type')
            if block_type == 'text':
                self._text_block_started = True
            elif block_type == 'thinking':
                self._reasoning_block_started = True
        elif event_type == 'content_block_delta':
            delta = event.get('delta') or {}
            if delta.get('type') == 'text_delta':
                return self._text(delta.get('text', ''))
            if delta.get('type') == 'thinking_delta':
                return self._reasoning(delta.get('thinking', ''))
        return []

    def _handle_assistant(self, message: dict) -> list:
        if message.get('parent_tool_use_id'):
            return []
        body = message.get('message') or {}
        blocks = body.get('content') or []
        if message.get('error'):
            texts = [block.get('text', '') for block in blocks if block.get('type') == 'text']
            self.error = _readable_error(' '.join(texts) or str(message['error']))
            return []

        events = []
        for block in blocks:
            if block.get('type') == 'text' and not self._streamed_text_since_message:
                # Partial messages were not streamed for this reply; use the full text.
                self._text_block_started = True
                events.extend(self._text(block.get('text', '')))
            elif block.get('type') == 'tool_use':
                description = describe_tool(block.get('name', ''), block.get('input'))
                events.append(StatusUpdate(description))
                self._reasoning_block_started = True
                events.extend(self._reasoning(f'*{description}*'))
        self._streamed_text_since_message = False
        return events

    def _handle_result(self, message: dict) -> list:
        self.finished = True
        self.session_id = message.get('session_id') or self.session_id
        if message.get('is_error') or message.get('subtype') != 'success':
            result_text = message.get('result') or message.get('subtype') or 'Claude Code reported an error.'
            if not self.error:
                self.error = _readable_error(str(result_text))
        usage = message.get('usage')
        if not isinstance(usage, dict):
            return []
        token_usage = _usage_from_result(usage)
        if token_usage.input_tokens == 0 and token_usage.output_tokens == 0:
            return []
        return [token_usage]


def models_from_initialize(entries: list) -> list[ProviderModel]:
    """Build the model list from Claude Code's initialize response."""
    models = []
    used_keys = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get('value')
        if not value or value == 'default':
            continue

        display_name = entry.get('displayName') or value
        segments = [segment.strip() for segment in str(entry.get('description') or '').split('·')]
        versioned_name = segments[0] or display_name
        # Per-token prices describe API billing, which does not apply to a plan.
        details = [segment for segment in segments[1:] if segment and '$' not in segment]

        key = model_key(display_name)
        if key in used_keys:
            key = model_key(value)
        used_keys.add(key)

        models.append(
            ProviderModel(
                key=key,
                value=value,
                name=f'Claude {versioned_name}',
                description=' · '.join(details),
                efforts=list(entry.get('supportedEffortLevels') or []),
            )
        )
    return models
