"""Terminal rendering for the CLI client, safe to call from any thread.

Two threads write here -- the receiver prints as messages arrive, the input side
prints the prompt -- so every write holds one lock. Without it the two interleave
mid-line and produce text nobody can read.

The prompt is redrawn rather than preserved. Restoring exactly what the user had
half-typed would mean intercepting every keystroke, which costs far more than it
is worth: the partial line is still in the terminal's own buffer, and pressing
Enter re-reads it.
"""

from __future__ import annotations

import sys
import threading
from typing import TextIO

__all__ = ["DEFAULT_PROMPT", "Ui"]

#: Shown while waiting for input.
DEFAULT_PROMPT = "> "

_RESET = "\033[0m"
_DIM = "\033[2m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_CLEAR_LINE = "\r\033[K"


class Ui:
    """Renders chat traffic and prompts to one stream.

    Args:
        prompt: the string shown while waiting for input.
        stream: where to write. Defaults to ``stdout``.
        interactive: force the terminal treatment on or off. ``None`` -- the
            default -- decides from the stream, so piping the client into a file
            produces a clean transcript with no escape sequences and no prompts
            the user never typed.
    """

    __slots__ = ("_interactive", "_lock", "_prompt", "_stream")

    def __init__(
        self,
        *,
        prompt: str = DEFAULT_PROMPT,
        stream: TextIO | None = None,
        interactive: bool | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._prompt = prompt
        self._lock = threading.Lock()
        self._interactive = self._stream.isatty() if interactive is None else interactive

    @property
    def prompt(self) -> str:
        """The prompt string, so no caller keeps a second copy of it."""
        return self._prompt

    @property
    def interactive(self) -> bool:
        """Whether this UI is drawing to a terminal."""
        return self._interactive

    def system(self, text: str) -> None:
        """Print a line the server generated rather than a user writing."""
        self._line(text, color=_CYAN)

    def chat(self, text: str) -> None:
        """Print user-visible chat traffic, which may span several lines."""
        self._line(text, color=None)

    def error(self, text: str) -> None:
        """Print a failure the user needs to see."""
        self._line(text, color=_RED)

    def banner(self, text: str) -> None:
        """Print the connection banner."""
        self._line(text, color=_DIM)

    def clear_line(self) -> None:
        """Erase the current line, leaving the cursor at its start.

        Called just before reading a line: the receiver has already reprinted the
        prompt after the last message, and ``input()`` is about to print another
        one on the same line.
        """
        if not self._interactive:
            return
        with self._lock:
            self._write(_CLEAR_LINE)

    def close(self) -> None:
        """Flush, so a redirected stream is complete even on a hard exit."""
        with self._lock:
            try:
                self._stream.flush()
            except (ValueError, OSError):
                pass

    def _line(self, text: str, *, color: str | None) -> None:
        """Write whole lines, holding the lock across the redraw.

        On a terminal each line is preceded by a line-clear and followed by a
        fresh prompt, which is what stops an arriving message from landing on top
        of what the user was typing. Off a terminal both are skipped.
        """
        with self._lock:
            if not self._interactive:
                self._write(f"{text}\n")
                return

            body = f"{color}{text}{_RESET}" if color else text
            self._write(f"{_CLEAR_LINE}{body}\n{self._prompt}")

    def _write(self, text: str) -> None:
        try:
            self._stream.write(text)
            self._stream.flush()
        except (ValueError, OSError):
            # A closed pipe, or a stdout detached at interpreter shutdown. It
            # cannot be reported to anyone, and raising from the receiver thread
            # would lose the message that follows.
            pass
