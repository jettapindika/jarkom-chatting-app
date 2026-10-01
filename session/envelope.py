"""The L5 session envelope: a binary header wrapped around presentation bytes.

This is where encapsulation becomes visible. One chat message on the wire is::

    [ 4-byte length prefix ][ 24-byte session header ][ JSON payload ]
      ^ Transport (L4)        ^ Session (L5)            ^ Presentation (L6)

Reading the hex view of a frame, the first 24 bytes after the prefix are not
JSON -- they are the session header, and the ``7b 22`` (``{"``) that starts the
JSON only appears at offset 28. That is the point: each layer contributes its
own header, and the visualizer can show exactly which bytes came from which
layer.

Header layout::

    +--------+----------+--------------------------------------+
    | offset | size     | field                                |
    +--------+----------+--------------------------------------+
    | 0      | 16       | session id, raw UUID bytes           |
    | 16     | 8        | sequence number, uint64 big-endian   |
    +--------+----------+--------------------------------------+

Why binary and not more JSON
----------------------------
Two reasons. First, the sequence number is read on every single frame, and a
fixed-offset integer is cheaper to decode than a JSON parse. Second -- and more
importantly for this project -- a binary header makes the layering unmistakable.
If the session header were also JSON, the frame would be one undifferentiated
JSON blob and the "each layer adds its own header" claim would be unverifiable
by looking at the bytes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from session.errors import SessionEnvelopeError

__all__ = [
    "MAX_SEQUENCE",
    "SESSION_HEADER_SIZE",
    "UNASSIGNED_SESSION_ID",
    "SessionHeader",
    "decode_envelope",
    "encode_envelope",
    "is_unassigned",
]

#: Bytes occupied by the session header, excluding the transport length prefix.
SESSION_HEADER_SIZE = 24

#: Session id used before the handshake assigns a real one. A CONNECT frame is
#: sent before the server has told the client its session id, so the field must
#: have a defined placeholder rather than being omitted -- a variable-length
#: header would make the fixed-offset decode above impossible.
UNASSIGNED_SESSION_ID = "00000000-0000-0000-0000-000000000000"

_UUID_SIZE = 16
_SEQUENCE_SIZE = 8

#: Sequence numbers are unsigned 64-bit and start at 1, so 0 stays available as
#: an unambiguous "no messages sent yet" marker.
MAX_SEQUENCE = (1 << (_SEQUENCE_SIZE * 8)) - 1


@dataclass(frozen=True, slots=True)
class SessionHeader:
    """The L5 header: which session a PDU belongs to, and its position in it."""

    session_id: str
    sequence: int

    def __post_init__(self) -> None:
        if not 0 <= self.sequence <= MAX_SEQUENCE:
            raise SessionEnvelopeError(f"sequence {self.sequence} is outside 0..{MAX_SEQUENCE}")


def is_unassigned(session_id: str) -> bool:
    """Return whether ``session_id`` is still the pre-handshake placeholder."""
    return session_id == UNASSIGNED_SESSION_ID


def encode_envelope(
    payload: bytes,
    *,
    session_id: str = UNASSIGNED_SESSION_ID,
    sequence: int = 0,
) -> bytes:
    """Prefix ``payload`` with the L5 session header.

    Args:
        payload: presentation bytes from L6.
        session_id: canonical UUID string, or :data:`UNASSIGNED_SESSION_ID`
            before the handshake completes.
        sequence: per-session, per-direction sequence number.

    Returns:
        ``session id bytes || sequence || payload``.

    Raises:
        SessionEnvelopeError: if ``session_id`` is not a valid UUID, or
            ``sequence`` is out of range.
    """
    header = SessionHeader(session_id=session_id, sequence=sequence)
    return _encode_header(header) + payload


def decode_envelope(data: bytes) -> tuple[SessionHeader, bytes]:
    """Split a session envelope into its header and its presentation payload.

    Args:
        data: one complete frame payload from the transport layer.

    Returns:
        ``(header, payload)``.

    Raises:
        SessionEnvelopeError: if ``data`` is shorter than the header, which
            means the frame could not have been produced by this stack.
    """
    if len(data) < SESSION_HEADER_SIZE:
        raise SessionEnvelopeError(
            f"frame of {len(data)} bytes is shorter than the "
            f"{SESSION_HEADER_SIZE}-byte session header"
        )

    session_id = str(uuid.UUID(bytes=bytes(data[:_UUID_SIZE])))
    sequence = int.from_bytes(data[_UUID_SIZE:SESSION_HEADER_SIZE], "big")
    return SessionHeader(session_id=session_id, sequence=sequence), data[SESSION_HEADER_SIZE:]


def _encode_header(header: SessionHeader) -> bytes:
    """Render a header to its fixed-width binary form."""
    try:
        identifier = uuid.UUID(header.session_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise SessionEnvelopeError(f"invalid session id: {header.session_id!r}") from exc

    return identifier.bytes + header.sequence.to_bytes(_SEQUENCE_SIZE, "big")
