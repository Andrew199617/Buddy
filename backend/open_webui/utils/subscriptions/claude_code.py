"""Claude models through the Claude Code CLI, billed to the user's Claude plan.

Each turn runs ``claude -p`` with stream-json input and output. Claude Code
signs in with its own ``claude auth login`` flow and stores its credentials in
its own config folder. Anthropic counts ``claude -p`` usage against the Claude
subscription's limits.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from open_webui.utils.subscriptions.common import (
    ACCESS_CHAT,
    ACCESS_READ,
    CHAT_INSTRUCTIONS,
    ProviderModel,
    ProviderSettings,
    TurnRequest,
    model_key,
)
from open_webui.utils.subscriptions.conversation import (
    ChatTurn,
    SessionStore,
    conversation_key,
    transcript_prompt,
)
from open_webui.utils.subscriptions.events import (
    ReasoningDelta,
    StatusUpdate,
    SubscriptionError,
    TextDelta,
    TokenUsage,
    TurnFailed,
)
from open_webui.utils.subscriptions.process import (
    ChildProcess,
    ProcessClosedError,
    run_command,
    subscription_env,
)

log = logging.getLogger(__name__)

PROVIDER_ID = 'claude'
NOT_INSTALLED_MESSAGE = (
    'Claude Code was not found. Install it from https://claude.com/claude-code '
    'or set its path in the Claude subscription settings.'
)
NOT_SIGNED_IN_MESSAGE = 'Claude is not signed in. Sign in under Admin Settings → Connections → Subscriptions.'
EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
READ_ONLY_TOOLS = 'Read,Grep,Glob,WebSearch,WebFetch'
LOGIN_URL = re.compile(r'https://\S+/oauth/authorize\?\S+')
FALLBACK_MODELS = [
    ProviderModel(key='opus', value='opus', name='Claude Opus'),
    ProviderModel(key='sonnet', value='sonnet', name='Claude Sonnet'),
    ProviderModel(key='haiku', value='haiku', name='Claude Haiku'),
]


def _desktop_bundle_version(path: Path) -> list[int]:
    version_name = path.parent.parent.name
    numbers = []
    for piece in version_name.split('.'):
        if piece.isdigit():
            numbers.append(int(piece))
        else:
            numbers.append(0)
    return numbers


def _newest_desktop_bundle() -> str | None:
    """The Claude desktop app ships the CLI under %APPDATA%/Claude/claude-code."""
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return None
    bundles = list((Path(appdata) / 'Claude' / 'claude-code').glob('*/*/claude.exe'))
    if not bundles:
        return None
    bundles.sort(key=_desktop_bundle_version)
    return str(bundles[-1])


def find_claude_cli(configured_path: str = '') -> str | None:
    if configured_path:
        if os.path.isfile(configured_path):
            return configured_path
        return None

    on_path = shutil.which('claude')
    if on_path and Path(on_path).suffix.lower() in ('', '.exe'):
        return on_path

    executable_name = 'claude.exe' if sys.platform == 'win32' else 'claude'
    native_install = Path.home() / '.local' / 'bin' / executable_name
    if native_install.is_file():
        return str(native_install)

    bundled = _newest_desktop_bundle()
    if bundled:
        return bundled

    # An npm install provides claude.cmd on Windows; it works, just more slowly.
    return on_path


def claude_effort(effort: str | None) -> str | None:
    if effort in ('none', 'minimal'):
        return 'low'
    if effort in EFFORT_LEVELS:
        return effort
    return None


def _image_block(url: str) -> dict | None:
    if url.startswith(('http://', 'https://')):
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    match = re.match(r'data:(image/[\w.+-]+);base64,(.*)', url, re.DOTALL)
    if not match:
        return None
    return {
        'type': 'image',
        'source': {'type': 'base64', 'media_type': match.group(1), 'data': match.group(2)},
    }


def user_message(text: str, images: list[str]) -> dict:
    content = [{'type': 'text', 'text': text}]
    for url in images:
        block = _image_block(url)
        if block:
            content.append(block)
    return {
        'type': 'user',
        'message': {'role': 'user', 'content': content},
        'parent_tool_use_id': None,
        'session_id': '',
    }


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


RATE_LIMIT_LABELS = {
    'five_hour': '5-hour',
    'seven_day': 'Weekly',
    'seven_day_opus': 'Weekly Opus',
    'seven_day_sonnet': 'Weekly Sonnet',
}


def rate_limit_window(info: dict) -> dict | None:
    """One usage window from a Claude Code rate_limit_event, when it has numbers."""
    utilization = info.get('utilization')
    if not isinstance(utilization, (int, float)):
        return None
    used_percent = utilization * 100 if utilization <= 1 else utilization
    resets_at = info.get('resetsAt')
    if isinstance(resets_at, (int, float)) and resets_at > 10_000_000_000:
        resets_at = resets_at / 1000
    return {
        'label': RATE_LIMIT_LABELS.get(info.get('rateLimitType'), 'Usage'),
        'used_percent': round(used_percent),
        'resets_at': resets_at,
    }


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


class ClaudeLogin:
    """A running ``claude auth login`` process and what the user should see."""

    def __init__(self, process: ChildProcess):
        self.process = process
        self.state = 'waiting'
        self.url: str | None = None
        self.message: str | None = None
        self.output = ''
        self.watcher: asyncio.Task | None = None

    def to_dict(self) -> dict:
        return {
            'state': self.state,
            'method': 'code',
            'url': self.url,
            'user_code': None,
            'needs_code': self.state == 'waiting',
            'message': self.message,
        }


class ClaudeCodeProvider:
    id = PROVIDER_ID
    name = 'Claude'

    def __init__(self, state_dir: Path, chat_dir: Path):
        self._chat_dir = chat_dir
        self._sessions = SessionStore(state_dir / 'claude-sessions.json')
        self._login: ClaudeLogin | None = None
        self._usage_windows: dict[str, dict] = {}
        self._limit_reached = False

    def _record_rate_limit(self, info: dict) -> None:
        self._limit_reached = info.get('status') == 'rejected'
        window = rate_limit_window(info)
        if window:
            self._usage_windows[window['label']] = window

    def usage_summary(self) -> dict | None:
        """Plan usage seen in recent replies; Claude Code reports it only while chatting."""
        if not self._usage_windows and not self._limit_reached:
            return None
        return {
            'plan': None,
            'windows': list(self._usage_windows.values()),
            'limit_reached': self._limit_reached,
        }

    def _cli_cwd(self) -> str:
        """Sign-in, status, and model checks run in Buddy's empty chat folder."""
        self._chat_dir.mkdir(parents=True, exist_ok=True)
        return str(self._chat_dir)

    def _require_cli(self, settings: ProviderSettings) -> str:
        cli = find_claude_cli(settings.cli_path)
        if not cli:
            raise SubscriptionError(NOT_INSTALLED_MESSAGE)
        return cli

    async def status(self, settings: ProviderSettings) -> dict:
        cli = find_claude_cli(settings.cli_path)
        if not cli:
            return {'installed': False, 'signed_in': False, 'message': NOT_INSTALLED_MESSAGE}

        version = ''
        try:
            _, version_output, _ = await run_command([cli, '--version'], self._cli_cwd(), subscription_env(), 30)
            version = version_output.strip().split(' ')[0]
        except (OSError, TimeoutError) as error:
            log.warning('Could not read the Claude Code version: %s', error)

        try:
            _, output, _ = await run_command(
                [cli, 'auth', 'status', '--json'], self._cli_cwd(), subscription_env(), 30
            )
            info = json.loads(output or '{}')
        except (OSError, TimeoutError, ValueError) as error:
            return {
                'installed': True,
                'cli_path': cli,
                'version': version,
                'signed_in': False,
                'message': f'Could not check the Claude sign-in: {error}',
            }

        auth_method = str(info.get('authMethod') or '')
        status = {
            'installed': True,
            'cli_path': cli,
            'version': version,
            'signed_in': bool(info.get('loggedIn')),
            'account': {
                'email': info.get('email') or info.get('emailAddress'),
                'organization': info.get('orgName') or info.get('organizationName'),
                'plan': info.get('subscriptionType') or info.get('plan'),
                'auth_method': auth_method,
            },
            'usage': self.usage_summary(),
        }
        if status['signed_in'] and ('console' in auth_method.lower() or 'api' in auth_method.lower()):
            status['message'] = (
                'Claude Code is signed in with an Anthropic Console account, which bills API usage '
                'instead of a Claude plan. Sign out and sign in with your Claude account.'
            )
        return status

    async def list_models(self, settings: ProviderSettings) -> list[ProviderModel]:
        cli = self._require_cli(settings)
        args = [
            cli,
            '-p',
            '--input-format',
            'stream-json',
            '--output-format',
            'stream-json',
            '--verbose',
            '--no-session-persistence',
            '--strict-mcp-config',
            '--setting-sources',
            '',
            '--tools',
            '',
        ]
        process = ChildProcess(args, self._cli_cwd(), subscription_env())
        initialize = {
            'type': 'control_request',
            'request_id': 'buddy-models',
            'request': {'subtype': 'initialize', 'hooks': None},
        }
        entries = []
        try:
            await process.write(json.dumps(initialize) + '\n')
            async with asyncio.timeout(30):
                while True:
                    line = await process.read_line()
                    if line is None:
                        break
                    message = _parse_json_line(line)
                    if message.get('type') == 'control_response':
                        response = (message.get('response') or {}).get('response') or {}
                        entries = response.get('models') or []
                        break
        except (ProcessClosedError, TimeoutError) as error:
            log.warning('Could not list Claude models: %s', error)
        finally:
            process.close_stdin()
            process.kill()

        models = models_from_initialize(entries)
        if models:
            return models
        return list(FALLBACK_MODELS)

    async def start_login(self, settings: ProviderSettings, method: str) -> dict:
        await self.cancel_login()
        cli = self._require_cli(settings)
        # BROWSER=none keeps the CLI from opening a tab on this computer; Buddy
        # shows the sign-in link instead, which also works from a phone.
        process = ChildProcess(
            [cli, 'auth', 'login', '--claudeai'],
            self._cli_cwd(),
            subscription_env({'BROWSER': 'none'}),
        )
        login = ClaudeLogin(process)
        self._login = login

        try:
            async with asyncio.timeout(30):
                while not login.url:
                    text = await process.read_text()
                    if text is None:
                        break
                    login.output += text
                    urls = LOGIN_URL.findall(login.output)
                    if urls:
                        login.url = urls[-1]
        except TimeoutError:
            pass

        if not login.url:
            process.kill()
            login.state = 'error'
            output = _shorten(login.output or process.stderr_text(), 400)
            login.message = output or 'Claude Code did not provide a sign-in link.'
            return login.to_dict()

        login.watcher = asyncio.create_task(self._watch_login(login))
        return login.to_dict()

    async def _watch_login(self, login: ClaudeLogin) -> None:
        while True:
            text = await login.process.read_text()
            if text is None:
                break
            login.output = (login.output + text)[-4000:]
        returncode = await login.process.wait()
        if login.state != 'waiting':
            return
        if returncode == 0:
            login.state = 'success'
            login.message = 'Signed in to Claude.'
        else:
            login.state = 'error'
            tail = login.output.split('Paste code here if prompted >')[-1]
            login.message = _shorten(tail or login.process.stderr_text(), 400) or 'Sign-in did not finish.'

    async def submit_login_code(self, code: str) -> dict:
        login = self._login
        if not login or login.state != 'waiting':
            raise SubscriptionError('There is no Claude sign-in waiting for a code. Start the sign-in again.')
        lines = code.strip().splitlines()
        code = lines[0].strip() if lines else ''
        if not code:
            raise SubscriptionError('Paste the code shown after you approve the sign-in.')
        await login.process.write(code + '\n')
        try:
            await asyncio.wait_for(asyncio.shield(login.watcher), 60)
        except TimeoutError:
            login.message = 'Still waiting for Claude Code to finish signing in.'
        return login.to_dict()

    def login_state(self) -> dict:
        if not self._login:
            return {'state': 'idle'}
        return self._login.to_dict()

    async def cancel_login(self) -> None:
        login = self._login
        self._login = None
        if not login:
            return
        login.state = 'cancelled'
        login.process.kill()
        if login.watcher:
            login.watcher.cancel()

    async def logout(self, settings: ProviderSettings) -> None:
        cli = self._require_cli(settings)
        await self.cancel_login()
        returncode, _, error_output = await run_command(
            [cli, 'auth', 'logout'], self._cli_cwd(), subscription_env(), 60
        )
        self._sessions.clear()
        if returncode != 0:
            raise SubscriptionError(_shorten(error_output, 400) or 'Claude Code could not sign out.')

    def _turn_args(self, cli: str, turn: TurnRequest, resume_id: str | None, instructions_path: str) -> list[str]:
        args = [
            cli,
            '-p',
            '--input-format',
            'stream-json',
            '--output-format',
            'stream-json',
            '--verbose',
            '--include-partial-messages',
            '--permission-prompts',
            'none',
            '--system-prompt-snapshot',
            'off',
            '--model',
            turn.model,
        ]

        effort = claude_effort(turn.effort)
        if turn.is_task:
            effort = 'low'
        if effort:
            args.extend(['--effort', effort])

        if turn.is_task:
            args.append('--no-session-persistence')
        if resume_id:
            args.extend(['--resume', resume_id, '--fork-session'])

        if turn.access == ACCESS_CHAT:
            args.extend(['--system-prompt-file', instructions_path])
            args.extend(['--strict-mcp-config', '--disable-slash-commands'])
            args.extend(['--setting-sources', ''])
            args.extend(['--tools', ''])
        elif turn.access == ACCESS_READ:
            # The working folder may be someone else's repository. Its
            # .claude/settings.json hooks and .mcp.json servers would run as
            # this user, so read mode loads no settings files, MCP servers, or
            # skills, and --restricted keeps file tools inside the folder.
            args.extend(['--append-system-prompt-file', instructions_path])
            args.append('--restricted')
            args.extend(['--strict-mcp-config', '--disable-slash-commands'])
            args.extend(['--setting-sources', ''])
            args.extend(['--tools', READ_ONLY_TOOLS])
            args.extend(['--allowedTools', 'WebSearch,WebFetch'])
        else:
            args.extend(['--append-system-prompt-file', instructions_path])
            args.append('--dangerously-skip-permissions')
        return args

    def _instructions(self, turn: TurnRequest) -> str:
        system = turn.conversation.system
        if turn.access == ACCESS_CHAT:
            return f'{CHAT_INSTRUCTIONS}\n\n{system}'.strip()
        return system or 'The user is chatting with you from Buddy, their personal chat app.'

    def _session_key(self, turn: TurnRequest, turns: list[ChatTurn]) -> str:
        # Claude Code keeps sessions per working folder, so a session can only
        # be resumed from the folder that created it.
        return f'{turn.cwd}|{conversation_key(turns)}'

    def _write_instructions(self, turn: TurnRequest) -> str:
        instructions_file = tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', suffix='.md', prefix='buddy-instructions-', delete=False
        )
        with instructions_file:
            instructions_file.write(self._instructions(turn))
        return instructions_file.name

    async def _run_attempt(
        self,
        cli: str,
        turn: TurnRequest,
        resume_id: str | None,
        prompt_text: str,
        instructions_path: str,
        attempt: 'ClaudeAttempt',
    ):
        """Run ``claude -p`` once, yielding events and recording the outcome in ``attempt``."""
        parser = attempt.parser
        process = ChildProcess(self._turn_args(cli, turn, resume_id, instructions_path), turn.cwd, subscription_env())
        try:
            await process.write(json.dumps(user_message(prompt_text, turn.conversation.prompt.images)) + '\n')
            while not parser.finished:
                line = await process.read_line()
                if line is None:
                    break
                for event in parser.handle(_parse_json_line(line)):
                    yield event
                if parser.rate_limit:
                    self._record_rate_limit(parser.rate_limit)
                    parser.rate_limit = None
        except ProcessClosedError:
            attempt.exited_early = True
        finally:
            process.close_stdin()
            if parser.error or not parser.finished:
                # The CLI is exiting with an error; it explains why on stderr.
                await process.finish_output(5)
            process.kill()
            attempt.stderr = process.stderr_text()

    async def run_turn(self, turn: TurnRequest):
        """Run one chat turn and yield its events."""
        cli = self._require_cli(turn.settings)
        conversation = turn.conversation
        resume_id = None
        if not turn.is_task:
            resume_id = self._sessions.get(self._session_key(turn, conversation.history))

        instructions_path = self._write_instructions(turn)
        attempt = ClaudeAttempt()
        try:
            if resume_id:
                prompt_text = conversation.prompt.text
                async for event in self._run_attempt(cli, turn, resume_id, prompt_text, instructions_path, attempt):
                    yield event
                if attempt.session_missing():
                    log.info('Claude session %s no longer exists; starting from the chat text', resume_id)
                    resume_id = None
                    attempt = ClaudeAttempt()

            if not resume_id:
                prompt_text = transcript_prompt(conversation.history, conversation.prompt.text)
                async for event in self._run_attempt(cli, turn, None, prompt_text, instructions_path, attempt):
                    yield event

            failure = attempt.failure()
            if failure:
                yield TurnFailed(failure)
            elif attempt.parser.session_id and not turn.is_task:
                reply = ChatTurn('assistant', attempt.parser.reply_text)
                answered = conversation.history + [conversation.prompt, reply]
                self._sessions.put(self._session_key(turn, answered), attempt.parser.session_id)
        finally:
            try:
                os.unlink(instructions_path)
            except OSError:
                pass


class ClaudeAttempt:
    """What one ``claude -p`` run produced."""

    def __init__(self):
        self.parser = ClaudeStreamParser()
        self.stderr = ''
        self.exited_early = False

    def session_missing(self) -> bool:
        """The resumed session was deleted or belongs to another folder."""
        if self.parser.reply_text:
            return False
        output = f'{self.stderr} {self.parser.error or ""}'.lower()
        return 'no conversation found' in output

    def failure(self) -> str | None:
        details = _shorten(self.stderr, 600)
        if self.parser.error:
            # Results such as "error_during_execution" explain themselves on stderr.
            if self.parser.error.startswith('error_') and details:
                return details
            return self.parser.error
        if self.exited_early:
            return f'Claude Code exited unexpectedly. {details}'.strip()
        if not self.parser.finished:
            return f'Claude Code stopped before finishing the reply. {details}'.strip()
        return None


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


def _parse_json_line(line: str) -> dict:
    line = line.strip()
    if not line.startswith('{'):
        return {}
    try:
        message = json.loads(line)
    except ValueError:
        return {}
    if isinstance(message, dict):
        return message
    return {}
