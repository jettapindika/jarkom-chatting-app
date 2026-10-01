"""A minimal RFC 6455 WebSocket server built on stdlib sockets.

The assignment forbids ready-made chat/socket libraries, and the same reasoning
applies to the browser hop: writing the handshake and the frame codec here is
the point, not boilerplate to be delegated. Only ``hashlib``, ``base64`` and
``struct`` are used, so the Python side of this project stays dependency-free.

Scope is deliberately narrow -- exactly the subset a browser needs:

* text and binary frames, with continuation reassembly for fragmented messages
* ping/pong, answered automatically
* close handshake
* masked client frames (the RFC requires clients to mask; we reject unmasked)

Not implemented, because nothing here needs them: extensions, subprotocol
negotiation, and server-to-client masking.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from typing import Iterator

__all__ = [
    "CLOSE_NORMAL",
    "CLOSE_PROTOCOL_ERROR",
    "WebSocket",
    "WebSocketClosed",
    "WebSocketError",
    "accept_key",
    "handshake",
]

#: The GUID from RFC 6455 section 1.3. Concatenated with the client's key and
#: hashed with SHA-1 to prove we understood the upgrade rather than echoing.
_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

MAX_HEADER_BYTES = 16 * 1024

#: Refuse frames larger than this. The chat protocol caps at 64 KiB, so anything
#: far above that is a bug or an attack, and either way we should not buffer it.
MAX_FRAME_BYTES = 4 * 1024 * 1024

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA

CLOSE_NORMAL = 1000
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_TOO_LARGE = 1009


class WebSocketError(Exception):
    """The peer sent something that is not valid WebSocket."""


class WebSocketClosed(Exception):
    """The peer closed the connection, normally or not."""


def accept_key(client_key: str) -> str:
    """Compute the ``Sec-WebSocket-Accept`` value for a client's key."""
    digest = hashlib.sha1(client_key.encode("ascii") + _GUID).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake(sock: socket.socket) -> dict[str, str]:
    """Read the HTTP upgrade request and send the 101 response.

    Returns the parsed request headers, lower-cased. Raises
    :class:`WebSocketError` if the request is not a valid WebSocket upgrade, so
    the caller can answer 400 rather than hanging.
    """
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("client closed during handshake")
        request += chunk
        if len(request) > MAX_HEADER_BYTES:
            raise WebSocketError("handshake headers too large")

    head, _, _rest = request.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    request_line = lines[0].split()
    if len(request_line) < 3 or request_line[0].upper() != "GET":
        raise WebSocketError(f"not a GET upgrade: {lines[0]!r}")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()

    key = headers.get("sec-websocket-key")
    if not key:
        raise WebSocketError("missing Sec-WebSocket-Key")
    if "websocket" not in headers.get("upgrade", "").lower():
        raise WebSocketError("missing Upgrade: websocket")

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n"
        "\r\n"
    )
    sock.sendall(response.encode("ascii"))
    return headers


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise WebSocketClosed("connection closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _build_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Serialise one server-to-client frame. Servers never mask."""
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, length)
    elif length < 65536:
        header = struct.pack("!BBH", first, 126, length)
    else:
        header = struct.pack("!BBQ", first, 127, length)
    return header + payload


class WebSocket:
    """One WebSocket connection, with a send lock so threads can share it.

    A chat bridge has two writers -- the TCP reader forwarding chat traffic and
    the trace sink forwarding layer events -- and interleaved writes would
    corrupt the frame stream. Every send therefore takes ``_send_lock``.
    """

    __slots__ = ("_closed", "_lock", "_released", "_send_lock", "_sock")

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self._lock = threading.Lock()
        self._closed = False
        self._released = False

    @classmethod
    def upgrade(cls, sock: socket.socket) -> "WebSocket":
        """Perform the HTTP handshake on an accepted socket."""
        handshake(sock)
        return cls(sock)

    @property
    def closed(self) -> bool:
        """Whether this connection has been closed."""
        with self._lock:
            return self._closed

    @property
    def peer(self) -> str:
        """The peer address, for logging."""
        try:
            host, port = self._sock.getpeername()
        except OSError:
            return "?"
        return f"{host}:{port}"

    def send_text(self, text: str) -> None:
        """Send a text frame. Raises :class:`WebSocketClosed` if already closed."""
        self._send(_build_frame(OPCODE_TEXT, text.encode("utf-8")))

    def send_bytes(self, payload: bytes) -> None:
        """Send a binary frame."""
        self._send(_build_frame(OPCODE_BINARY, payload))

    def ping(self, payload: bytes = b"") -> None:
        """Send a ping frame."""
        self._send(_build_frame(OPCODE_PING, payload))

    def _send(self, frame: bytes) -> None:
        with self._send_lock:
            if self._closed:
                raise WebSocketClosed("send on a closed connection")
            try:
                self._sock.sendall(frame)
            except OSError as exc:
                # A failed send means the connection is gone. Release the
                # descriptor here rather than waiting for close(), which the
                # caller may never reach -- send_json swallows this exception.
                with self._lock:
                    self._closed = True
                self._release()
                raise WebSocketClosed(f"send failed: {exc}") from exc

    def _read_frame(self) -> tuple[int, bool, bytes]:
        """Read one frame, unmasking it. Returns ``(opcode, fin, payload)``."""
        header = _recv_exactly(self._sock, 2)
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F

        if length == 126:
            (length,) = struct.unpack("!H", _recv_exactly(self._sock, 2))
        elif length == 127:
            (length,) = struct.unpack("!Q", _recv_exactly(self._sock, 8))

        if length > MAX_FRAME_BYTES:
            self.close(CLOSE_TOO_LARGE, "frame too large")
            raise WebSocketError(f"frame of {length} bytes exceeds the cap")

        # RFC 6455 section 5.1: a server MUST close on an unmasked client frame.
        if not masked:
            self.close(CLOSE_PROTOCOL_ERROR, "client frames must be masked")
            raise WebSocketError("unmasked client frame")

        mask = _recv_exactly(self._sock, 4)
        payload = bytearray(_recv_exactly(self._sock, length))
        for index in range(length):
            payload[index] ^= mask[index & 3]
        return opcode, fin, bytes(payload)

    def __iter__(self) -> Iterator[tuple[int, bytes]]:
        """Yield ``(opcode, payload)`` for each complete message until close.

        Fragmented messages are reassembled here, so callers see one item per
        logical message. Control frames are yielded as they arrive because they
        may legitimately be interleaved with a fragmented message.
        """
        pending: bytearray = bytearray()
        pending_opcode: int | None = None

        while True:
            try:
                opcode, fin, payload = self._read_frame()
            except WebSocketClosed:
                return
            except (OSError, struct.error) as exc:
                raise WebSocketClosed(f"read failed: {exc}") from exc

            if opcode == OPCODE_CLOSE:
                self.close(CLOSE_NORMAL, "")
                return
            if opcode == OPCODE_PING:
                self._send(_build_frame(OPCODE_PONG, payload))
                continue
            if opcode == OPCODE_PONG:
                continue

            if opcode == OPCODE_CONTINUATION:
                if pending_opcode is None:
                    self.close(CLOSE_PROTOCOL_ERROR, "continuation without a start")
                    raise WebSocketError("continuation frame with nothing to continue")
                pending += payload
            else:
                if pending_opcode is not None:
                    self.close(CLOSE_PROTOCOL_ERROR, "new message before the last finished")
                    raise WebSocketError("interleaved message start")
                pending_opcode = opcode
                pending = bytearray(payload)

            if len(pending) > MAX_FRAME_BYTES:
                self.close(CLOSE_TOO_LARGE, "message too large")
                raise WebSocketError("reassembled message exceeds the cap")

            if fin:
                yield pending_opcode, bytes(pending)
                pending = bytearray()
                pending_opcode = None

    def _release(self) -> None:
        """Shut down and close the socket exactly once.

        Separate from :meth:`close` because a send failure closes the
        connection without sending a close frame, and the descriptor must still
        be released -- otherwise the garbage collector reports it as leaked.
        """
        with self._lock:
            if self._released:
                return
            self._released = True

        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        """Send a close frame and shut the socket. Idempotent and thread-safe.

        The socket is released even when a previous send already failed: that
        path marks the connection closed without releasing it, and returning
        early here would leave the descriptor open.
        """
        with self._lock:
            should_send = not self._closed
            self._closed = True

        if should_send:
            payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
            try:
                with self._send_lock:
                    self._sock.sendall(_build_frame(OPCODE_CLOSE, payload))
            except OSError:
                pass

        self._release()


def random_mask() -> bytes:
    """Return a fresh 4-byte mask. Used by the tests' client side."""
    return os.urandom(4)


def build_client_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Serialise a masked client frame, as the tests need to speak the protocol."""
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    mask = random_mask()
    if length < 126:
        header = struct.pack("!BB", first, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", first, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, length)
    masked = bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))
    return header + mask + masked
