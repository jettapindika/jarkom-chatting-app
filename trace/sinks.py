"""Where trace events go.

A sink is anything that can accept a :class:`~trace.events.TraceEvent`. The
stack never knows which sinks are attached, which is what lets the CLI printer
and the WebSocket broadcaster share one emission path.
"""

from __future__ import annotations

import json
from typing import IO, Protocol, runtime_checkable

from trace.events import TraceEvent

__all__ = [
    "CollectingTraceSink",
    "FanoutTraceSink",
    "JsonLinesTraceSink",
    "NullTraceSink",
    "TraceSink",
]


@runtime_checkable
class TraceSink(Protocol):
    """Consumer of trace events."""

    def emit(self, event: TraceEvent) -> None:
        """Accept one event. Implementations must not raise on normal input."""
        ...


class NullTraceSink:
    """Discards every event. Used when tracing is disabled."""

    __slots__ = ()

    def emit(self, event: TraceEvent) -> None:
        """Do nothing."""


class CollectingTraceSink:
    """Keeps events in memory, in arrival order.

    This is what the test suite asserts against, and what a UI can replay from.
    """

    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        """Drop every collected event."""
        self.events.clear()

    def for_trace(self, trace_id: str) -> list[TraceEvent]:
        """Return the events belonging to one message, in arrival order."""
        return [event for event in self.events if event.trace_id == trace_id]


class JsonLinesTraceSink:
    """Writes one JSON object per line to a text stream.

    This is the ``--trace`` output format: greppable, diffable, and directly
    consumable by the visualizer's replay mode without a parser change.
    """

    __slots__ = ("_stream",)

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    def emit(self, event: TraceEvent) -> None:
        self._stream.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._stream.flush()


class FanoutTraceSink:
    """Broadcasts each event to several sinks.

    The bridge uses this to feed its ``/ws/trace`` subscribers *and* its own
    log file from a single emission, so the two can never disagree.
    """

    __slots__ = ("_sinks",)

    def __init__(self, sinks: list[TraceSink] | None = None) -> None:
        self._sinks: list[TraceSink] = list(sinks or [])

    def add(self, sink: TraceSink) -> None:
        """Attach another sink."""
        self._sinks.append(sink)

    def remove(self, sink: TraceSink) -> None:
        """Detach a sink; a no-op if it was never attached."""
        if sink in self._sinks:
            self._sinks.remove(sink)

    @property
    def sink_count(self) -> int:
        """Number of attached sinks."""
        return len(self._sinks)

    def emit(self, event: TraceEvent) -> None:
        for sink in tuple(self._sinks):
            sink.emit(event)
