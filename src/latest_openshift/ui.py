"""Terminal output.

Two rules shape everything here:

* **stdout carries data, stderr carries chrome.**  Progress bars, spinners and
  status messages always go to stderr, so ``$(latest-openshift latest)`` picks
  up ``4.22.13`` and nothing else, and so a shim can print progress without
  corrupting the output of the program it is about to exec.
* **Animation needs a terminal.**  Spinners and progress bars appear only when
  the relevant stream is a TTY and ``CI`` is unset or false.  Otherwise the
  same information is emitted as plain lines, or not at all.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from typing import IO, Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

_FALSEY = {"", "0", "false", "no", "off"}


def is_ci(environ: dict[str, str] | None = None) -> bool:
    """True when ``CI`` is set to something other than a falsey value."""
    env = os.environ if environ is None else environ
    return env.get("CI", "").strip().lower() not in _FALSEY


class UI:
    """Console output for one CLI invocation."""

    def __init__(
        self,
        *,
        stdout: IO[str] | None = None,
        stderr: IO[str] | None = None,
        quiet: bool = False,
        force_plain: bool = False,
    ) -> None:
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self.quiet = quiet
        self._force_plain = force_plain or is_ci()
        self.out = Console(file=self._stdout, soft_wrap=True)
        # Soft wrapping keeps paths and URLs in messages on one line when
        # stderr is redirected, where rich would otherwise assume 80 columns.
        self.err = Console(file=self._stderr, stderr=True, soft_wrap=True)

    @property
    def animated(self) -> bool:
        """Whether spinners and progress bars should be shown."""
        return not self._force_plain and self.err.is_terminal

    @property
    def rich_stdout(self) -> bool:
        """Whether stdout may carry tables, colour and other decoration."""
        return not self._force_plain and self.out.is_terminal

    # ------------------------------------------------------------------
    # Data output (stdout)
    # ------------------------------------------------------------------

    def value(self, text: str) -> None:
        """Print one plain value to stdout. Never decorated, never suppressed."""
        print(text, file=self._stdout, flush=True)

    def values(self, rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> None:
        """Print tabular data: a rich table on a TTY, tab-separated otherwise."""
        if not self.rich_stdout:
            for row in rows:
                print("\t".join(row), file=self._stdout)
            self._stdout.flush()
            return

        table = Table(box=None, pad_edge=False, header_style="bold")
        for header in headers:
            # Fold rather than ellipsize: a truncated path or URL is useless.
            table.add_column(header, overflow="fold")
        for row in rows:
            table.add_row(*row)
        self.out.print(table)

    # ------------------------------------------------------------------
    # Chrome (stderr)
    # ------------------------------------------------------------------

    # Messages are written with highlighting off: rich would otherwise pick
    # out version numbers and paths inside them and colour them separately,
    # which reads as noise rather than emphasis.

    def info(self, message: str) -> None:
        if not self.quiet:
            self.err.print(message, style="dim", highlight=False)

    def warn(self, message: str) -> None:
        self.err.print(f"warning: {message}", style="yellow", highlight=False)

    def error(self, message: str) -> None:
        self.err.print(f"error: {message}", style="bold red", highlight=False)

    def success(self, message: str) -> None:
        if not self.quiet:
            self.err.print(message, style="green", highlight=False)

    @contextlib.contextmanager
    def status(self, message: str) -> Iterator[None]:
        """Show a spinner for the duration of a slow, unmeasurable operation."""
        if self.quiet or not self.animated:
            yield
            return
        with self.err.status(message, spinner="dots"):
            yield

    @contextlib.contextmanager
    def download(self, description: str) -> Iterator[DownloadReporter]:
        """Show a progress bar for one download.

        Yields a reporter whose ``start``/``advance`` methods are passed
        straight to :meth:`mirror.Mirror.download`.
        """
        if self.quiet or not self.animated:
            yield _SilentReporter()
            return

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.err,
            transient=True,
        )
        with progress:
            task = progress.add_task(description, total=None)
            yield _ProgressReporter(progress, task)


class DownloadReporter:
    """Callbacks handed to the downloader to report progress."""

    def start(self, total: int | None) -> None:
        raise NotImplementedError

    def advance(self, amount: int) -> None:
        raise NotImplementedError


class _SilentReporter(DownloadReporter):
    def start(self, total: int | None) -> None:
        return None

    def advance(self, amount: int) -> None:
        return None


class _ProgressReporter(DownloadReporter):
    def __init__(self, progress: Progress, task: Any) -> None:
        self._progress = progress
        self._task = task

    def start(self, total: int | None) -> None:
        self._progress.update(self._task, total=total)

    def advance(self, amount: int) -> None:
        self._progress.advance(self._task, amount)
