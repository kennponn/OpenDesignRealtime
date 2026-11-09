"""Short-lived key clipboard without modifying later user clipboard contents."""

import tkinter as tk


class KeyClipboard:
    def __init__(self, root, timeout_ms=30_000):
        self.root, self.timeout_ms = root, timeout_ms
        self.value = None
        self.timer = None

    def copy(self, value):
        self.clear()
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.value = value
        self.timer = self.root.after(self.timeout_ms, self.clear)

    def clear(self):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.value is not None:
            try:
                if self.root.clipboard_get() == self.value:
                    self.root.clipboard_clear()
            except tk.TclError:
                pass
            finally:
                self.value = None
