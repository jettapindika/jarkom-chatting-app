"""The one entry point layers use to publish trace events."""

from __future__ import annotations

import os
from typing import Mapping

from trace.events import (
    Direction,
    LAYER_NAMES,
    LAYER_PDU_TYPES,
    Layer,
    Node,
    TraceEvent,
    new_trace_id,
)
from trace.sinks import NullTraceSink, TraceSink
from util.hexdump import hex_dump
from util.timeutil import utc_now

__all__ = ["DEFAULT_HEX_LIMIT", "DEFAULT_PREVIEW_LIMIT", "TRACE_ENABLED_ENV", "TraceEmitter"]

#: Maximum characters of human-readable payload kept in an event.
DEFAULT_PREVIEW_LIMIT = 512

#: Maximum bytes rendered as hex in an event. A 64 KiB message would otherwise
#: produce a ~192 KiB hex string per event, per layer.
DEFAULT_HEX_LIMIT = 128

#: Environment switch consulted by :meth:`TraceEmitter.from_env`.
TRACE_ENABLED_ENV = "TRACE_ENABLED"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class TraceEmitter:
    """Builds and publishes :class:`TraceEvent` objects for one stack instance.

    The emitter is bound to a node (client, bridge or server) and optionally to
    a session. Layers call :meth:`emit`; they never construct events by hand and
    never touch a sink directly.

    When tracing is disabled the emitter returns before building anything, so
    the cost of an unused ``--trace`` flag is one attribute check per layer per
    message rather than a discarded object allocation.
    """

    __slots__ = ("_enabled", "_hex_limit", "_node", "_preview_limit", "_session_id", "_sink")

    def __init__(
        self,
        sink: TraceSink | None = None,
        *,
        node: Node = Node.CLIENT,
        session_id: str | None = None,
        enabled: bool = True,
        preview_limit: int = DEFAULT_PREVIEW_LIMIT,
        hex_limit: int = DEFAULT_HEX_LIMIT,
    ) -> None:
        if preview_limit < 0:
            raise ValueError("preview_limit must be >= 0")
        if hex_limit < 0:
            raise ValueError("hex_limit must be >= 0")

        self._sink: TraceSink = sink if sink is not None else NullTraceSink()
        self._node = node
        self._session_id = session_id
        self._enabled = enabled
        self._preview_limit = preview_limit
        self._hex_limit = hex_limit

    @classmethod
    def from_env(
        cls,
        sink: TraceSink | None = None,
        *,
        node: Node = Node.CLIENT,
        enabled: bool | None = None,
        environ: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> "TraceEmitter":
        """Build an emitter whose on/off state comes from ``TRACE_ENABLED``.

        An explicit ``enabled`` argument wins over the environment so a
        ``--trace`` CLI flag can force tracing on without the operator having to
        export a variable.
        """
        source = os.environ if environ is None else environ
        if enabled is None:
            raw = source.get(TRACE_ENABLED_ENV)
            enabled = raw is not None and raw.strip().lower() in _TRUTHY

        return cls(sink, node=node, enabled=enabled, **kwargs)  # type: ignore[arg-type]

    @property
    def enabled(self) -> bool:
        """Whether events are currently published."""
        return self._enabled

    @property
    def node(self) -> Node:
        """The node this emitter speaks for."""
        return self._node

    @property
    def session_id(self) -> str | None:
        """The bound session id, if any."""
        return self._session_id

    def set_session_id(self, session_id: str | None) -> None:
        """Bind the emitter to a session once the handshake assigns one."""
        self._session_id = session_id

    def set_enabled(self, enabled: bool) -> None:
        """Turn tracing on or off at runtime."""
        self._enabled = enabled

    def emit(
        self,
        *,
        layer: Layer,
        direction: Direction,
        summary: str,
        trace_id: str | None = None,
        payload: bytes = b"",
        preview: str | None = None,
    ) -> str | None:
        """Publish one layer event and return the trace id it was filed under.

        Args:
            layer: the emitting OSI layer.
            direction: ``OUTBOUND`` when data moves down the stack,
                ``INBOUND`` when it moves up.
            summary: one-line description of what the layer just did.
            trace_id: id correlating every event for one message. Generated
                when omitted, which is what lets an application layer emit
                before it knows the id.
            payload: the PDU bytes as they exist at this layer, used for
                ``sizeBytes`` and ``payloadHex``.
            preview: human-readable form. Defaults to the payload decoded as
                UTF-8, which is right for L6/L7 and wrong for binary headers,
                so those layers pass their own summary text instead.

        Returns:
            The trace id used, or ``None`` when tracing is disabled.
        """
        if not self._enabled:
            return None

        resolved_id = trace_id if trace_id is not None else new_trace_id()
        text = preview if preview is not None else _decode_utf8(payload)

        event = TraceEvent(
            trace_id=resolved_id,
            session_id=self._session_id,
            direction=direction,
            layer=layer,
            node=self._node,
            summary=summary,
            payload_preview=_clip(text, self._preview_limit),
            payload_hex=hex_dump(payload, limit=self._hex_limit),
            size_bytes=len(payload),
            timestamp=utc_now(),
        )
        self._sink.emit(event)
        return resolved_id


def _decode_utf8(payload: bytes) -> str:
    """Decode ``payload`` for display, never raising on invalid bytes."""
    return payload.decode("utf-8", errors="replace")


def _clip(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` characters, marking what was dropped."""
    if limit == 0:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(+{len(text) - limit} chars)"
