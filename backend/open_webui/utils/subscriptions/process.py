"""Child processes for the subscription CLIs.

Output is read on background threads and handed to the event loop. This works
with both Windows event loops; the selector loop cannot run asyncio
subprocesses.
"""

import asyncio
import codecs
import logging
import os
import signal
import stat
import subprocess
import sys
import threading
import time

from open_webui.utils.subscriptions.windows_job import spawn_in_job

log = logging.getLogger(__name__)

MAX_CAPTURE_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_PENDING_CHUNKS = 128
READ_BYTES = 65536


def _posix_group_has_live_processes(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    if not sys.platform.startswith('linux') or not os.path.isdir('/proc'):
        return True
    # Orphaned zombies can remain until the host init/subreaper collects them.
    # They cannot execute or retain pipes, so distinguish them from live members.
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                with open(os.path.join(entry.path, 'stat'), encoding='ascii') as status:
                    fields = status.read().rsplit(')', 1)[1].split()
                if int(fields[2]) == group_id and fields[0] not in ('Z', 'X'):
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (PermissionError, IndexError, ValueError):
                # If the host hides a process, do not claim verified cleanup.
                return True
    return False


def _stop_posix_group(group_id: int) -> None:
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2.0
    while _posix_group_has_live_processes(group_id):
        if time.monotonic() >= deadline:
            raise TimeoutError('The CLI process group did not finish terminating')
        time.sleep(0.01)

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


class OutputLimitError(ProcessClosedError):
    """A command produced more output than the caller can safely buffer."""


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
        self._output_error: OutputLimitError | None = None
        self._output_limit_bytes = MAX_CAPTURE_BYTES

    def _raise_output_error(self) -> None:
        if self._output_error is not None:
            raise self._output_error

    def _consume_chunk(self, chunk: str | None) -> None:
        pass

    def _set_output_error(self, error: OutputLimitError) -> None:
        self._output_error = error

    async def _next_chunk(self, timeout: float | None = None) -> str | None:
        self._raise_output_error()
        chunk = await asyncio.wait_for(self._chunks.get(), timeout)
        self._consume_chunk(chunk)
        self._raise_output_error()
        return chunk

    async def read_text(self, timeout: float | None = None) -> str | None:
        """Return the next piece of stdout text, or None once stdout is closed."""
        self._raise_output_error()
        if self._pending_text:
            text, self._pending_text = self._pending_text, ''
            return text
        if self._stdout_closed:
            return None
        chunk = await self._next_chunk(timeout)
        if chunk is None:
            self._stdout_closed = True
        return chunk

    async def read_line(self) -> str | None:
        """Return the next stdout line without its newline, or None at the end."""
        self._raise_output_error()
        while '\n' not in self._pending_text:
            if self._stdout_closed:
                break
            chunk = await self._next_chunk()
            if chunk is None:
                self._stdout_closed = True
                break
            self._pending_text += chunk
            if len(self._pending_text.encode('utf-8')) > self._output_limit_bytes:
                error = OutputLimitError('A CLI output line exceeded its buffer limit')
                self._set_output_error(error)
                raise error

        if '\n' in self._pending_text:
            line, _, self._pending_text = self._pending_text.partition('\n')
            return line.rstrip('\r')
        if self._pending_text:
            line, self._pending_text = self._pending_text, ''
            return line
        return None


class ChildProcess(StreamedOutput):
    """A contained CLI process with piped stdin and bounded output buffers.

    ``output_limit_bytes`` caps pending stdout before event-loop callbacks are
    queued. Split stderr remains the default for provider JSON protocols.
    """

    def __init__(
        self, args: list[str], cwd: str, env: dict[str, str], cleanup_paths: list[str] | None = None,
        *, merge_stderr: bool = False, output_limit_bytes: int = MAX_CAPTURE_BYTES,
        _capture_output: bool = False, cwd_fd: int | None = None,
    ):
        super().__init__()
        if isinstance(output_limit_bytes, bool) or not isinstance(output_limit_bytes, int) or output_limit_bytes <= 0:
            raise ValueError('output_limit_bytes must be a positive integer')
        self.args = args
        self._cleanup_paths = list(cleanup_paths or [])
        self._loop = asyncio.get_running_loop()
        self._output_limit_bytes = output_limit_bytes
        self._queue_lock = threading.Lock()
        self._queued_bytes = 0
        self._queued_chunks = 0
        self._eof_posted = False
        self._stderr_lock = threading.Lock()
        self._stderr_tail = bytearray()
        self._stderr_total_bytes = 0
        self._stdout_total_bytes = 0
        self._capture_output = _capture_output
        self._stop_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._tree_stopped = False
        self._disposal_error: BaseException | None = None
        self._cleanup_done = threading.Event()
        self._job = None
        if sys.platform == 'win32':
            if cwd_fd is not None:
                raise ValueError('cwd_fd is only supported on POSIX hosts')
            self.process, self._job = spawn_in_job(args, cwd, env, merge_stderr=merge_stderr)
        else:
            spawn_cwd = cwd
            inherited_fds = ()
            if cwd_fd is not None:
                if isinstance(cwd_fd, bool) or not isinstance(cwd_fd, int) or cwd_fd < 0:
                    raise ValueError('cwd_fd must be an open directory descriptor')
                if not stat.S_ISDIR(os.fstat(cwd_fd).st_mode):
                    raise ValueError('cwd_fd must refer to a directory')
                for descriptor_root in ('/proc/self/fd', '/dev/fd'):
                    descriptor_path = os.path.join(descriptor_root, str(cwd_fd))
                    if os.path.isdir(descriptor_path):
                        spawn_cwd = descriptor_path
                        inherited_fds = (cwd_fd,)
                        break
                else:
                    raise OSError('This host cannot spawn from a pinned directory descriptor')
            stderr = subprocess.STDOUT if merge_stderr else subprocess.PIPE
            self.process = subprocess.Popen(
                args, cwd=spawn_cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=stderr, start_new_session=True,
                pass_fds=inherited_fds,
            )
        program = os.path.basename(args[0])
        self._stdout_reader = threading.Thread(target=self._read_stdout, name=f'{program}-stdout', daemon=True)
        self._stderr_reader = None
        if self.process.stderr is not None:
            self._stderr_reader = threading.Thread(target=self._read_stderr, name=f'{program}-stderr', daemon=True)
        self._monitor = threading.Thread(target=self._watch_leader, name=f'{program}-cleanup', daemon=True)
        try:
            self._stdout_reader.start()
            if self._stderr_reader is not None:
                self._stderr_reader.start()
            self._monitor.start()
        except BaseException:
            # Reader/monitor thread startup failure must not leave a contained
            # process alive merely because construction did not return it.
            self.kill()
            self.process.wait(timeout=2)
            for reader in (self._stdout_reader, self._stderr_reader):
                if reader is not None and reader.ident is not None:
                    reader.join(2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()
            raise

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    @property
    def cleanup_confirmed(self) -> bool:
        readers_stopped = not self._stdout_reader.is_alive()
        if self._stderr_reader is not None:
            readers_stopped = readers_stopped and not self._stderr_reader.is_alive()
        return (
            self._tree_stopped and self._disposal_error is None
            and self._cleanup_done.is_set() and not self._monitor.is_alive()
            and readers_stopped and not self._cleanup_paths
        )

    def stderr_text(self) -> str:
        return self._stderr_snapshot().decode('utf-8', 'replace').rstrip()

    def _stderr_snapshot(self) -> bytes:
        with self._stderr_lock:
            return bytes(self._stderr_tail)

    def _consume_chunk(self, chunk: str | None) -> None:
        if chunk is not None:
            with self._queue_lock:
                self._queued_bytes -= len(chunk.encode('utf-8'))
                self._queued_chunks -= 1

    def _set_output_error(self, error: OutputLimitError) -> None:
        if self._output_error is None:
            self._output_error = error
            self._post(None)
            self.kill()

    def _post(self, item: str | None) -> None:
        size = 0
        exceeded = False
        with self._queue_lock:
            if item is None:
                if self._eof_posted:
                    return
                self._eof_posted = True
            else:
                if self._output_error is not None:
                    return
                size = len(item.encode('utf-8'))
                if self._queued_bytes + size > self._output_limit_bytes or self._queued_chunks >= MAX_PENDING_CHUNKS:
                    exceeded = True
                else:
                    self._queued_bytes += size
                    self._queued_chunks += 1
        if exceeded:
            self._set_output_error(OutputLimitError('The CLI stdout buffer limit was reached'))
            return
        try:
            self._loop.call_soon_threadsafe(self._chunks.put_nowait, item)
        except RuntimeError:
            if item is not None:
                self._consume_chunk(item)

    def _read_stdout(self) -> None:
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                data = self.process.stdout.read1(READ_BYTES)
                if not data:
                    break
                self._stdout_total_bytes += len(data)
                if self._capture_output and self._stdout_total_bytes > MAX_CAPTURE_BYTES:
                    self._set_output_error(OutputLimitError('The command stdout capture limit was reached'))
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
            while True:
                data = self.process.stderr.read1(READ_BYTES)
                if not data:
                    break
                self._stderr_total_bytes += len(data)
                if self._capture_output and self._stderr_total_bytes > MAX_CAPTURE_BYTES:
                    self._set_output_error(OutputLimitError('The command stderr capture limit was reached'))
                    break
                limit = MAX_CAPTURE_BYTES if self._capture_output else MAX_STDERR_BYTES
                with self._stderr_lock:
                    self._stderr_tail.extend(data)
                    if len(self._stderr_tail) > limit:
                        del self._stderr_tail[:-limit]
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

    def _remove_cleanup_paths(self) -> None:
        with self._cleanup_lock:
            # A second cleanup owner must wait for the first owner's unlink,
            # rather than observing an emptied list before deletion finishes.
            remaining = []
            for cleanup_path in self._cleanup_paths:
                try:
                    os.unlink(cleanup_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    remaining.append(cleanup_path)
            self._cleanup_paths = remaining

    def _watch_leader(self) -> None:
        try:
            self.process.wait()
            # Descendants can retain pipes after their leader exits. Stop the
            # job/group before waiting for reader EOF, even for a successful CLI.
            self._stop_process_tree()
        finally:
            self.close_stdin()
            self._remove_cleanup_paths()
            self._cleanup_done.set()

    def _join_cleanup(self, timeout: float, *, strict: bool) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            self.process.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if strict:
                raise TimeoutError('The CLI process did not finish terminating')
            return
        self._monitor.join(max(0.0, deadline - time.monotonic()))
        self._stdout_reader.join(max(0.0, deadline - time.monotonic()))
        if self._stderr_reader is not None:
            self._stderr_reader.join(max(0.0, deadline - time.monotonic()))
        readers_alive = self._stdout_reader.is_alive()
        if self._stderr_reader is not None:
            readers_alive = readers_alive or self._stderr_reader.is_alive()
        if strict and (self._monitor.is_alive() or readers_alive or not self._cleanup_done.is_set()):
            raise TimeoutError('The CLI process tree or output pipes did not finish closing')
        if strict and self._disposal_error is not None:
            raise self._disposal_error
        if strict and self._cleanup_paths:
            raise OSError('The CLI temporary files could not be removed')

    async def finish_output(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for the process to exit and its output to be read."""

        await asyncio.to_thread(self._join_cleanup, timeout, strict=False)

    async def wait(self, timeout: float | None = None) -> int:
        try:
            return await asyncio.to_thread(self.process.wait, timeout)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(str(error)) from error

    def kill(self) -> None:
        """Stop the process and any commands it started, and remove its temporary files."""
        self._stop_process_tree()
        self.close_stdin()
        self._remove_cleanup_paths()

    async def kill_and_wait(self, timeout: float = 5.0) -> None:
        """Stop the complete tree and confirm containment disposal and pipe EOF."""
        def kill_and_join():
            deadline = time.monotonic() + timeout
            self.kill()
            self._join_cleanup(max(0.0, deadline - time.monotonic()), strict=True)

        operation = asyncio.create_task(asyncio.to_thread(kill_and_join))
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Cancellation must not let a caller report a closed process while
            # native cleanup is still running in the worker thread.
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
            operation.result()
            raise

    async def close(self, timeout: float = 5.0) -> None:
        await self.kill_and_wait(timeout)

    def _stop_process_tree(self) -> None:
        with self._stop_lock:
            if self._tree_stopped:
                return
            try:
                if self._job is not None:
                    self._job.close()
                else:
                    # Keep the original process-group ID after leader exit;
                    # poll() alone says nothing about surviving descendants.
                    _stop_posix_group(self.process.pid)
            except ProcessLookupError:
                self._tree_stopped = True
                self._disposal_error = None
            except (OSError, TimeoutError) as error:
                self._disposal_error = error
                log.warning('Could not verify process-tree disposal for %s: %s', self.process.pid, error)
                try:
                    self.process.kill()
                except OSError:
                    pass
            else:
                self._tree_stopped = True
                self._disposal_error = None


async def run_command(args: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    """Run a short command and return its exit code, stdout, and stderr."""

    process = ChildProcess(args, cwd, env, _capture_output=True)
    process.close_stdin()

    async def capture():
        stdout = []
        length = 0
        while True:
            chunk = await process.read_text()
            if chunk is None:
                break
            length += len(chunk.encode('utf-8'))
            if length > MAX_CAPTURE_BYTES:
                raise OutputLimitError('The command stdout capture limit was reached')
            stdout.append(chunk)
        returncode = await process.wait()
        await process.finish_output(2.0)
        process._raise_output_error()
        stderr = process._stderr_snapshot().decode('utf-8', 'replace')
        return returncode, ''.join(stdout), stderr

    timed_out = False
    try:
        return await asyncio.wait_for(capture(), timeout)
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        await process.kill_and_wait()
    if timed_out:
        raise TimeoutError(f'{os.path.basename(args[0])} did not finish within {timeout:.0f} seconds')
