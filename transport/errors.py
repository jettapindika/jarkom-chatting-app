"""Transport-layer (OSI L4) exceptions."""

from __future__ import annotations

__all__ = [
    "ConnectionClosedError",
    "FrameTooLargeError",
    "FramingError",
    "TransportError",
    "TransportTimeout",
]


class TransportError(Exception):
    """Base class for every failure raised by the L4 transport layer."""


class FramingError(TransportError):
    """The byte stream could not be cut back into frames."""


class FrameTooLargeError(FramingError):
    """A length prefix announced a frame larger than the agreed maximum.

    Raised from the prefix alone, before the payload is buffered, so a peer
    cannot make us allocate unbounded memory by promising a huge frame and then
    trickling it in. This is the defence a newline-delimited protocol cannot
    offer, and it is the main reason this protocol is length-prefixed.

    The stream position is unknowable after this error -- the decoder does not
    attempt to resynchronise -- so the caller must tear the connection down.
    """

    def __init__(self, announced_size: int, max_frame_size: int) -> None:
        self.announced_size = announced_size
        self.max_frame_size = max_frame_size
        super().__init__(
            f"announced frame of {announced_size} bytes exceeds maximum of {max_frame_size} bytes"
        )


class ConnectionClosedError(TransportError):
    """The peer closed the connection, or it was closed locally.

    A clean EOF is an ordinary event in a chat protocol, not an exceptional
    one, but it still has to unwind a blocking ``receive_frame`` call, so it
    travels as an exception rather than a sentinel return value.
    """

    def __init__(self, message: str = "connection closed by peer") -> None:
        super().__init__(message)


class TransportTimeout(TransportError):
    """A bounded ``receive_frame`` waited past its deadline without a frame.

    Only raised when the caller supplied a timeout. It means "nothing arrived",
    not "the connection is broken" -- the caller decides which of the two a
    silence implies.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"no frame received within {timeout:g}s")
