"""Remember user window sizes independently of account and theme settings."""
from __future__ import annotations

import logging
import tkinter as tk

from .models import CacheError
from .storage import Storage
from .ui_graphics import dpi_scale, window_work_area

logger = logging.getLogger(__name__)


class WindowGeometry:
    def __init__(self, window: tk.Toplevel, storage: Storage, name: str,
                 minimum: tuple[int, int]):
        self.window, self.storage, self.name = window, storage, name
        self.minimum = minimum
        self.initialized = False
        self.pending: str | None = None
        self.size = storage.load_window_size(name)
        window.resizable(True, True)
        window.minsize(*(round(n*dpi_scale(window)) for n in minimum))
        window.bind("<Configure>", self._changed, add=True)
        window.bind("<Unmap>", self._unmap, add=True)
        window.bind("<Destroy>", self._destroy, add=True)

    def fit(self, default: tuple[int, int]) -> None:
        if self.initialized:
            return
        scale = dpi_scale(self.window)
        left, top, right, bottom = window_work_area(self.window)
        # Clamp before multiplying: a finite JSON number may overflow at high DPI.
        limits = (right-left-20, bottom-top-60)
        requested = tuple(round(min(n, limit/scale)*scale)
                          for n, limit in zip(self.size, limits)) if self.size else default
        width = min(max(round(self.minimum[0]*scale), requested[0]), right-left-20)
        height = min(max(round(self.minimum[1]*scale), requested[1]), bottom-top-60)
        self.pixel_size=(width,height)
        self.window.geometry(f"{width}x{height}")
        self.initialized = True

    def _changed(self, event: tk.Event) -> None:
        if event.widget != self.window or not self.initialized:
            return
        if self.pending:
            self.window.after_cancel(self.pending)
        self.pending = self.window.after(350, self.flush)

    def flush(self) -> None:
        if self.pending:
            self.window.after_cancel(self.pending)
            self.pending = None
        if not self.initialized or not self.window.winfo_exists():
            return
        if self.window.state() in ("zoomed", "iconic"):
            return
        chrome=getattr(self.window,"_chrome",None)
        if chrome is not None and not chrome.normal:
            return
        width, height = self.window.winfo_width(), self.window.winfo_height()
        if min(width, height) <= 1:
            return
        scale = dpi_scale(self.window)
        size = width/scale, height/scale
        if size == self.size:
            return
        self.size = size
        try:
            self.storage.save_window_size(self.name, size)
        except CacheError as exc:
            logger.warning("Could not save %s size: %s", self.name, exc.message)

    def _unmap(self, event: tk.Event) -> None:
        if event.widget == self.window:
            self.flush()

    def _destroy(self, event: tk.Event) -> None:
        if event.widget == self.window and self.pending:
            self.window.after_cancel(self.pending)
            self.pending = None
