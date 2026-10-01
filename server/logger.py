"""Server-side logging: timestamped, level-filtered, one line per event.

Kept separate from the trace emitter on purpose. The two answer different
questions:

* **This log** is the operator's view -- who connected, who left, what broke --
  and it is always on.
* **The trace** is the packet inspector's view -- the bytes each layer handled
  for one message -- and it is off unless ``--trace`` asks for it.

Folding them together would either flood the log with per-byte noise or leave
the trace with no record of the events that never reached a layer.
"""

from __future__ import annotations

import logging
import sys
import time

from util.timeutil import utc_now

__all__ = ["DEFAULT_LOG_FORMAT", "configure_logging", "get_logger"]

#: ``2026-10-01T06:31:17.482Z  INFO      server      listening on 0.0.0.0:9009``
DEFAULT_LOG_FORMAT = "%(asctime)s  %(levelname)-9s %(name)-11s %(message)s"

#: ``asctime`` in UTC, to match every timestamp the protocol puts on the wire.
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


class _UtcFormatter(logging.Formatter):
    """Renders ``asctime`` in UTC with a trailing ``Z``.

    The default formatter uses local time, which would put log lines and wire
    timestamps in different zones -- exactly the confusion the protocol's
    server-authoritative UTC decision exists to avoid.
    """

    converter = staticmethod(time.gmtime)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        stamp = super().formatTime(record, datefmt)
        return f"{stamp}.{int(record.msecs):03d}Z"


def configure_logging(level: str = "INFO", *, stream=None) -> None:
    """Install the server's log format on the root logger.

    Args:
        level: ``DEBUG``/``INFO``/``WARNING``/``ERROR``/``CRITICAL``.
        stream: destination, defaulting to stderr. Diagnostics on stderr keep
            stdout free for anything a caller wants to pipe.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(_UtcFormatter(DEFAULT_LOG_FORMAT, DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    """Return the logger for one component, e.g. ``get_logger("server")``."""
    return logging.getLogger(name)
