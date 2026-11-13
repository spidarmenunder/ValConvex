"""Bounded, deduplicating background task scheduling for UI-triggered work."""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, Optional, TypeVar

T = TypeVar("T")


class BoundedTaskScheduler:
    """Fixed worker pool with a fixed pending limit and optional keyed replacement."""

    def __init__(self, max_workers: int = 4, max_queued: int = 8, thread_name_prefix: str = "valconvex-job"):
        if max_workers < 1 or max_queued < 0:
            raise ValueError("max_workers must be positive and max_queued non-negative")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._slots = threading.BoundedSemaphore(max_workers + max_queued)
        self._lock = threading.RLock()
        self._futures: Dict[str, Future] = {}
        self._closed = False

    def submit(self, key: str, fn: Callable[[], T], *, replace: bool = False) -> Optional[Future]:
        """Schedule a job, return its Future, or None when the bounded queue is full/closed.

        A duplicate non-replacing key returns the existing future. Replacing cancels
        queued work if possible; running work remains bounded and must cooperate.
        """
        with self._lock:
            if self._closed:
                return None
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                if not replace:
                    return existing
                existing.cancel()
            if not self._slots.acquire(blocking=False):
                return None
            try:
                future = self._executor.submit(fn)
            except RuntimeError:
                self._slots.release()
                return None
            self._futures[key] = future
            future.add_done_callback(lambda completed, task_key=key: self._finished(task_key, completed))
            return future

    def _finished(self, key: str, future: Future) -> None:
        with self._lock:
            if self._futures.get(key) is future:
                del self._futures[key]
            self._slots.release()

    def cancel(self, key: str) -> bool:
        with self._lock:
            future = self._futures.get(key)
            return bool(future and future.cancel())

    def shutdown(self, wait: bool = True, cancel_futures: bool = True) -> None:
        with self._lock:
            self._closed = True
            futures = list(self._futures.values())
            if cancel_futures:
                for future in futures:
                    future.cancel()
        # ThreadPoolExecutor supports a later wait=True shutdown after an
        # earlier non-blocking shutdown, which lets the UI close promptly while
        # the launcher joins workers before closing SQLite.
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(not future.done() for future in self._futures.values())
