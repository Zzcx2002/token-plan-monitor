from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Hold a Windows named mutex for the lifetime of one application process."""

    def __init__(self, name: str = r"Local\TokenPlanMonitor.SingleInstance"):
        self.name = name
        self._kernel32 = None
        self._handle = None
        self._owned = False

    def acquire(self) -> bool:
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
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self._kernel32 = None
        self._owned = False
