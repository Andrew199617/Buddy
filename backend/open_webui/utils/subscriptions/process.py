"""Child processes for the subscription CLIs.

Output is read on background threads and handed to the event loop. This works
with both Windows event loops; the selector loop cannot run asyncio
subprocesses.
"""

import asyncio
import codecs
import collections
import logging
import os
import signal
import subprocess
import sys
import threading

log = logging.getLogger(__name__)

if sys.platform == 'win32':
    _CREATION_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    _CREATION_FLAGS = 0

# On POSIX each CLI leads its own process group, so stopping a reply also stops
# the commands it started (taskkill /T does this on Windows).
_NEW_PROCESS_GROUP = sys.platform != 'win32'

# A CLI started from inside another Claude Code session inherits variables that
# make it behave as a nested session. API keys and gateway URLs would switch it
# from subscription sign-in to API billing.
_REMOVED_ENV_PREFIXES = ('CLAUDE_CODE_', 'CLAUDE_AGENT_SDK_')
_KEPT_ENV_NAMES = {'CLAUDE_CODE_GIT_BASH_PATH'}
_REMOVED_ENV_NAMES = {
    'CLAUDECODE',
    'CLAUDE_PID',
    'ANTHROPIC_API_KEY',
    'ANTHROPIC_AUTH_TOKEN',
    'ANTHROPIC_BASE_URL',
    'OPENAI_API_KEY',
    'OPENAI_BASE_URL',
    'CODEX_API_KEY',
}


class ProcessClosedError(RuntimeError):
    pass


def subscription_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return this process's environment without session or API-key variables."""
    env = {}
    for name, value in os.environ.items():
        upper_name = name.upper()
        if upper_name in _REMOVED_ENV_NAMES:
            continue
        if upper_name.startswith(_REMOVED_ENV_PREFIXES) and upper_name not in _KEPT_ENV_NAMES:
            continue
        env[name] = value
    if extra:
        env.update(extra)
    return env


class StreamedOutput:
    """Stdout text that arrives in pieces, read as text or as whole lines.

    Subclasses put decoded text into ``_chunks`` and None when stdout ends.
    """

    def __init__(self):
        self._chunks: asyncio.Queue[str | None] = asyncio.Queue()
        self._pending_text = ''
        self._stdout_closed = False

    async def read_text(self, timeout: float | None = None) -> str | None:
        """Return the next piece of stdout text, or None once stdout is closed."""
        if self._pending_text:
            text, self._pending_text = self._pending_text, ''
            return text
        if self._stdout_closed:
            return None
        chunk = await asyncio.wait_for(self._chunks.get(), timeout)
        if chunk is None:
            self._stdout_closed = True
        return chunk

    async def read_line(self) -> str | None:
        """Return the next stdout line without its newline, or None at the end."""
        while '\n' not in self._pending_text:
            if self._stdout_closed:
                break
            chunk = await self._chunks.get()
            if chunk is None:
                self._stdout_closed = True
                break
            self._pending_text += chunk

        if '\n' in self._pending_text:
            line, _, self._pending_text = self._pending_text.partition('\n')
            return line.rstrip('\r')
        if self._pending_text:
            line, self._pending_text = self._pending_text, ''
            return line
        return None


class ChildProcess(StreamedOutput):
    """A CLI process with piped stdin, stdout, and stderr."""

    def __init__(self, args: list[str], cwd: str, env: dict[str, str], cleanup_paths: list[str] | None = None):
        super().__init__()
        self.args = args
        # Temporary files the command reads; removed when the process is stopped.
        self._cleanup_paths = list(cleanup_paths or [])
        self._loop = asyncio.get_running_loop()
        self._stderr_tail = collections.deque(maxlen=40)
        self.process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATION_FLAGS,
            start_new_session=_NEW_PROCESS_GROUP,
        )
        program = os.path.basename(args[0])
        self._stdout_reader = threading.Thread(target=self._read_stdout, name=f'{program}-stdout', daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, name=f'{program}-stderr', daemon=True)
        self._stdout_reader.start()
        self._stderr_reader.start()

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    def stderr_text(self) -> str:
        return '\n'.join(self._stderr_tail)

    def _post(self, item: str | None) -> None:
        try:
            self._loop.call_soon_threadsafe(self._chunks.put_nowait, item)
        except RuntimeError:
            # The event loop closed first; nobody is waiting for this output.
            pass

    def _read_stdout(self) -> None:
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                data = self.process.stdout.read1(65536)
                if not data:
                    break
                text = decoder.decode(data)
                if text:
                    self._post(text)
            tail = decoder.decode(b'', final=True)
            if tail:
                self._post(tail)
        except (OSError, ValueError) as error:
            log.debug('stdout reader stopped: %s', error)
        finally:
            self.process.stdout.close()
            self._post(None)

    def _read_stderr(self) -> None:
        try:
            for raw_line in self.process.stderr:
                line = raw_line.decode('utf-8', 'replace').rstrip()
                if line:
                    self._stderr_tail.append(line)
                    log.debug('%s: %s', os.path.basename(self.args[0]), line)
        except (OSError, ValueError) as error:
            log.debug('stderr reader stopped: %s', error)
        finally:
            self.process.stderr.close()

    def _write_blocking(self, text: str) -> None:
        try:
            self.process.stdin.write(text.encode('utf-8'))
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise ProcessClosedError(f'{os.path.basename(self.args[0])} is no longer running') from error

    async def write(self, text: str) -> None:
        await asyncio.to_thread(self._write_blocking, text)

    def close_stdin(self) -> None:
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass

    async def finish_output(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for the process to exit and its output to be read."""

        def join_readers():
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                return
            self._stdout_reader.join(timeout)
            self._stderr_reader.join(timeout)

        await asyncio.to_thread(join_readers)

    async def wait(self, timeout: float | None = None) -> int:
        try:
            return await asyncio.to_thread(self.process.wait, timeout)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(str(error)) from error

    def kill(self) -> None:
        """Stop the process and any commands it started, and remove its temporary files."""
        self._stop_process_tree()
        self.close_stdin()
        for path in self._cleanup_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._cleanup_paths = []

    def _stop_process_tree(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            if sys.platform == 'win32':
                subprocess.run(
                    ['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                    capture_output=True,
                    creationflags=_CREATION_FLAGS,
                    timeout=10,
                )
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError) as error:
            log.debug('Could not stop process %s: %s', self.process.pid, error)
            try:
                self.process.kill()
            except OSError:
                pass


async def run_command(args: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    """Run a short command and return its exit code, stdout, and stderr."""

    def run_blocking():
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATION_FLAGS,
        )

    try:
        result = await asyncio.to_thread(run_blocking)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f'{os.path.basename(args[0])} did not finish within {timeout:.0f} seconds') from error
    stdout = result.stdout.decode('utf-8', 'replace')
    stderr = result.stderr.decode('utf-8', 'replace')
    return result.returncode, stdout, stderr
