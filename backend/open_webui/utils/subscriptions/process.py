"""Child processes for the subscription CLIs.

Output is read on background threads and handed to the event loop. This works
with both Windows event loops; the selector loop cannot run asyncio
subprocesses.
"""

import asyncio
import codecs
import ctypes
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
# Protocol lines can echo attachments: a Claude Read tool result carries an
# image or PDF twice in base64, and Codex items carry attached images.
MAX_LINE_BYTES = 64 * 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_PENDING_CHUNKS = 128
READ_BYTES = 65536

# Zombie and dead processes cannot execute or hold pipes.
_FINISHED_STATES = (b'Z', b'X')
_PR_SET_CHILD_SUBREAPER = 36


def _has_linux_proc() -> bool:
    return sys.platform.startswith('linux') and os.path.isdir('/proc')


def _linux_process_status(pid: int | str) -> list[bytes]:
    """Return the /proc/<pid>/stat fields after the command name.

    Index 0 is the state, 1 the parent pid, 2 the process group and 19 the
    start time. The command name may hold any bytes, so it is skipped.
    """
    with open(f'/proc/{pid}/stat', 'rb') as status:
        return status.read().rsplit(b')', 1)[1].split()


def _linux_descendants(leader_pid: int) -> dict[int, int]:
    """Map each live process started under ``leader_pid`` to its start time.

    Parent links are followed into any session or process group. The start
    time lets a later check tell whether a pid still names the same process.
    """
    children_by_parent: dict[int, list[int]] = {}
    start_times: dict[int, int] = {}
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                fields = _linux_process_status(entry.name)
            except (FileNotFoundError, ProcessLookupError):
                continue
            if fields[0] in _FINISHED_STATES:
                continue
            pid = int(entry.name)
            children_by_parent.setdefault(int(fields[1]), []).append(pid)
            start_times[pid] = int(fields[19])
    descendants = {}
    unvisited = [leader_pid]
    while unvisited:
        for child_pid in children_by_parent.get(unvisited.pop(), []):
            if child_pid not in descendants:
                descendants[child_pid] = start_times[child_pid]
                unvisited.append(child_pid)
    return descendants


def _linux_process_is_running(pid: int, start_time: int) -> bool:
    """Whether ``pid`` is still the live process that started at ``start_time``."""
    try:
        fields = _linux_process_status(pid)
    except (FileNotFoundError, ProcessLookupError):
        return False
    return fields[0] not in _FINISHED_STATES and int(fields[19]) == start_time


def _subreaper_hook():
    """Return a pre-exec hook that makes a new CLI a child subreaper, or None.

    A command orphaned inside the CLI's tree, such as a background job whose
    shell has exited, is then re-parented to the CLI instead of init, so the
    parent links searched at kill time still reach it. The hook only calls
    prctl between fork and exec.
    """
    if not sys.platform.startswith('linux'):
        return None
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None

    def become_subreaper() -> None:
        prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)

    return become_subreaper


def _posix_group_has_live_processes(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    if not _has_linux_proc():
        return True
    # Orphaned zombies can remain until the host init/subreaper collects them.
    # They cannot execute or retain pipes, so distinguish them from live members.
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                fields = _linux_process_status(entry.name)
                if int(fields[2]) == group_id and fields[0] not in _FINISHED_STATES:
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
    Unread text is limited to ``_output_limit_bytes`` and
    ``_pending_chunk_limit`` chunks; each subclass decides what a producer
    does while the queue is full.
    """

    def __init__(self):
        self._chunks: asyncio.Queue[str | None] = asyncio.Queue()
        self._pending_text = ''
        self._stdout_closed = False
        self._output_error: OutputLimitError | None = None
        self._pending_chunk_limit = MAX_PENDING_CHUNKS
        self._output_limit_bytes = MAX_CAPTURE_BYTES
        self._queued_bytes = 0
        self._queued_chunks = 0
        self._line_limit_bytes = MAX_LINE_BYTES
        self._line_parts: list[str] = []
        self._line_bytes = 0

    def _queue_has_room(self, size: int) -> bool:
        """Whether ``size`` more bytes of unread stdout fit the queue budget."""
        if self._queued_chunks >= self._pending_chunk_limit:
            return False
        return self._queued_bytes + size <= self._output_limit_bytes

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

    def _add_line_text(self, text: str) -> None:
        """Add text to the current line, dropping the line once it is too long."""
        self._line_bytes += len(text.encode('utf-8'))
        if self._line_bytes > self._line_limit_bytes:
            self._line_parts.clear()
        else:
            self._line_parts.append(text)

    def _take_line(self) -> str | None:
        """Return the current line and start the next one; None if it was dropped."""
        line = ''.join(self._line_parts)
        line_bytes = self._line_bytes
        self._line_parts = []
        self._line_bytes = 0
        if line_bytes > self._line_limit_bytes:
            log.warning(
                'Skipped a %d-byte CLI output line over the %d-byte line limit',
                line_bytes, self._line_limit_bytes,
            )
            return None
        return line

    async def read_line(self) -> str | None:
        """Return the next stdout line without its newline, or None at the end.

        A line over MAX_LINE_BYTES is skipped with a warning rather than
        stopping the CLI, which would end every turn the CLI is serving.
        """
        self._raise_output_error()
        while True:
            if not self._pending_text:
                if self._stdout_closed:
                    if self._line_bytes == 0:
                        return None
                    return self._take_line()
                chunk = await self._next_chunk()
                if chunk is None:
                    self._stdout_closed = True
                    continue
                self._pending_text = chunk
            text, newline, self._pending_text = self._pending_text.partition('\n')
            self._add_line_text(text)
            if newline:
                line = self._take_line()
                if line is not None:
                    return line.rstrip('\r')


class ChildProcess(StreamedOutput):
    """A contained CLI process with piped stdin and bounded output buffers.

    ``output_limit_bytes`` caps pending stdout before event-loop callbacks are
    queued. When the reader falls behind, the stdout thread waits instead of
    reading, so the pipe fills and the CLI pauses. Split stderr remains the
    default for provider JSON protocols.
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
        self._queue_condition = threading.Condition()
        self._stdout_abandoned = False
        self._eof_posted = False
        self._stdin_lock = threading.Lock()
        self._stdin_writes = 0
        self._stdin_close_requested = False
        self._stderr_lock = threading.Lock()
        self._stderr_tail = bytearray()
        self._stderr_total_bytes = 0
        self._stdout_total_bytes = 0
        self._capture_output = _capture_output
        self._stop_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._tree_stopped = False
        self._known_descendants: dict[int, int] = {}
        self._disposal_started = False
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
                pass_fds=inherited_fds, preexec_fn=_subreaper_hook(),
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

    @property
    def disposal_started(self) -> bool:
        return self._disposal_started

    def stderr_text(self) -> str:
        return self._stderr_snapshot().decode('utf-8', 'replace').rstrip()

    def _stderr_snapshot(self) -> bytes:
        with self._stderr_lock:
            return bytes(self._stderr_tail)

    def _consume_chunk(self, chunk: str | None) -> None:
        if chunk is not None:
            with self._queue_condition:
                self._queued_bytes -= len(chunk.encode('utf-8'))
                self._queued_chunks -= 1
                self._queue_condition.notify_all()

    def _set_output_error(self, error: OutputLimitError) -> None:
        if self._output_error is None:
            self._output_error = error
            self._post(None)
            self.kill()

    def _abandon_stdout(self) -> None:
        """Stop waiting for the reader; kill() means later output is not needed."""
        with self._queue_condition:
            self._stdout_abandoned = True
            self._queue_condition.notify_all()

    def _reserve_queue_room(self, text: str) -> bool:
        """Count ``text`` as unread output, waiting while the reader is behind.

        While this waits the thread does not read the pipe, so the pipe fills
        and the CLI pauses on its next write instead of being stopped. Returns
        False when the text is dropped: after an output error, or once kill()
        has abandoned the output.
        """
        size = len(text.encode('utf-8'))
        if size > self._output_limit_bytes:
            # Text that cannot fit even an empty queue would wait forever.
            self._set_output_error(OutputLimitError('The CLI stdout buffer limit was reached'))
            return False
        with self._queue_condition:
            while self._output_error is None and not self._queue_has_room(size):
                if self._stdout_abandoned:
                    return False
                self._queue_condition.wait()
            if self._output_error is not None:
                return False
            self._queued_bytes += size
            self._queued_chunks += 1
            return True

    def _post(self, item: str | None) -> None:
        if item is None:
            with self._queue_condition:
                if self._eof_posted:
                    return
                self._eof_posted = True
        elif not self._reserve_queue_room(item):
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

    def _closed_error(self) -> ProcessClosedError:
        return ProcessClosedError(f'{os.path.basename(self.args[0])} is no longer running')

    def _write_blocking(self, text: str) -> None:
        with self._stdin_lock:
            if self._stdin_close_requested:
                raise self._closed_error()
            self._stdin_writes += 1
        try:
            self.process.stdin.write(text.encode('utf-8'))
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise self._closed_error() from error
        finally:
            self._finish_stdin_write()

    def _finish_stdin_write(self) -> None:
        with self._stdin_lock:
            self._stdin_writes -= 1
            close_now = self._stdin_close_requested and self._stdin_writes == 0
        if close_now:
            self._close_stdin_pipe()

    async def write(self, text: str) -> None:
        await asyncio.to_thread(self._write_blocking, text)

    def close_stdin(self) -> None:
        """Close stdin now, or as soon as the writes in progress finish.

        A write keeps the pipe's buffer locked until the CLI reads the data,
        and cancelling ``write()`` does not stop its thread. Closing then would
        block the event loop, so the last write closes the pipe instead. kill()
        makes a pending write fail at once.
        """
        with self._stdin_lock:
            self._stdin_close_requested = True
            if self._stdin_writes > 0:
                return
        self._close_stdin_pipe()

    def _close_stdin_pipe(self) -> None:
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
        self._disposal_started = True
        self._abandon_stdout()
        self._stop_process_tree()
        self.close_stdin()
        self._remove_cleanup_paths()

    async def kill_and_wait(self, timeout: float = 5.0) -> None:
        """Stop the complete tree and confirm containment disposal and pipe EOF."""
        self._disposal_started = True
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
        self._disposal_started = True
        await self.kill_and_wait(timeout)

    def _stop_process_tree(self) -> None:
        self._disposal_started = True
        with self._stop_lock:
            if self._tree_stopped:
                return
            try:
                if self._job is not None:
                    self._job.close()
                else:
                    self._stop_posix_tree()
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

    def _stop_posix_tree(self) -> None:
        try:
            if _has_linux_proc():
                self._stop_linux_descendants()
        finally:
            # Keep the original process-group ID after leader exit;
            # poll() alone says nothing about surviving descendants.
            _stop_posix_group(self.process.pid)

    def _stop_linux_descendants(self) -> None:
        """SIGKILL what the CLI started outside its process group, and confirm it ended.

        Claude Code starts its Bash tool commands with setsid, so killing the
        CLI's process group misses them. While the CLI runs they are found
        through parent links; its group is stopped first so it cannot start
        more meanwhile. After the CLI has exited they can no longer be found,
        but those found by an earlier attempt are still checked.
        """
        leader_running = self.process.returncode is None
        if leader_running:
            try:
                os.killpg(self.process.pid, signal.SIGSTOP)
            except ProcessLookupError:
                leader_running = False
        deadline = time.monotonic() + 2.0
        while True:
            if leader_running:
                self._known_descendants.update(_linux_descendants(self.process.pid))
            self._forget_ended_descendants()
            if not self._known_descendants:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('Commands started by the CLI did not finish terminating')
            for pid in self._known_descendants:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.01)

    def _forget_ended_descendants(self) -> None:
        running = {}
        for pid, start_time in self._known_descendants.items():
            if _linux_process_is_running(pid, start_time):
                running[pid] = start_time
        self._known_descendants = running


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
