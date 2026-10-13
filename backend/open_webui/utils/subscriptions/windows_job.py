"""Native Windows containment for piped CLI processes.

A process is created suspended, assigned to a verified kill-on-close Job Object,
and only then resumed. No shell, supervisor script, or persistent service is used.
"""

import ctypes
from ctypes import wintypes
import subprocess
import sys
import threading
import time

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        ('ReadOperationCount', ctypes.c_ulonglong),
        ('WriteOperationCount', ctypes.c_ulonglong),
        ('OtherOperationCount', ctypes.c_ulonglong),
        ('ReadTransferCount', ctypes.c_ulonglong),
        ('WriteTransferCount', ctypes.c_ulonglong),
        ('OtherTransferCount', ctypes.c_ulonglong),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', BasicLimits),
        ('IoInfo', IoCounters),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


class BasicAccounting(ctypes.Structure):
    _fields_ = [
        ('TotalUserTime', ctypes.c_longlong),
        ('TotalKernelTime', ctypes.c_longlong),
        ('ThisPeriodTotalUserTime', ctypes.c_longlong),
        ('ThisPeriodTotalKernelTime', ctypes.c_longlong),
        ('TotalPageFaultCount', wintypes.DWORD),
        ('TotalProcesses', wintypes.DWORD),
        ('ActiveProcesses', wintypes.DWORD),
        ('TotalTerminatedProcesses', wintypes.DWORD),
    ]


class ThreadEntry(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ThreadID', wintypes.DWORD),
        ('th32OwnerProcessID', wintypes.DWORD),
        ('tpBasePri', wintypes.LONG),
        ('tpDeltaPri', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
    ]


def _configure_kernel32():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


_kernel32 = _configure_kernel32() if sys.platform == 'win32' else None


def _native_error(action: str) -> OSError:
    error = ctypes.WinError(ctypes.get_last_error())
    return OSError(error.errno, f'{action}: {error.strerror}')


class WindowsJob:
    """Own a job handle; explicit disposal verifies that no job processes remain."""

    def __init__(self):
        if _kernel32 is None:
            raise OSError('Windows Job Objects are only available on Windows')
        self._lock = threading.Lock()
        self._handle = _kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise _native_error('Cannot create command job')
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            self._handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = _native_error('Cannot protect command job')
            _kernel32.CloseHandle(self._handle)
            self._handle = None
            raise error

    def assign(self, process: subprocess.Popen) -> None:
        handle = wintypes.HANDLE(int(process._handle))
        if not _kernel32.AssignProcessToJobObject(self._handle, handle):
            raise _native_error('Cannot assign suspended command to job')
        contained = wintypes.BOOL()
        if not _kernel32.IsProcessInJob(handle, self._handle, ctypes.byref(contained)):
            raise _native_error('Cannot verify command job assignment')
        if not contained.value:
            raise OSError('The suspended command was not assigned to its job')

    def resume(self, process: subprocess.Popen) -> None:
        """Resume suspended threads only after assignment has been verified.

        Popen closes CreateProcess's primary-thread handle, so obtain its thread
        through the documented Toolhelp API rather than patching global Popen.
        """
        snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            raise _native_error('Cannot inspect suspended command thread')
        resumed = False
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == process.pid:
                    thread = _kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if not thread:
                        raise _native_error('Cannot open suspended command thread')
                    try:
                        previous = _kernel32.ResumeThread(thread)
                        if previous == 0xFFFFFFFF:
                            raise _native_error('Cannot resume protected command')
                        if previous > 1:
                            raise OSError('The command thread has an unexpected suspension count')
                        if previous == 1:
                            resumed = True
                    finally:
                        _kernel32.CloseHandle(thread)
                entry.dwSize = ctypes.sizeof(entry)
                available = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            if not resumed:
                raise OSError('Cannot locate the suspended command thread')
        finally:
            _kernel32.CloseHandle(snapshot)

    def close(self, timeout: float = 2.0) -> None:
        """Terminate the complete job, confirm zero active processes, and close."""
        with self._lock:
            if self._handle is None:
                return
            # Retain the handle on failure so its owner can retry verification.
            # The destructor's kill-on-close remains the final safeguard.
            if not _kernel32.TerminateJobObject(self._handle, 1):
                raise _native_error('Cannot terminate command job')
            deadline = time.monotonic() + timeout
            accounting = BasicAccounting()
            while True:
                if not _kernel32.QueryInformationJobObject(
                    self._handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                    ctypes.byref(accounting), ctypes.sizeof(accounting), None
                ):
                    raise _native_error('Cannot verify command job cleanup')
                if accounting.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('The command job did not finish terminating')
                time.sleep(0.01)
            if not _kernel32.CloseHandle(self._handle):
                raise _native_error('Cannot release command job')
            self._handle = None

    def __del__(self):
        # Kernel kill-on-close is the final safeguard if normal disposal cannot run.
        handle = getattr(self, '_handle', None)
        if handle and _kernel32 is not None:
            _kernel32.CloseHandle(handle)
            self._handle = None


def spawn_in_job(args: list[str], cwd: str, env: dict[str, str], *, merge_stderr: bool = False):
    """Return a verified contained Popen and its job, or fail before execution."""
    job = WindowsJob()
    process = None
    try:
        stderr = subprocess.STDOUT if merge_stderr else subprocess.PIPE
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED,
        )
        job.assign(process)
        job.resume(process)
        return process, job
    except BaseException:
        # Assignment failure leaves a suspended process outside the job. Kill it
        # directly as well, before allowing a caller to observe startup failure.
        try:
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=2)
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
        finally:
            job.close()
        raise
