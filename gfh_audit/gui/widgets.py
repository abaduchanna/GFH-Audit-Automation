"""Reusable tkinter widgets: thread-safe log console, form rows, tables."""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Tuple


class LogConsole(ttk.Frame):
    """Thread-safe scrolling log console fed via a queue + periodic poll."""

    def __init__(self, master, poll_ms: int = 250, max_lines: int = 2000):
        super().__init__(master)
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._max_lines = max_lines

        self.text = tk.Text(self, wrap="word", state="disabled", height=14,
                            bg="#101418", fg="#d7dde3", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.text.tag_configure("info", foreground="#d7dde3")
        self.text.tag_configure("warn", foreground="#f2c744")
        self.text.tag_configure("error", foreground="#ef5b5b")
        self.text.tag_configure("success", foreground="#59d499")

        self.after(poll_ms, self._drain)

    def log(self, message: str, level: str = "info") -> None:
        self._queue.put((level, message))

    def _drain(self) -> None:
        try:
            batch: List[Tuple[str, str]] = []
            while True:
                batch.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        if batch:
            self.text.configure(state="normal")
            for level, message in batch:
                self.text.insert("end", message + "\n", level if level in {"info", "warn", "error", "success"} else "info")
            # trim
            line_count = int(self.text.index("end-1c").split(".")[0])
            if line_count > self._max_lines:
                self.text.delete("1.0", f"{line_count - self._max_lines}.0")
            self.text.configure(state="disabled")
            self.text.see("end")
        self.after(250, self._drain)


def form_row(parent, row: int, label: str, widget, label_width: int = 24) -> None:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
    widget.grid(row=row, column=1, sticky="ew", pady=3)
    parent.columnconfigure(1, weight=1)


def make_treeview(master, columns: List[Tuple[str, str, int, str]], height: int = 12) -> tuple:
    """columns: list of (key, heading, width, anchor). Returns (frame, tree)."""
    frame = ttk.Frame(master)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height)
    for key, heading, width, anchor in columns:
        tree.heading(key, text=heading)
        tree.column(key, width=width, anchor=anchor)
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    return frame, tree


def sort_treeview(tree: ttk.Treeview, col: str, reverse: bool) -> None:
    try:
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        data.sort(key=lambda item: item[0].lower(), reverse=reverse)
        for index, (_val, k) in enumerate(data):
            tree.move(k, "", index)
        tree.heading(col, command=lambda: sort_treeview(tree, col, not reverse))
    except Exception:
        pass
