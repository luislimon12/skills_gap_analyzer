"""Single-instance lock so two worker runs never overlap.

Uses an OS file lock (fcntl on Linux, msvcrt on Windows), which the operating
system releases automatically if the process dies, so a crash never leaves a
stale lock behind. The PID is written into the file for troubleshooting.
"""

from __future__ import annotations

import os
from pathlib import Path


class WorkerAlreadyRunning(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise WorkerAlreadyRunning(f"Another worker holds {self.path}") from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
