"""Offline subprocess containment tests; no subscription CLI or network access.

Run with the repository virtualenv:
    .venv\\Scripts\\python.exe -m unittest discover -s local/tests -p test_process_containment.py -v

Every executable is this test's Python interpreter. Tree fixtures write only
inside a temporary directory and stop themselves after 30 seconds as a final
safeguard. Tests retain native handles to their own Windows descendants, so
failure cleanup never targets a PID that could have been reused.
"""

import asyncio
import ctypes
import faulthandler
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]


def load_process_module():
    """Load real process/helper code without initializing the web application."""
    package_paths = {
        'open_webui': ROOT_DIR / 'backend' / 'open_webui',
        'open_webui.utils': ROOT_DIR / 'backend' / 'open_webui' / 'utils',
        'open_webui.utils.subscriptions': ROOT_DIR / 'backend' / 'open_webui' / 'utils' / 'subscriptions',
    }
    module_stubs = {}
    for name, path in package_paths.items():
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        module_stubs[name] = package
    process_path = package_paths['open_webui.utils.subscriptions'] / 'process.py'
    spec = importlib.util.spec_from_file_location('buddy_process_containment_under_test', process_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, module_stubs):
        spec.loader.exec_module(module)
    return module


subscription_process = load_process_module()

IS_WINDOWS = sys.platform == 'win32'

TREE_FIXTURE = r'''
import os
import subprocess
import sys
import time
from pathlib import Path

mode, directory, lifetime, detached, output_stream, output_size = sys.argv[1:]
root = Path(directory)
deadline = time.monotonic() + 30

if mode == 'worker':
    (root / 'worker.pid').write_text(str(os.getpid()), encoding='ascii')
    (root / 'worker.ready').write_text('ready', encoding='ascii')
    count = 0
    while time.monotonic() < deadline:
        count += 1
        (root / 'heartbeat.txt').write_text(str(count), encoding='ascii')
        time.sleep(0.05)
else:
    flags = 0
    if sys.platform == 'win32' and detached == 'yes':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, __file__, 'worker', directory, lifetime, detached, output_stream, output_size],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    while not (root / 'worker.ready').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('worker did not become ready')
        time.sleep(0.01)
    (root / 'leader.ready').write_text('ready', encoding='ascii')
    print('leader-ready', flush=True)
    if output_stream != 'none':
        while not (root / 'emit.ready').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('test did not release output fixture')
            time.sleep(0.01)
        descriptor = 1 if output_stream == 'stdout' else 2
        os.write(descriptor, b'x' * int(output_size))
    if lifetime == 'stay':
        while time.monotonic() < deadline:
            time.sleep(0.05)
'''


class FixtureProcess:
    """Track and, only on test failure, stop one synthetic descendant."""

    def __init__(self, pid: int):
        self.pid = pid
        self.handle = None
        if IS_WINDOWS:
            from ctypes import wintypes

            self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel32.OpenProcess.restype = wintypes.HANDLE
            self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel32.TerminateProcess.restype = wintypes.BOOL
            self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel32.CloseHandle.restype = wintypes.BOOL
            # SYNCHRONIZE | PROCESS_TERMINATE; retain the actual process handle.
            self.handle = self.kernel32.OpenProcess(0x00100001, False, pid)
            if not self.handle and ctypes.get_last_error() not in (87, 1168):
                raise ctypes.WinError(ctypes.get_last_error())

    def is_running(self) -> bool:
        if IS_WINDOWS:
            return bool(self.handle) and self.kernel32.WaitForSingleObject(self.handle, 0) == 0x102
        try:
            # A zombie is no longer executing, even before init reaps it.
            status = Path(f'/proc/{self.pid}/stat')
            if status.exists() and status.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                return False
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def dispose(self) -> None:
        if IS_WINDOWS:
            if self.handle:
                if self.is_running():
                    self.kernel32.TerminateProcess(self.handle, 99)
                    self.kernel32.WaitForSingleObject(self.handle, 5000)
                self.kernel32.CloseHandle(self.handle)
                self.handle = None
            return
        if self.is_running():
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class ProcessContainmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if IS_WINDOWS:
            original_mkdir = os.mkdir

            def make_fixture_directory(path, mode=0o777, *, dir_fd=None):
                # Python 3.13's private Windows 0o700 DACL excludes restricted
                # sandbox tokens. Inherit the workspace ACL for new fixtures.
                original_mkdir(path, 0o777, dir_fd=dir_fd)

            with patch('tempfile._os.mkdir', make_fixture_directory):
                self.directory = tempfile.TemporaryDirectory(prefix='buddy-process-test-', dir=ROOT_DIR)
        else:
            self.directory = tempfile.TemporaryDirectory(prefix='buddy-process-test-')
        self.root = Path(self.directory.name)
        self.fixture = self.root / 'tree_fixture.py'
        self.fixture.write_text(TREE_FIXTURE, encoding='utf-8')
        self.children = []
        self.descendants = []
        self.tasks = []
        self.raw_processes = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, TimeoutError, subscription_process.ProcessClosedError):
                pass
        for child in self.children:
            child.kill()
            try:
                await child.wait(5)
            except TimeoutError:
                pass
        # Fallback cleanup is limited to the fixture's own recorded processes.
        for descendant in self.descendants:
            descendant.dispose()
        for process in self.raw_processes:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        self.directory.cleanup()

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        return env

    def start_child(self, script: str, cleanup_paths=None, **options):
        child = subscription_process.ChildProcess(
            [sys.executable, '-c', script], str(self.root), self.environment(), cleanup_paths, **options
        )
        self.children.append(child)
        return child

    def tree_args(
        self, lifetime: str = 'stay', detached: bool = IS_WINDOWS, output_stream: str = 'none'
    ) -> list[str]:
        return [
            sys.executable,
            str(self.fixture),
            'leader',
            str(self.root),
            lifetime,
            'yes' if detached else 'no',
            output_stream,
            str(getattr(subscription_process, 'MAX_CAPTURE_BYTES', 1024 * 1024) + 1),
        ]

    def start_tree(self, lifetime: str = 'stay', detached: bool = IS_WINDOWS, output_stream: str = 'none'):
        child = subscription_process.ChildProcess(
            self.tree_args(lifetime, detached, output_stream), str(self.root), self.environment()
        )
        self.children.append(child)
        return child

    async def wait_until(self, predicate, message: str, timeout: float = 8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.02)
        self.fail(message)

    async def track_descendant(self, require_running: bool = True) -> FixtureProcess:
        await self.wait_until(
            lambda: (self.root / 'leader.ready').exists(), 'Synthetic process tree did not become ready'
        )
        pid = int((self.root / 'worker.pid').read_text(encoding='ascii'))
        descendant = FixtureProcess(pid)
        self.descendants.append(descendant)
        if require_running:
            self.assertTrue(descendant.is_running(), 'Fixture exited before the action under test')
        return descendant

    async def assert_stopped(self, descendant: FixtureProcess):
        await self.wait_until(
            lambda: not descendant.is_running(), 'Synthetic descendant survived process-tree cleanup', timeout=5
        )

    @unittest.skipUnless(IS_WINDOWS, 'Windows Job Objects contain detached descendants')
    async def test_windows_immediate_kill_stops_detached_descendant(self):
        child = self.start_tree(detached=True)
        descendant = await self.track_descendant()
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        child.kill()
        await child.wait(5)
        await self.assert_stopped(descendant)
        # Disposal may be called repeatedly by a provider and its disconnect handler.
        child.kill()
        await self.assert_stopped(descendant)

    @unittest.skipUnless(IS_WINDOWS, 'Windows Job Objects survive their original leader')
    async def test_windows_natural_leader_exit_stops_detached_descendant(self):
        child = self.start_tree(lifetime='exit', detached=True)
        descendant = await self.track_descendant(require_running=False)
        self.assertEqual(await child.wait(5), 0)
        await self.assert_stopped(descendant)
        await child.finish_output(5)
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        self.assertIsNone(await asyncio.wait_for(child.read_line(), 5))

    async def test_explicit_kill_after_leader_exit_cleans_descendant(self):
        child = self.start_tree(lifetime='exit')
        descendant = await self.track_descendant(require_running=False)
        # Wait on the underlying process to exercise kill even after poll() has a code.
        await asyncio.to_thread(child.process.wait, 5)
        self.assertEqual(child.returncode, 0)
        child.kill()
        await self.assert_stopped(descendant)

    @unittest.skipIf(IS_WINDOWS, 'POSIX process-group containment')
    async def test_posix_kill_stops_descendant_in_leader_group(self):
        child = self.start_tree(detached=False)
        descendant = await self.track_descendant()
        child.kill()
        await child.wait(5)
        await self.assert_stopped(descendant)

    @unittest.skipIf(IS_WINDOWS, 'POSIX process-group containment')
    async def test_posix_natural_leader_exit_stops_group_descendant(self):
        child = self.start_tree(lifetime='exit', detached=False)
        descendant = await self.track_descendant(require_running=False)
        self.assertEqual(await child.wait(5), 0)
        await self.assert_stopped(descendant)

    async def test_run_command_timeout_stops_tree(self):
        task = asyncio.create_task(
            subscription_process.run_command(self.tree_args(), str(self.root), self.environment(), 2)
        )
        self.tasks.append(task)
        descendant = await self.track_descendant()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(task, 10)
        await self.assert_stopped(descendant)

    async def test_run_command_natural_leader_exit_stops_tree(self):
        task = asyncio.create_task(
            subscription_process.run_command(
                self.tree_args(lifetime='exit'), str(self.root), self.environment(), 10
            )
        )
        self.tasks.append(task)
        descendant = await self.track_descendant(require_running=False)
        code, stdout, stderr = await asyncio.wait_for(task, 15)
        self.assertEqual((code, stdout.strip(), stderr), (0, 'leader-ready', ''))
        await self.assert_stopped(descendant)

    async def test_run_command_cancellation_stops_tree(self):
        task = asyncio.create_task(
            subscription_process.run_command(self.tree_args(), str(self.root), self.environment(), 20)
        )
        self.tasks.append(task)
        descendant = await self.track_descendant()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.assert_stopped(descendant)

    async def test_stdin_stdout_streaming_preserves_split_utf8(self):
        payload = 'café 🚀 quote " slash \\ tab\t'
        script = (
            'import os, sys, time\n'
            'for line in sys.stdin:\n'
            '    data = ("echo:" + line).encode("utf-8")\n'
            '    for byte in data:\n'
            '        os.write(1, bytes([byte]))\n'
            '        time.sleep(0.001)\n'
            'os.write(2, "stderr:naïve 🌍".encode("utf-8"))\n'
        )
        child = self.start_child(script)
        await child.write(payload + '\n')
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'echo:' + payload)
        child.close_stdin()
        self.assertIsNone(await asyncio.wait_for(child.read_line(), 5))
        self.assertEqual(await child.wait(5), 0)
        await child.finish_output(5)
        self.assertEqual(child.stderr_text(), 'stderr:naïve 🌍')

    async def test_merged_stderr_is_plain_stdout_text(self):
        child = self.start_child(
            'import os\n'
            'os.write(1, "stdout:café 🚀\\n".encode("utf-8"))\n'
            'os.write(2, "stderr:naïve 🌍\\n".encode("utf-8"))\n',
            merge_stderr=True,
        )
        chunks = []
        while True:
            text = await child.read_text(timeout=5)
            if text is None:
                break
            chunks.append(text)
        self.assertEqual(await child.wait(5), 0)
        await child.finish_output(5)
        self.assertEqual(''.join(chunks), 'stdout:café 🚀\nstderr:naïve 🌍\n')
        self.assertIsNone(child.process.stderr)
        self.assertEqual(child.stderr_text(), '')

    async def test_custom_stdout_cap_applies_before_event_loop_enqueue(self):
        child = self.start_child(
            'import os, time\nos.write(1, b"x" * 4097)\ntime.sleep(20)\n',
            output_limit_bytes=4096,
        )
        # Deliberately do not yield: queued callbacks cannot run on this loop.
        # The reader thread must reserve/count bytes before posting callbacks.
        deadline = time.monotonic() + 5
        while child._output_error is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsInstance(child._output_error, subscription_process.OutputLimitError)
        self.assertEqual(child._chunks.qsize(), 0)
        self.assertLessEqual(child._queued_bytes, 4096)
        await child.kill_and_wait(5)
        with self.assertRaises(subscription_process.OutputLimitError):
            await child.read_text(timeout=5)

    async def test_async_disposal_confirms_tree_pipe_eof_and_is_idempotent(self):
        child = self.start_tree()
        descendant = await self.track_descendant()
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        self.assertFalse(child.disposal_started)
        await child.kill_and_wait(5)
        self.assertTrue(child.disposal_started)
        await self.assert_stopped(descendant)
        self.assertIsNotNone(child.returncode)
        self.assertFalse(child._monitor.is_alive())
        self.assertFalse(child._stdout_reader.is_alive())
        self.assertFalse(child._stderr_reader.is_alive())
        self.assertTrue(child.process.stdin.closed)
        self.assertTrue(child.process.stdout.closed)
        self.assertTrue(child.process.stderr.closed)
        self.assertIsNone(await child.read_text(timeout=5))
        if IS_WINDOWS:
            self.assertIsNone(child._job._handle)
        await child.close(5)
        await child.kill_and_wait(5)
        await child.close(5)

    def disposal_diagnostics(self, child, descendant: FixtureProcess) -> dict:
        return {
            'leader_returncode': child.returncode,
            'descendant_running': descendant.is_running(),
            'monitor_alive': child._monitor.is_alive(),
            'stdout_reader_alive': child._stdout_reader.is_alive(),
            'stderr_reader_alive': child._stderr_reader.is_alive(),
            'cleanup_done': child._cleanup_done.is_set(),
            'tree_stopped': child._tree_stopped,
            'disposal_error': repr(child._disposal_error),
            'stdin_closed': child.process.stdin.closed,
            'stdout_closed': child.process.stdout.closed,
            'stderr_closed': child.process.stderr.closed,
        }

    async def assert_concurrent_disposal(self, child, descendant: FixtureProcess):
        disposals = asyncio.gather(child.close(), child.close(), child.kill_and_wait(), return_exceptions=True)
        try:
            results = await asyncio.wait_for(disposals, 12)
        except TimeoutError:
            faulthandler.dump_traceback()
            self.fail(f'Concurrent disposal timed out: {self.disposal_diagnostics(child, descendant)}')
        errors = [repr(result) for result in results if isinstance(result, BaseException)]
        if errors:
            faulthandler.dump_traceback()
            self.fail(f'Concurrent disposal failed: {errors}; {self.disposal_diagnostics(child, descendant)}')
        await self.assert_stopped(descendant)
        self.assertIsNotNone(child.returncode)
        self.assertTrue(child._cleanup_done.is_set())
        self.assertFalse(child._monitor.is_alive())
        self.assertFalse(child._stdout_reader.is_alive())
        self.assertFalse(child._stderr_reader.is_alive())
        self.assertTrue(child.process.stdin.closed)
        self.assertTrue(child.process.stdout.closed)
        self.assertTrue(child.process.stderr.closed)
        self.assertIsNone(await child.read_text(timeout=5))
        if IS_WINDOWS:
            self.assertIsNone(child._job._handle)

    async def test_concurrent_disposal_of_active_detached_tree(self):
        child = self.start_tree()
        descendant = await self.track_descendant()
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        await self.assert_concurrent_disposal(child, descendant)

    async def test_concurrent_disposal_after_natural_leader_exit(self):
        child = self.start_tree(lifetime='exit')
        descendant = await self.track_descendant(require_running=False)
        self.assertEqual(await child.wait(5), 0)
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        await self.assert_concurrent_disposal(child, descendant)

    @unittest.skipUnless(IS_WINDOWS, 'Windows Job Objects retain failed cleanup ownership')
    async def test_failed_job_verification_retains_handle_for_verified_retry(self):
        job_class = subscription_process.spawn_in_job.__globals__['WindowsJob']
        kernel32 = subscription_process.spawn_in_job.__globals__['_kernel32']
        job = job_class()
        original_handle = job._handle
        try:
            with patch.object(kernel32, 'QueryInformationJobObject', return_value=False):
                with self.assertRaisesRegex(OSError, 'verify'):
                    job.close()
            self.assertEqual(job._handle, original_handle)
            job.close()
            self.assertIsNone(job._handle)
        finally:
            job.close()

    @unittest.skipUnless(IS_WINDOWS, 'Windows Job Object retry after descendant cleanup failure')
    async def test_failed_tree_cleanup_retries_and_clears_error_only_after_success(self):
        child = self.start_tree()
        descendant = await self.track_descendant()
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        with patch.object(child._job, 'close', side_effect=OSError('synthetic close failure')):
            with self.assertRaisesRegex(OSError, 'synthetic close failure'):
                await child.close()
        self.assertFalse(child._tree_stopped)
        self.assertFalse(child.cleanup_confirmed)
        self.assertTrue(child.disposal_started)
        self.assertIsNotNone(child._disposal_error)
        self.assertIsNotNone(child._job._handle)
        self.assertTrue(descendant.is_running())
        await child.close()
        await self.assert_stopped(descendant)
        self.assertTrue(child._tree_stopped)
        self.assertIsNone(child._disposal_error)
        self.assertIsNone(child._job._handle)
        self.assertTrue(child.cleanup_confirmed)

    async def test_repeated_close_cancellation_retains_worker_until_native_cleanup(self):
        child = self.start_tree()
        descendant = await self.track_descendant()
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'leader-ready')
        entered = threading.Event()
        release = threading.Event()
        original_stop = child._stop_process_tree

        def delayed_stop():
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Synthetic cleanup gate was not released')
            original_stop()

        with patch.object(child, '_stop_process_tree', side_effect=delayed_stop):
            close = asyncio.create_task(child.close())
            try:
                await self.wait_until(entered.is_set, 'Cleanup worker never started')
                self.assertTrue(child.disposal_started)
                self.assertIsNone(child.returncode)
                close.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(close.done())
                close.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(close.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await close
        self.assertTrue(child._tree_stopped)
        self.assertFalse(child._monitor.is_alive())
        await self.assert_stopped(descendant)

    async def test_short_command_preserves_unicode_spaces_cwd_argv_and_environment(self):
        directory = self.root / 'project with spaces café 🚀'
        directory.mkdir()
        (directory / 'read µ.txt').write_text('fixture naïve 🌍', encoding='utf-8')
        script = (
            'import json, os, sys\n'
            'from pathlib import Path\n'
            'print(json.dumps({"cwd": os.getcwd(), "arg": sys.argv[1], '
            '"env": os.environ["BUDDY_FIXTURE_UNICODE"], '
            '"file": Path("read µ.txt").read_text(encoding="utf-8")}, ensure_ascii=False))\n'
        )
        script_path = directory / 'script with spaces.py'
        script_path.write_text(script, encoding='utf-8')
        argument = 'quote " and trailing slash \\ café 🚀'
        env = self.environment()
        env['BUDDY_FIXTURE_UNICODE'] = 'environment café 🌍'
        code, stdout, stderr = await subscription_process.run_command(
            [sys.executable, str(script_path), argument], str(directory), env, 10
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, '')
        self.assertEqual(json.loads(stdout), {
            'cwd': str(directory),
            'arg': argument,
            'env': 'environment café 🌍',
            'file': 'fixture naïve 🌍',
        })

    def assert_startup_failure_never_executes(self, method: str):
        marker = self.root / f'{method}-executed.txt'
        script = f'from pathlib import Path; Path({str(marker)!r}).write_text("executed", encoding="utf-8")'
        job_class = subscription_process.spawn_in_job.__globals__['WindowsJob']
        original_popen = subprocess.Popen

        def record_suspended_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            self.raw_processes.append(process)
            return process

        failure = OSError(f'synthetic {method} failure')
        with patch.object(job_class, method, side_effect=failure):
            with patch.object(subprocess, 'Popen', side_effect=record_suspended_process):
                with self.assertRaisesRegex(OSError, f'synthetic {method} failure'):
                    self.start_child(script)
        self.assertEqual(len(self.raw_processes), 1)
        suspended_process = self.raw_processes[0]
        self.assertIsNotNone(suspended_process.poll(), 'Startup failure left the suspended leader alive')
        self.assertFalse(marker.exists(), 'The fixture executed before verified containment')
        self.assertTrue(suspended_process.stdin.closed)
        self.assertTrue(suspended_process.stdout.closed)
        self.assertTrue(suspended_process.stderr.closed)

    @unittest.skipUnless(IS_WINDOWS, 'Windows suspended-process assignment')
    async def test_windows_assignment_failure_terminates_unexecuted_process(self):
        self.assert_startup_failure_never_executes('assign')

    @unittest.skipUnless(IS_WINDOWS, 'Windows suspended-process resume')
    async def test_windows_resume_failure_terminates_unexecuted_process(self):
        self.assert_startup_failure_never_executes('resume')

    async def test_short_command_preserves_stdout_stderr_utf8_below_cap(self):
        self.assertEqual(subscription_process.MAX_CAPTURE_BYTES, 1024 * 1024)
        stdout = 'o' * (subscription_process.MAX_CAPTURE_BYTES // 4) + ' café 🚀'
        stderr = 'e' * (subscription_process.MAX_CAPTURE_BYTES // 4) + ' naïve 🌍'
        script = (
            f'import os\nos.write(1, {json.dumps(stdout, ensure_ascii=False)}.encode("utf-8"))\n'
            f'os.write(2, {json.dumps(stderr, ensure_ascii=False)}.encode("utf-8"))\n'
        )
        # Keep argv small on Windows; the synthetic command reads its own fixture text.
        capture_script = self.root / 'capture.py'
        capture_script.write_text(script, encoding='utf-8')
        code, captured_stdout, captured_stderr = await subscription_process.run_command(
            [sys.executable, str(capture_script)], str(self.root), self.environment(), 10
        )
        self.assertEqual(code, 0)
        self.assertEqual(captured_stdout, stdout)
        self.assertEqual(captured_stderr, stderr)

    async def test_short_command_stdout_overflow_fails_and_stops_tree(self):
        task = asyncio.create_task(
            subscription_process.run_command(
                self.tree_args(output_stream='stdout'), str(self.root), self.environment(), 10
            )
        )
        self.tasks.append(task)
        descendant = await self.track_descendant()
        # Release output only once the test holds the fixture descendant's native handle.
        (self.root / 'emit.ready').write_text('emit', encoding='ascii')
        with self.assertRaises(subscription_process.OutputLimitError):
            await asyncio.wait_for(task, 15)
        await self.assert_stopped(descendant)

    async def test_short_command_stderr_without_newline_overflow_fails(self):
        task = asyncio.create_task(
            subscription_process.run_command(
                self.tree_args(output_stream='stderr'), str(self.root), self.environment(), 10
            )
        )
        self.tasks.append(task)
        descendant = await self.track_descendant()
        (self.root / 'emit.ready').write_text('emit', encoding='ascii')
        with self.assertRaises(subscription_process.OutputLimitError):
            await asyncio.wait_for(task, 15)
        await self.assert_stopped(descendant)

    async def test_streamed_stdout_queue_overflow_fails_and_stops_process(self):
        child = self.start_tree(output_stream='stdout')
        descendant = await self.track_descendant()
        (self.root / 'emit.ready').write_text('emit', encoding='ascii')
        # Let output arrive without a consumer; providers can pause between streamed events.
        await child.wait(5)
        await self.assert_stopped(descendant)
        captured_bytes = 0
        with self.assertRaises(subscription_process.OutputLimitError):
            while True:
                text = await child.read_text(timeout=5)
                if text is None:
                    break
                captured_bytes += len(text.encode('utf-8'))
        self.assertLessEqual(captured_bytes, subscription_process.MAX_CAPTURE_BYTES)

    async def test_streamed_stdout_overflow_reaches_line_reader(self):
        child = self.start_tree(output_stream='stdout')
        descendant = await self.track_descendant()
        (self.root / 'emit.ready').write_text('emit', encoding='ascii')
        await child.wait(5)
        await self.assert_stopped(descendant)
        with self.assertRaises(subscription_process.OutputLimitError):
            while await asyncio.wait_for(child.read_line(), 5) is not None:
                pass

    async def test_streamed_stderr_without_newline_has_bounded_tail(self):
        self.assertEqual(subscription_process.MAX_STDERR_BYTES, 256 * 1024)
        child = self.start_child(
            'import os\n'
            f'os.write(2, b"e" * {subscription_process.MAX_STDERR_BYTES + 1024} + b"end")\n'
            'print("done", flush=True)\n'
        )
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'done')
        self.assertEqual(await child.wait(5), 0)
        await child.finish_output(5)
        stderr = child.stderr_text()
        self.assertTrue(stderr.endswith('end'))
        self.assertLessEqual(len(stderr.encode('utf-8')), subscription_process.MAX_STDERR_BYTES)

    async def test_natural_exit_removes_temporary_files_without_explicit_kill(self):
        instructions = self.root / 'instructions.md'
        instructions.write_text('temporary fixture instructions', encoding='utf-8')
        child = self.start_child('print("finished", flush=True)', cleanup_paths=[str(instructions)])
        self.assertEqual(await child.wait(5), 0)
        await self.wait_until(lambda: not instructions.exists(), 'Natural exit did not remove temporary file')
        self.assertEqual(await asyncio.wait_for(child.read_line(), 5), 'finished')
        self.assertIsNone(await asyncio.wait_for(child.read_line(), 5))


class CleanupOwnershipTests(unittest.TestCase):
    def cleanup_owner(self, path):
        child = subscription_process.ChildProcess.__new__(subscription_process.ChildProcess)
        child._cleanup_lock = threading.Lock()
        child._cleanup_paths = [str(path)]
        return child

    def test_parallel_cleanup_waits_for_the_owned_unlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.md'
            path.write_text('disposable instructions', encoding='utf-8')
            child = self.cleanup_owner(path)
            deleting = threading.Event()
            release = threading.Event()
            second_entered = threading.Event()
            second_done = threading.Event()
            original_unlink = os.unlink
            failures = []

            def delayed_unlink(target):
                deleting.set()
                if not release.wait(5):
                    raise TimeoutError('Synthetic unlink was not released')
                original_unlink(target)

            def first_cleanup():
                try:
                    child._remove_cleanup_paths()
                except BaseException as error:
                    failures.append(error)

            def second_cleanup():
                second_entered.set()
                first_cleanup()
                second_done.set()

            with patch.object(subscription_process.os, 'unlink', side_effect=delayed_unlink):
                first = threading.Thread(target=first_cleanup)
                second = threading.Thread(target=second_cleanup)
                first.start()
                try:
                    self.assertTrue(deleting.wait(2))
                    second.start()
                    self.assertTrue(second_entered.wait(2))
                    self.assertFalse(second_done.wait(0.1))
                finally:
                    release.set()
                    first.join(5)
                    if second.ident is not None:
                        second.join(5)
            self.assertEqual(failures, [])
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertFalse(path.exists())
            self.assertEqual(child._cleanup_paths, [])

    def test_failed_unlink_retains_the_path_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.md'
            path.write_text('disposable instructions', encoding='utf-8')
            child = self.cleanup_owner(path)
            with patch.object(subscription_process.os, 'unlink', side_effect=PermissionError('synthetic denial')):
                child._remove_cleanup_paths()
            self.assertEqual(child._cleanup_paths, [str(path)])
            self.assertTrue(path.exists())
            child._remove_cleanup_paths()
            self.assertEqual(child._cleanup_paths, [])
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
