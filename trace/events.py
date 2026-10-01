"""Trace events: the single source of truth for layer-level observability.

Every layer in the stack (L4-L7) emits :class:`TraceEvent` objects through a
:class:`~trace.emitter.TraceEmitter`. Two consumers read those events:

* the CLI ``--trace`` printer, and
* the web visualizer, which receives them over the bridge's single WebSocket
  as ``{"type": "trace", ...}`` frames.

Both consume the *same* event objects produced by the *same* emitter. There is
deliberately no second tracing path: if the visualizer shows something the CLI
cannot, that is a bug in the sink, not a second implementation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum

from util.timeutil import to_iso8601

__all__ = [
    "Direction",
    "LAYER_NAMES",
    "LAYER_PDU_TYPES",
    "Layer",
    "Node",
    "PduType",
    "TraceEvent",
    "new_trace_id",
]


class Layer(IntEnum):
    """OSI layers this project actually implements, highest number on top."""

    APPLICATION = 7
    PRESENTATION = 6
    SESSION = 5
    TRANSPORT = 4


class Direction(str, Enum):
    """Whether a PDU is travelling down the stack or up it."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


class Node(str, Enum):
    """Which process emitted the event.

    ``BRIDGE`` is the web bridge service: from the chat server's point of view
    it is just another TCP client, but the visualizer needs to tell its events
    apart from a native CLI client's.
    """

    CLIENT = "client"
    BRIDGE = "bridge"
    SERVER = "server"


class PduType(str, Enum):
    """Protocol data unit name at the emitting layer.

    Layers 3-1 are handled by the OS and never emit events; see
    ``docs/OSI.md`` for the Wireshark evidence that covers them.
    """

    DATA = "data"
    MESSAGE = "message"
    SEGMENT = "segment"


#: Human-readable layer name, matching the ``layerName`` field of the trace
#: schema in the project specification.
LAYER_NAMES: dict[Layer, str] = {
    Layer.APPLICATION: "Application",
    Layer.PRESENTATION: "Presentation",
    Layer.SESSION: "Session",
    Layer.TRANSPORT: "Transport",
}

#: Textbook PDU name for each implemented layer.
LAYER_PDU_TYPES: dict[Layer, PduType] = {
    Layer.APPLICATION: PduType.DATA,
    Layer.PRESENTATION: PduType.MESSAGE,
    Layer.SESSION: PduType.MESSAGE,
    Layer.TRANSPORT: PduType.SEGMENT,
}


def new_trace_id() -> str:
    """Return a fresh trace identifier as a canonical UUID string."""
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One layer's view of one message, in one direction, at one instant.

    Instances are immutable: a sink that wants to annotate an event must copy
    it, so a consumer downstream can never mutate what another consumer is
    about to read.
    """

    trace_id: str
    session_id: str | None
    direction: Direction
    layer: Layer
    node: Node
    summary: str
    payload_preview: str
    payload_hex: str
    size_bytes: int
    timestamp: datetime

    def to_dict(self) -> dict[str, object]:
        """Serialise to the JSON shape the visualizer consumes.

        Field names are camelCase here on purpose: this dict is what crosses
        the bridge's WebSocket, so it is a wire format, not an internal
        Python structure.
        """
        return {
            "traceId": self.trace_id,
            "sessionId": self.session_id,
            "direction": self.direction.value,
            "layer": int(self.layer),
            "layerName": LAYER_NAMES[self.layer],
            "pduType": LAYER_PDU_TYPES[self.layer].value,
            "node": self.node.value,
            "summary": self.summary,
            "payloadPreview": self.payload_preview,
            "payloadHex": self.payload_hex,
            "sizeBytes": self.size_bytes,
            "timestamp": to_iso8601(self.timestamp),
        }
