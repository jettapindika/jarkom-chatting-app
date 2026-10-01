"""TCP framing: a 4-byte big-endian length prefix in front of every payload.

Why a length prefix rather than a newline delimiter
---------------------------------------------------
TCP is a byte stream; it preserves order and delivers *bytes*, not messages. A
delimiter scheme has to answer a question it cannot answer cheaply: where does
this message end? That forces the receiver to scan every arriving byte and to
buffer without knowing how much it will eventually need. A length prefix answers
the question in the first four bytes, which buys three things:

1. **Allocation is bounded before the payload exists.** The decoder rejects an
   oversized frame from the prefix alone, before a single payload byte is
   buffered. With a delimiter, a hostile peer can make the receiver accumulate
   indefinitely by simply never sending the delimiter.
2. **No escaping.** A delimiter must either be forbidden inside payloads or
   escaped, and JSON does not forbid it: ``json.dumps`` with default settings
   emits no raw ``0x0A``, but ``json.dumps(..., indent=2)`` emits plenty. A
   length prefix makes the question moot, and the payload stays plain JSON --
   inspectable in Wireshark, copy-pasteable into a test fixture.
3. **Framing is independent of payload semantics.** The transport layer never
   parses JSON, so a malformed payload is a Presentation-layer concern that
   cannot corrupt the frame boundaries of the messages around it.

The cost is four bytes per message and the requirement that both ends agree on
byte order. Big-endian (network byte order) is the conventional choice and is
what a reader of the Wireshark hex view expects.

Frame layout::

    +--------+--------+--------+--------+----------------------------+
    | 0x00   | 0x00   | 0x01   | 0x70   | {"type":"BROADCAST", ...}  |
    +--------+--------+--------+--------+----------------------------+
     4-byte length prefix (368, big-endian)   payload, exactly 368 bytes
"""

from __future__ import annotations

from typing import Iterator

from transport.errors import FrameTooLargeError

__all__ = [
    "LENGTH_PREFIX_SIZE",
    "MAX_FRAME_SIZE",
    "FrameDecoder",
    "encode_frame",
    "frame_length",
]

#: Width of the length prefix in bytes.
LENGTH_PREFIX_SIZE = 4

#: Largest payload the protocol accepts, in bytes. 64 KiB is far above any chat
#: message or file-transfer chunk and far below anything that would make one
#: client's buffering a denial-of-service risk.
MAX_FRAME_SIZE = 64 * 1024

#: Consumed bytes tolerated at the front of the reassembly buffer before it is
#: shifted down. Without this, a long-lived stream that pops one small frame at
#: a time would memmove the whole buffer on every frame.
_COMPACT_THRESHOLD = 8192


def encode_frame(payload: bytes, *, max_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    """Wrap ``payload`` in a length-prefixed frame.

    Args:
        payload: the PDU bytes to frame. Must already be encoded.
        max_frame_size: largest payload this side will send.

    Returns:
        ``len(payload).to_bytes(4, "big") + payload``.

    Raises:
        FrameTooLargeError: if ``payload`` exceeds ``max_frame_size``. Checking
            on send as well as on receive means an oversized message fails at
            the sender, where the cause is still visible, instead of silently
            killing the peer's connection.
    """
    size = len(payload)
    if size > max_frame_size:
        raise FrameTooLargeError(size, max_frame_size)
    return size.to_bytes(LENGTH_PREFIX_SIZE, "big") + payload


def frame_length(prefix: bytes) -> int:
    """Decode a 4-byte big-endian length prefix into a payload size.

    Raises:
        ValueError: if ``prefix`` is not exactly :data:`LENGTH_PREFIX_SIZE` bytes.
    """
    if len(prefix) != LENGTH_PREFIX_SIZE:
        raise ValueError(f"prefix must be exactly {LENGTH_PREFIX_SIZE} bytes")
    return int.from_bytes(prefix, "big")


class FrameDecoder:
    """Incremental frame decoder for a TCP byte stream.

    TCP guarantees neither that one ``recv`` returns one message nor that a
    message arrives in one piece. The decoder therefore owns reassembly: feed it
    whatever bytes arrive, in whatever sizes, and pull complete frames out. A
    frame split across ten reads and ten frames arriving in one read both work,
    without the caller tracking anything.

    The decoder is deliberately not thread-safe. One decoder belongs to one
    connection and is touched by one reader.
    """

    __slots__ = ("_buffer", "_max_frame_size", "_start")

    def __init__(self, *, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        if max_frame_size <= 0:
            raise ValueError("max_frame_size must be positive")
        self._buffer = bytearray()
        self._start = 0
        self._max_frame_size = max_frame_size

    @property
    def max_frame_size(self) -> int:
        """Largest payload this decoder will accept."""
        return self._max_frame_size

    @property
    def buffered_bytes(self) -> int:
        """Bytes held back awaiting the rest of their frame."""
        return len(self._buffer) - self._start

    def feed(self, chunk: bytes) -> None:
        """Append freshly received bytes to the reassembly buffer."""
        if chunk:
            self._buffer += chunk

    def pop_frame(self) -> bytes | None:
        """Return the next complete frame, or ``None`` if more bytes are needed.

        ``None`` covers both a partial payload and a length prefix that has not
        fully arrived. That is the normal resting state of a stream decoder, not
        an error, so it is a return value rather than an exception.

        Raises:
            FrameTooLargeError: if the length prefix announces more than
                ``max_frame_size``. The check happens as soon as the prefix is
                available, so an oversized frame is refused before its payload
                is buffered.
        """
        available = len(self._buffer) - self._start
        if available < LENGTH_PREFIX_SIZE:
            self._maybe_compact()
            return None

        start = self._start
        length = frame_length(self._buffer[start : start + LENGTH_PREFIX_SIZE])

        if length > self._max_frame_size:
            raise FrameTooLargeError(length, self._max_frame_size)

        if available < LENGTH_PREFIX_SIZE + length:
            self._maybe_compact()
            return None

        payload_start = start + LENGTH_PREFIX_SIZE
        payload = bytes(self._buffer[payload_start : payload_start + length])
        self._start = payload_start + length
        self._maybe_compact()
        return payload

    def frames(self) -> Iterator[bytes]:
        """Yield every complete frame currently buffered, draining the buffer."""
        while True:
            frame = self.pop_frame()
            if frame is None:
                return
            yield frame

    def reset(self) -> None:
        """Drop all buffered bytes.

        Used when a connection is reused for a new session, so a half-read frame
        from the old one cannot leak into the new one.
        """
        self._buffer.clear()
        self._start = 0

    def _maybe_compact(self) -> None:
        """Reclaim the already-consumed front of the buffer."""
        if self._start == 0:
            return
        if self._start == len(self._buffer):
            self._buffer.clear()
            self._start = 0
        elif self._start >= _COMPACT_THRESHOLD:
            del self._buffer[: self._start]
            self._start = 0
