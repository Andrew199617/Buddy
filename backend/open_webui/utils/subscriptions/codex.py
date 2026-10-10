"""OpenAI models through the Codex CLI, billed to the user's ChatGPT plan.

Buddy runs one ``codex app-server`` process and talks JSON-RPC to it over
stdio, the same interface the Codex IDE extensions use. Codex signs in with
"Sign in with ChatGPT" and keeps its credentials in its own config folder.

Chats use in-memory (ephemeral) Codex threads. A thread stays loaded while the
chat continues; any other state is rebuilt by injecting the chat's earlier
messages into a new thread.
"""

import json

from open_webui.utils.subscriptions.events import (
    ReasoningDelta,
    StatusUpdate,
    TextDelta,
    TokenUsage,
)


def readable_codex_error(message: str) -> str:
    """Codex forwards API errors as JSON text; keep only the human message."""
    text = str(message or '').strip()
    if text.startswith('{'):
        try:
            payload = json.loads(text)
        except ValueError:
            return text
        error = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get('message'):
            return str(error['message'])
        if isinstance(payload, dict) and payload.get('message'):
            return str(payload['message'])
    return text


def _shorten(text, limit: int = 160) -> str:
    text = ' '.join(str(text or '').split())
    if len(text) > limit:
        return text[: limit - 1] + '…'
    return text


def _usage_difference(total: dict | None, previous: dict | None) -> TokenUsage | None:
    if not isinstance(total, dict):
        return None
    previous = previous or {}

    def counted(name: str) -> int:
        return max(0, int(total.get(name) or 0) - int(previous.get(name) or 0))

    return TokenUsage(
        input_tokens=counted('inputTokens'),
        output_tokens=counted('outputTokens'),
        cached_input_tokens=counted('cachedInputTokens'),
        reasoning_tokens=counted('reasoningOutputTokens'),
    )


class CodexTurnParser:
    """Turns app-server notifications for one turn into turn events."""

    def __init__(self, turn_id: str):
        self.turn_id = turn_id
        self.reply_text = ''
        self.error: str | None = None
        self.finished = False
        self.usage_total: dict | None = None
        self._reasoning_text = ''
        self._streamed_message_ids = set()
        self._new_message = False
        self._new_reasoning = False

    def handle(self, message: dict) -> list:
        method = message.get('method')
        params = message.get('params') or {}
        turn_id = params.get('turnId') or (params.get('turn') or {}).get('id')
        if turn_id and turn_id != self.turn_id:
            return []

        if method == 'item/agentMessage/delta':
            self._streamed_message_ids.add(params.get('itemId'))
            return self._text(params.get('delta', ''))
        if method == 'item/reasoning/summaryTextDelta':
            return self._reasoning(params.get('delta', ''))
        if method == 'item/reasoning/textDelta':
            return self._reasoning(params.get('delta', ''))
        if method == 'item/reasoning/summaryPartAdded':
            self._new_reasoning = True
            return []
        if method == 'item/started':
            return self._item_started(params.get('item') or {})
        if method == 'item/completed':
            return self._item_completed(params.get('item') or {})
        if method == 'thread/tokenUsage/updated':
            self.usage_total = (params.get('tokenUsage') or {}).get('total')
            return []
        if method == 'error':
            return self._error(params)
        if method == 'turn/completed':
            return self._turn_completed(params.get('turn') or {})
        return []

    def _text(self, text: str) -> list:
        if not text:
            return []
        if self._new_message:
            self._new_message = False
            if self.reply_text and not self.reply_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self.reply_text += text
        return [TextDelta(text)]

    def _reasoning(self, text: str) -> list:
        if not text:
            return []
        if self._new_reasoning:
            self._new_reasoning = False
            if self._reasoning_text and not self._reasoning_text.endswith('\n\n'):
                text = '\n\n' + text.lstrip('\n')
        self._reasoning_text += text
        return [ReasoningDelta(text)]

    def _activity(self, description: str) -> list:
        self._new_reasoning = True
        return [StatusUpdate(description), *self._reasoning(f'*{description}*')]

    def _item_started(self, item: dict) -> list:
        item_type = item.get('type')
        if item_type == 'agentMessage':
            self._new_message = True
            return []
        if item_type == 'reasoning':
            self._new_reasoning = True
            return []
        if item_type == 'commandExecution':
            return self._activity(f'Running `{_shorten(item.get("command"))}`')
        if item_type == 'fileChange':
            return self._activity('Editing files')
        if item_type == 'webSearch':
            return self._activity(f'Searching the web for {_shorten(item.get("query"), 100)}')
        if item_type == 'mcpToolCall':
            return self._activity(f'Using {item.get("server")}.{item.get("tool")}')
        return []

    def _item_completed(self, item: dict) -> list:
        item_type = item.get('type')
        if item_type == 'agentMessage' and item.get('id') not in self._streamed_message_ids:
            # No deltas arrived for this message; show its full text.
            self._new_message = True
            return self._text(item.get('text', ''))
        if item_type == 'commandExecution' and item.get('exitCode') not in (None, 0):
            return self._reasoning(f' (exit code {item.get("exitCode")})')
        if item_type == 'fileChange':
            paths = [str(change.get('path')) for change in item.get('changes') or [] if change.get('path')]
            if paths:
                self._new_reasoning = True
                return self._reasoning('Edited ' + ', '.join(paths))
        return []

    def _error(self, params: dict) -> list:
        message = readable_codex_error((params.get('error') or {}).get('message'))
        if params.get('willRetry'):
            return [StatusUpdate(f'Retrying: {_shorten(message, 120)}')]
        self.error = message or 'Codex reported an error.'
        return []

    def _turn_completed(self, turn: dict) -> list:
        self.finished = True
        status = turn.get('status')
        if status == 'failed' and not self.error:
            error = turn.get('error') or {}
            self.error = readable_codex_error(error.get('message')) or 'The Codex turn failed.'
        elif status == 'interrupted' and not self.error:
            self.error = 'The reply was stopped.'
        return []
