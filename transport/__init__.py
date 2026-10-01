"""OSI L4: reliable byte transport with length-prefixed framing.

Layers above import from here and never touch a socket directly.
"""

from transport.channel import (
    DEFAULT_CONNECT_TIMEOUT,
    READ_CHUNK_SIZE,
    AsyncTcpChannel,
    BlockingTcpChannel,
)
from transport.errors import (
    ConnectionClosedError,
    FrameTooLargeError,
    FramingError,
    TransportError,
    TransportTimeout,
)
from transport.framing import (
    LENGTH_PREFIX_SIZE,
    MAX_FRAME_SIZE,
    FrameDecoder,
    encode_frame,
    frame_length,
)

__all__ = [
    "AsyncTcpChannel",
    "BlockingTcpChannel",
    "ConnectionClosedError",
    "DEFAULT_CONNECT_TIMEOUT",
    "FrameDecoder",
    "FrameTooLargeError",
    "FramingError",
    "LENGTH_PREFIX_SIZE",
    "MAX_FRAME_SIZE",
    "READ_CHUNK_SIZE",
    "TransportError",
    "TransportTimeout",
    "encode_frame",
    "frame_length",
]
