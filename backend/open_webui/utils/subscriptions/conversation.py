"""Turn Open WebUI chat messages into CLI turns, and remember CLI sessions.

The CLIs keep their own conversation history. Open WebUI sends the whole chat
on every request, so each provider looks up the CLI session whose history
matches everything before the newest user message. A match continues that
session; anything else (first turn, edited or regenerated messages, a restart)
starts a fresh session from the chat text.
"""

import hashlib
import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from open_webui.utils.subscriptions.events import SubscriptionError

log = logging.getLogger(__name__)

DETAILS_BLOCK = re.compile(r'<details[^>]*>.*?</details>', re.DOTALL | re.IGNORECASE)
CONTINUE_PROMPT = 'Continue your previous reply from where it stopped.'


@dataclass
class ChatTurn:
    role: str
    text: str
    images: list[str] = field(default_factory=list)


@dataclass
class Conversation:
    system: str
    history: list[ChatTurn]
    prompt: ChatTurn


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    texts = []
    for part in content:
        if isinstance(part, dict) and part.get('type') == 'text':
            texts.append(str(part.get('text') or ''))
    return '\n'.join(texts)


def content_images(content) -> list[str]:
    if not isinstance(content, list):
        return []
    images = []
    for part in content:
        if not isinstance(part, dict) or part.get('type') != 'image_url':
            continue
        image_url = part.get('image_url')
        if isinstance(image_url, dict):
            image_url = image_url.get('url')
        if isinstance(image_url, str) and image_url:
            images.append(image_url)
    return images


def _tool_calls_text(tool_calls) -> str:
    """Describe the Buddy tool calls an assistant message made."""
    lines = []
    for call in tool_calls or []:
        function = call.get('function') or {}
        arguments = function.get('arguments') or '{}'
        lines.append(f'[Called tool {function.get("name")} with {arguments}]')
    return '\n'.join(lines)


def parse_messages(messages: list[dict]) -> Conversation:
    """Split chat messages into instructions, earlier turns, and the new prompt.

    The CLIs only take user and assistant turns, so Buddy tool calls become
    part of the assistant's text and tool results become user turns.
    """
    system_parts = []
    turns = []
    tool_names = {}
    for message in messages or []:
        role = message.get('role')
        content = message.get('content')
        if role in ('system', 'developer'):
            text = content_text(content).strip()
            if text:
                system_parts.append(text)
            continue

        text = content_text(content)
        images = content_images(content)
        if role == 'assistant':
            for call in message.get('tool_calls') or []:
                tool_names[call.get('id')] = (call.get('function') or {}).get('name')
            text = DETAILS_BLOCK.sub('', text).strip()
            calls_text = _tool_calls_text(message.get('tool_calls'))
            text = '\n\n'.join(part for part in (text, calls_text) if part)
        elif role == 'tool':
            tool_name = message.get('name') or tool_names.get(message.get('tool_call_id')) or 'tool'
            text = f'[Result from tool {tool_name}]\n{text}'
            role = 'user'
        elif role != 'user':
            continue

        if not text.strip() and not images:
            continue
        turns.append(ChatTurn(role=role, text=text, images=images))

    if not turns:
        raise SubscriptionError('There is no message to send.')

    if turns[-1].role == 'user':
        prompt = turns[-1]
        history = turns[:-1]
    else:
        # "Continue response" ends the chat with the partial assistant reply.
        prompt = ChatTurn(role='user', text=CONTINUE_PROMPT)
        history = turns

    return Conversation(system='\n\n'.join(system_parts), history=history, prompt=prompt)


def _normalized_text(text: str) -> str:
    return ' '.join(DETAILS_BLOCK.sub('', text or '').split())


def _image_fingerprint(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def conversation_key(turns: list[ChatTurn], system: str = '') -> str:
    """Fingerprint a conversation state; equal chats give equal keys."""
    state = [_normalized_text(system)]
    for turn in turns:
        images = [_image_fingerprint(url) for url in turn.images]
        state.append([turn.role, _normalized_text(turn.text), images])
    encoded = json.dumps(state, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


class SessionStore:
    """Persisted map from conversation_key() to a CLI session id.

    Entries are kept in insertion order and the oldest are dropped first.
    """

    def __init__(self, path: Path, limit: int = 1000):
        self._path = path
        self._limit = limit
        self._entries: OrderedDict[str, str] = self._load()

    def _load(self) -> OrderedDict:
        try:
            pairs = json.loads(self._path.read_text(encoding='utf-8'))
            return OrderedDict((str(key), str(value)) for key, value in pairs)
        except FileNotFoundError:
            return OrderedDict()
        except (OSError, ValueError, TypeError) as error:
            log.warning('Ignoring unreadable session map %s: %s', self._path, error)
            return OrderedDict()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._path.with_suffix('.tmp')
            temporary_path.write_text(json.dumps(list(self._entries.items())), encoding='utf-8')
            os.replace(temporary_path, self._path)
        except OSError as error:
            log.warning('Could not save session map %s: %s', self._path, error)

    def get(self, key: str) -> str | None:
        return self._entries.get(key)

    def put(self, key: str, session_id: str) -> None:
        self._entries.pop(key, None)
        self._entries[key] = session_id
        while len(self._entries) > self._limit:
            self._entries.popitem(last=False)
        self._save()

    def clear(self) -> None:
        self._entries.clear()
        self._save()
