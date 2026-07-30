"""Small dependency-free progress display for long terminal operations."""

from __future__ import annotations

import os
import sys
import threading
from types import TracebackType
from typing import TextIO


class ProgressBar:
    """Render real milestone progress with a spinner between milestones."""

    _SPINNER = ("|", "/", "-", "\\")

    def __init__(
        self,
        total: int,
        *,
        stream: TextIO | None = None,
        interactive: bool | None = None,
        width: int = 28,
        interval: float = 0.1,
    ) -> None:
        if total < 1:
            raise ValueError("progress total must be positive")
        self.total = total
        self.stream = stream or sys.stderr
        self.width = width
        self.interval = interval
        self.interactive = (
            self.stream.isatty() and os.environ.get("TERM", "") != "dumb"
            if interactive is None
            else interactive
        )
        self.completed = 0
        self.label = "Starting installation"
        self._frame = 0
        self._last_line_length = 0
        self._last_plain_update: tuple[int, str] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._finished = False

    def __enter__(self) -> ProgressBar:
        if self.interactive:
            self._render()
            self._thread = threading.Thread(
                target=self._animate,
                name="airprint-progress",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is None:
            self.finish()
        else:
            self.fail()

    def update(self, completed: int, label: str) -> None:
        if not 0 <= completed <= self.total:
            raise ValueError("progress value is outside its declared total")
        clean_label = " ".join(label.split())
        if not clean_label:
            raise ValueError("progress label may not be empty")
        with self._lock:
            if completed < self.completed:
                raise ValueError("progress may not move backwards")
            self.completed = completed
            self.label = clean_label
            plain_update = (completed, clean_label)
            should_print_plain = not self.interactive and plain_update != self._last_plain_update
            if should_print_plain:
                self._last_plain_update = plain_update
        if self.interactive:
            self._render()
        elif should_print_plain:
            print(f"[{completed}/{self.total}] {clean_label}", file=self.stream, flush=True)

    def finish(self) -> None:
        if self._finished:
            return
        if self.completed < self.total:
            self.update(self.total, "Installation complete")
        self._stop_animation()
        if self.interactive:
            self._render(final=True)
            print(file=self.stream, flush=True)
        self._finished = True

    def fail(self) -> None:
        if self._finished:
            return
        self._stop_animation()
        if self.interactive:
            self._render(failed=True)
            print(file=self.stream, flush=True)
        else:
            print(f"[failed] {self.label}", file=self.stream, flush=True)
        self._finished = True

    def _stop_animation(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 2))
            self._thread = None

    def _animate(self) -> None:
        while not self._stop.wait(self.interval):
            with self._lock:
                self._frame = (self._frame + 1) % len(self._SPINNER)
            self._render()

    def _render(self, *, final: bool = False, failed: bool = False) -> None:
        with self._lock:
            fraction = self.completed / self.total
            filled = min(self.width, int(self.width * fraction))
            if final:
                bar = "=" * self.width
                marker = " "
                percentage = 100
            else:
                remaining = self.width - filled
                bar = "=" * filled + (">" if remaining else "")
                bar += "-" * max(0, remaining - 1)
                marker = "!" if failed else self._SPINNER[self._frame]
                percentage = round(fraction * 100)
            line = f"[{bar}] {percentage:3d}% {marker} {self.label}"
            padding = " " * max(0, self._last_line_length - len(line))
            self._last_line_length = len(line)
        print(f"\r{line}{padding}", end="", file=self.stream, flush=True)
