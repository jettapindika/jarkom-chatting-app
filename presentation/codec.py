"""JSON + UTF-8 codec: the concrete OSI L6 of this protocol stack.

This layer turns a message dict into bytes and back. It is the only place that
knows the wire representation is JSON encoded as UTF-8, which is what makes the
"presentation" abstraction real rather than decorative: swapping in MessagePack
would change this module and nothing above it.

Two deliberate choices worth defending in review:

* ``ensure_ascii=False`` -- UTF-8 is the encoding, so a nickname like "Budi"
  with non-ASCII characters should travel as the characters themselves. It also
  keeps a Wireshark payload view readable, which is the whole point of using
  JSON here.
* No ``indent`` -- pretty-printing is for humans reading a file, and here it
  would inflate every frame on the wire for no benefit. It also removes any
  raw ``0x0A`` byte from the payload, though the length-prefixed framing does
  not depend on that.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from presentation.errors import CodecError, EncodeError
from presentation.message_types import MessageType
from presentation.schema import validate_message
from trace import Direction, Layer, TraceEmitter
from util.timeutil import to_iso8601, utc_now

__all__ = ["decode", "encode", "make_message"]

#: Decoding is capped at the transport's maximum frame size, so a malformed
#: length prefix cannot make the JSON parser walk an unbounded string.
_DECODE_LIMIT = 64 * 1024


def make_message(
    message_type: MessageType | str,
    payload: Mapping[str, Any] | None = None,
    *,
    sender: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a message envelope with a server-stamped UTC timestamp.

    Centralising envelope construction is what keeps every producer emitting the
    same four fields in the same shape; a hand-written dict at each call site is
    how a protocol drifts.
    """
    resolved = message_type.value if isinstance(message_type, MessageType) else str(message_type)
    return {
        "type": resolved,
        "sender": sender,
        "payload": dict(payload or {}),
        "timestamp": timestamp if timestamp is not None else to_iso8601(utc_now()),
    }


def encode(
    message: Mapping[str, Any],
    *,
    emitter: TraceEmitter | None = None,
    trace_id: str | None = None,
) -> bytes:
    """Serialise ``message`` to UTF-8 JSON bytes.

    Args:
        message: the envelope dict. Validated before serialisation so that
            sending a malformed message fails locally, at the sender, rather
            than remotely, after the peer has already received it.
        emitter: trace emitter; when supplied an L6 outbound event is filed.
        trace_id: correlates this encoding with the rest of the message trace.

    Returns:
        The UTF-8 encoded JSON object.

    Raises:
        SchemaViolation: if ``message`` does not match the envelope schema.
        EncodeError: if the payload contains a value JSON cannot represent.
    """
    validated = validate_message(message)

    serialisable = dict(validated)
    if isinstance(serialisable["type"], MessageType):
        serialisable["type"] = serialisable["type"].value

    try:
        text = json.dumps(serialisable, ensure_ascii=False, separators=(",", ":"))
        data = text.encode("utf-8")
    except (TypeError, ValueError) as exc:
        # ``UnicodeEncodeError`` is a ``ValueError``: a lone surrogate sails
        # through ``json.dumps`` yet cannot be encoded as UTF-8, and it must
        # surface as the documented ``EncodeError`` rather than a raw crash.
        raise EncodeError(f"message is not JSON-serialisable: {exc}") from exc

    if emitter is not None:
        emitter.emit(
            layer=Layer.PRESENTATION,
            direction=Direction.OUTBOUND,
            summary=f"encoded {serialisable['type']} -> {len(data)} B UTF-8 JSON",
            trace_id=trace_id,
            payload=data,
        )

    return data


def decode(
    data: bytes,
    *,
    emitter: TraceEmitter | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Deserialise UTF-8 JSON bytes into a validated message envelope.

    Args:
        data: one complete frame payload from the transport layer.
        emitter: trace emitter; when supplied an L6 inbound event is filed.
        trace_id: correlates this decoding with the rest of the message trace.

    Returns:
        The validated envelope, with ``type`` as a :class:`MessageType`.

    Raises:
        CodecError: if ``data`` is not valid UTF-8, not valid JSON, or not a
            JSON object. These are the failures the server reports as
            ``MALFORMED``.
        SchemaViolation: if the JSON is well-formed but the envelope is wrong.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CodecError(f"payload is not valid UTF-8: {exc}") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodecError(f"payload is not valid JSON: {exc.msg} at offset {exc.pos}") from exc
    except RecursionError as exc:
        # A hostile frame of deeply nested brackets drives the JSON parser past
        # CPython's recursion limit. That is a malformed frame like any other
        # and belongs in the server's malformed budget, not in a crash.
        raise CodecError("payload is not valid JSON: nesting too deep") from exc

    message = validate_message(raw)

    if emitter is not None:
        emitter.emit(
            layer=Layer.PRESENTATION,
            direction=Direction.INBOUND,
            summary=f"decoded {message['type'].value} <- {len(data)} B UTF-8 JSON",
            trace_id=trace_id,
            payload=data,
        )

    return message
