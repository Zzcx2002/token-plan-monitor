from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import sys
from pathlib import Path

from .paths import data_directory

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Hold a Windows mutex or a macOS advisory file lock for this process."""

    def __init__(self, name: str = r"Local\TokenPlanMonitor.SingleInstance", *, lock_dir: Path | None = None):
        self.name = name
        self._kernel32 = None
        self._handle = None
        self._owned = False
        self._lock_dir = lock_dir
        self._file = None

    def acquire(self) -> bool:
        if sys.platform == "darwin":
            return self._acquire_file_lock()
        if self._handle is not None:
            return self._owned
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Unable to create instance mutex")
        self._kernel32 = kernel32
        self._handle = handle
        self._owned = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
        return self._owned

    def release(self) -> None:
        if self._file is not None:
            # Do not unlink: doing so can allow two processes to lock different inodes.
            self._file.close()
            self._file = None
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self._kernel32 = None
        self._owned = False

    def _acquire_file_lock(self) -> bool:
        import fcntl
        if self._file is not None:
            return self._owned
        directory = self._lock_dir or data_directory()
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(self.name.encode()).hexdigest()[:16]
        handle = (directory / f"instance-{digest}.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._file = handle
        self._owned = True
        return True
