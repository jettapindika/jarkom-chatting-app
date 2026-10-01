"""Concrete TCP channels: the OSI L4 of this protocol stack.

Everything above this layer exchanges *payloads*. Only this module knows about
sockets, byte streams and length prefixes, so swapping TCP for a Unix socket or
a test double means replacing these classes and nothing else.

Two implementations, because the server and the client genuinely differ:

* :class:`AsyncTcpChannel` -- asyncio streams, used by the server, where one
  event loop multiplexes many connections and nothing may block it.
* :class:`BlockingTcpChannel` -- a plain socket, used by the CLI client, whose
  reader and writer live in separate threads, and by tests, which want to
  assert on wire bytes without driving a loop.

Both expose the same four operations, which is the interface the specification
names as ``Transport.send_frame(bytes)``. Callers hand down opaque bytes and
receive opaque bytes back.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

from trace import Direction, Layer, TraceEmitter
from transport.errors import ConnectionClosedError, TransportTimeout
from transport.framing import (
    LENGTH_PREFIX_SIZE,
    MAX_FRAME_SIZE,
    FrameDecoder,
    encode_frame,
)

__all__ = ["AsyncTcpChannel", "BlockingTcpChannel", "READ_CHUNK_SIZE"]

#: Bytes requested per read. Large enough that a peer sending back-to-back
#: frames is drained in few syscalls, small enough that the buffer stays
#: negligible per connection.
READ_CHUNK_SIZE = 65536

#: Default TCP connect timeout, in seconds.
DEFAULT_CONNECT_TIMEOUT = 10.0


class AsyncTcpChannel:
    """Length-prefixed TCP channel over asyncio streams.

    The class is intentionally dumb: it frames, it moves bytes, it reports
    errors. It does not know what a nickname is, does not parse JSON, and never
    inspects a payload.

    No send lock is needed. :func:`encode_frame` and ``StreamWriter.write`` are
    both synchronous, so a task cannot be preempted between building a frame and
    handing it to the transport -- the frame is already contiguous in the write
    buffer by the time the ``await`` yields.
    """

    __slots__ = ("_closed", "_decoder", "_emitter", "_reader", "_writer")

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        emitter: TraceEmitter | None = None,
        max_frame_size: int = MAX_FRAME_SIZE,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._decoder = FrameDecoder(max_frame_size=max_frame_size)
        self._emitter = emitter
        self._closed = False

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        *,
        emitter: TraceEmitter | None = None,
        max_frame_size: int = MAX_FRAME_SIZE,
        connect_timeout: float | None = DEFAULT_CONNECT_TIMEOUT,
    ) -> "AsyncTcpChannel":
        """Open a client-side connection.

        Args:
            host: server hostname or address.
            port: server port.
            emitter: trace emitter for this node.
            max_frame_size: largest payload this side will accept.
            connect_timeout: seconds to wait for the TCP handshake. ``None``
                waits indefinitely, which a CLI almost never wants.

        Returns:
            A connected channel.

        Raises:
            OSError: on refusal, DNS failure, or timeout.
        """
        if connect_timeout is None:
            reader, writer = await asyncio.open_connection(host, port)
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=connect_timeout
            )
        return cls(reader, writer, emitter=emitter, max_frame_size=max_frame_size)

    @property
    def peer_name(self) -> str:
        """Remote ``host:port``, or ``"unknown"`` when the socket is gone."""
        return _format_address(self._writer.get_extra_info("peername"))

    @property
    def local_name(self) -> str:
        """Local ``host:port`` this connection is bound to."""
        return _format_address(self._writer.get_extra_info("sockname"))

    @property
    def is_closed(self) -> bool:
        """Whether :meth:`close` has already run."""
        return self._closed

    @property
    def emitter(self) -> TraceEmitter | None:
        """The trace emitter this channel reports through, if any.

        Exposed so a driver built on top of the channel can reuse it instead of
        requiring the caller to thread the same emitter through twice.
        """
        return self._emitter

    @property
    def max_frame_size(self) -> int:
        """Largest payload this channel will accept."""
        return self._decoder.max_frame_size

    async def send_frame(self, payload: bytes, *, trace_id: str | None = None) -> None:
        """Frame ``payload`` and write it to the socket.

        Args:
            payload: PDU bytes from the layer above.
            trace_id: correlates this hop with the rest of the message's trace.

        Raises:
            FrameTooLargeError: if the payload exceeds the negotiated maximum.
            ConnectionClosedError: if the channel is already closed.
            OSError: if the socket fails mid-write.
        """
        if self._closed:
            raise ConnectionClosedError("cannot send on a closed channel")

        frame = encode_frame(payload, max_frame_size=self.max_frame_size)
        try:
            self._writer.write(frame)
            await self._writer.drain()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
            # The peer went away mid-write. That is the same condition as EOF on
            # the read side, so it is reported as the same thing -- a layer
            # above should not have to know which errno means "gone".
            raise ConnectionClosedError(f"connection lost while sending: {exc}") from exc

        self._emit(Layer.TRANSPORT, Direction.OUTBOUND, frame, payload, trace_id)

    async def receive_frame(self, *, trace_id: str | None = None) -> bytes:
        """Read until one complete frame has arrived and return its payload.

        Returns:
            The payload, with the length prefix stripped.

        Raises:
            ConnectionClosedError: on EOF, including EOF in the middle of a
                frame -- a peer that dies mid-message must not be mistaken for a
                peer that sent a short message.
            FrameTooLargeError: if the announced size exceeds the maximum.
        """
        while True:
            frame = self._decoder.pop_frame()
            if frame is not None:
                # The decoder hands back the payload with the prefix stripped.
                # The event reports what this layer put on the wire, so the
                # prefix is restored for the layer's own hex view.
                self._emit(Layer.TRANSPORT, Direction.INBOUND, _framed(frame), frame, trace_id)
                return frame

            if self._closed:
                raise ConnectionClosedError("channel was closed while waiting for a frame")

            try:
                chunk = await self._reader.read(READ_CHUNK_SIZE)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
                # A peer that vanishes abruptly sends RST rather than FIN, so the
                # read raises instead of returning empty. Same event, same
                # exception: from here up, "the peer is gone" is one condition.
                raise ConnectionClosedError(f"connection lost: {exc}") from exc

            if not chunk:
                raise ConnectionClosedError(_eof_message(self._decoder))
            self._decoder.feed(chunk)

    async def close(self) -> None:
        """Close the connection, waiting for the transport to finish closing.

        Safe to call repeatedly and safe to call after :meth:`abort`: closing an
        already-closing transport is a no-op, and the ``wait_closed`` still
        drains the transport's own shutdown so nothing is left dangling.
        """
        self._closed = True
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    def abort(self) -> None:
        """Close the socket immediately, without waiting for the flush.

        Called when the decision to disconnect came from somewhere other than
        the reader -- a slow-consumer drop, or a server shutdown. Closing the
        transport is what unblocks a reader parked in :meth:`receive_frame`;
        flagging a private field would leave it awaiting a socket nobody is
        going to write to.
        """
        if self._closed:
            return
        self._closed = True
        self._writer.close()

    def _emit(
        self,
        layer: Layer,
        direction: Direction,
        frame: bytes,
        payload: bytes,
        trace_id: str | None,
    ) -> None:
        if self._emitter is None:
            return
        self._emitter.emit(
            layer=layer,
            direction=direction,
            summary=_summary(direction, payload, self.peer_name),
            trace_id=trace_id,
            payload=frame,
        )


class BlockingTcpChannel:
    """Length-prefixed TCP channel over a blocking socket.

    Used by the CLI client, where the reader thread blocks in ``recv`` while the
    writer thread blocks in ``input()``. Two threads sharing one socket is safe
    here because the two directions are independent; concurrent *sends* are not,
    so :meth:`send_frame` serialises them behind a lock.
    """

    __slots__ = ("_closed", "_decoder", "_emitter", "_local_name", "_lock", "_peer_name", "_sock")

    def __init__(
        self,
        sock: socket.socket,
        *,
        emitter: TraceEmitter | None = None,
        max_frame_size: int = MAX_FRAME_SIZE,
    ) -> None:
        self._sock = sock
        self._decoder = FrameDecoder(max_frame_size=max_frame_size)
        self._emitter = emitter
        self._lock = threading.Lock()
        self._closed = False
        # Resolved once, at connect time. ``getpeername`` on a closed socket
        # raises, and the disconnect log needs the address precisely then.
        self._peer_name = _format_address(_safe_extra(sock, "peername"))
        self._local_name = _format_address(_safe_extra(sock, "sockname"))

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        *,
        emitter: TraceEmitter | None = None,
        max_frame_size: int = MAX_FRAME_SIZE,
        connect_timeout: float | None = DEFAULT_CONNECT_TIMEOUT,
    ) -> "BlockingTcpChannel":
        """Open a client-side connection.

        Raises:
            OSError: on refusal, DNS failure, or timeout. A timed-out connect
                raises :class:`socket.timeout`, which is an ``OSError``.
        """
        sock = socket.create_connection((host, port), timeout=connect_timeout)
        # Blocking mode from here on: the reader thread wants an open-ended
        # blocking recv, and heartbeat deadlines are enforced above this layer
        # by passing an explicit timeout to receive_frame.
        sock.settimeout(None)
        return cls(sock, emitter=emitter, max_frame_size=max_frame_size)

    @property
    def socket(self) -> socket.socket:
        """The underlying socket, for tests that need to provoke a hard failure."""
        return self._sock

    @property
    def peer_name(self) -> str:
        """Remote ``host:port``, captured when the connection was established."""
        return self._peer_name

    @property
    def local_name(self) -> str:
        """Local ``host:port`` this connection is bound to."""
        return self._local_name

    @property
    def is_closed(self) -> bool:
        """Whether :meth:`close` has already run."""
        return self._closed

    @property
    def emitter(self) -> TraceEmitter | None:
        """The trace emitter this channel reports through, if any."""
        return self._emitter

    @property
    def max_frame_size(self) -> int:
        """Largest payload this channel will accept."""
        return self._decoder.max_frame_size

    def send_frame(self, payload: bytes, *, trace_id: str | None = None) -> None:
        """Frame ``payload`` and write it to the socket.

        Raises:
            FrameTooLargeError: if the payload exceeds the negotiated maximum.
            ConnectionClosedError: if the channel is already closed.
            OSError: if the socket fails mid-write.
        """
        frame = encode_frame(payload, max_frame_size=self.max_frame_size)

        with self._lock:
            if self._closed:
                raise ConnectionClosedError("cannot send on a closed channel")
            try:
                self._sock.sendall(frame)
            except OSError as exc:
                raise ConnectionClosedError(f"send failed: {exc}") from exc

        self._emit(Direction.OUTBOUND, frame, payload, trace_id)

    def receive_frame(
        self,
        *,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> bytes:
        """Block until one complete frame has arrived and return its payload.

        Args:
            trace_id: correlates this hop with the rest of the message's trace.
            timeout: seconds to wait for *more bytes*. Already-buffered frames
                are returned without consulting it.

        Returns:
            The payload, with the length prefix stripped.

        Raises:
            ConnectionClosedError: on EOF, including EOF mid-frame.
            FrameTooLargeError: if the announced size exceeds the maximum.
            TransportTimeout: if ``timeout`` elapses with no bytes arriving.
        """
        while True:
            frame = self._decoder.pop_frame()
            if frame is not None:
                self._emit(Direction.INBOUND, _framed(frame), frame, trace_id)
                return frame

            if self._closed:
                raise ConnectionClosedError("channel was closed while waiting for a frame")

            if timeout is not None:
                self._sock.settimeout(timeout)

            try:
                chunk = self._sock.recv(READ_CHUNK_SIZE)
            except socket.timeout as exc:
                raise TransportTimeout(timeout if timeout is not None else 0.0) from exc
            except OSError as exc:
                if self._closed:
                    raise ConnectionClosedError("channel was closed while waiting for a frame") from exc
                raise ConnectionClosedError(f"receive failed: {exc}") from exc
            finally:
                if timeout is not None and not self._closed:
                    self._sock.settimeout(None)

            if not chunk:
                raise ConnectionClosedError(_eof_message(self._decoder))
            self._decoder.feed(chunk)

    def close(self) -> None:
        """Close the connection. Idempotent, and safe to call from any thread.

        ``shutdown`` comes first, and it is not optional: it is what makes a
        ``recv`` blocked in another thread return immediately. ``close`` alone
        leaves that reader parked on a dead descriptor until the peer's TCP
        stack eventually resets it.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    def _emit(
        self,
        direction: Direction,
        frame: bytes,
        payload: bytes,
        trace_id: str | None,
    ) -> None:
        if self._emitter is None:
            return
        self._emitter.emit(
            layer=Layer.TRANSPORT,
            direction=direction,
            summary=_summary(direction, payload, self.peer_name),
            trace_id=trace_id,
            payload=frame,
        )


def _framed(payload: bytes) -> bytes:
    """Re-render a received payload with its length prefix.

    :meth:`FrameDecoder.pop_frame` strips the prefix, but the layer's own trace
    event has to show the bytes this layer handled -- otherwise the visualizer
    cannot show L4 contributing its four bytes.
    """
    return len(payload).to_bytes(LENGTH_PREFIX_SIZE, "big") + payload


def _eof_message(decoder: FrameDecoder) -> str:
    """Describe an EOF, naming a truncated frame when there was one."""
    pending = decoder.buffered_bytes
    if pending:
        return f"connection closed with {pending} bytes of an incomplete frame buffered"
    return "connection closed by peer"


def _summary(direction: Direction, payload: bytes, peer: str) -> str:
    """One-line transport summary for the trace UI."""
    verb = "->" if direction is Direction.OUTBOUND else "<-"
    return f"{len(payload)} B payload {verb} {peer}"


def _safe_extra(sock: socket.socket, name: str) -> Any:
    """Read a socket option that raises once the socket is closed."""
    try:
        return sock.getsockname() if name == "sockname" else sock.getpeername()
    except OSError:
        return None


def _format_address(value: Any) -> str:
    """Render a socket address tuple as ``host:port``."""
    if isinstance(value, tuple) and len(value) >= 2:
        return f"{value[0]}:{value[1]}"
    return "unknown"
