"""Blocking file-system calls on a few threads of their own.

A sleeping or disconnected drive can block a call for minutes, and a thread
can't be stopped. A call that times out keeps its thread, and its slot, until
the drive answers. So stuck calls never pile up, and never use up the shared
threads that move process output: once every slot is taken, new calls are
refused at once instead of waiting.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor


class DiskCallsBusy(Exception):
    """Every slot is held by a call that is still waiting on its drive."""


class DiskCalls:
    def __init__(self, slots: int, name: str):
        self._slots = slots
        self._busy = 0
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=slots, thread_name_prefix=f'buddy-{name}')

    @property
    def busy(self) -> int:
        """Calls that have not returned yet, including ones whose caller gave up."""
        with self._lock:
            return self._busy

    async def run(self, timeout: float, function, *args):
        """function(*args) on one of these threads; asyncio.TimeoutError after timeout seconds."""
        with self._lock:
            if self._busy >= self._slots:
                raise DiskCallsBusy()
            self._busy += 1
        try:
            future = self._executor.submit(function, *args)
        except BaseException:
            self._release(None)
            raise
        # Released when the call really returns, not when its caller stops waiting.
        future.add_done_callback(self._release)
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout)

    def _release(self, _future) -> None:
        with self._lock:
            self._busy -= 1

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
