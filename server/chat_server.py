"""The server: one asyncio event loop serving every connection concurrently.

Why one loop and not a thread per client. A chat server's shared state is the
roster, and every operation on it is a read or a write of a dictionary. On a
single event loop those operations are atomic by construction -- none of them
awaits -- so the roster needs no lock and cannot be observed half-updated. The
usual objection to this design is that one slow client blocks everybody; that is
answered below by the outbound queue rather than by threads.

Each connection is two tasks. The reader decapsulates and dispatches; the writer
drains that connection's own queue onto the socket. The queue is the crux: a
client that stops reading fills its own queue and is dropped, so it can never
stall the broadcast that is trying to reach everyone else.

The encapsulation order is visible in the two directions. Inbound, a frame
becomes ``bytes -> SessionCore.parse_inbound -> message dict``. Outbound, a
message dict becomes ``SessionCore.build_outbound -> bytes -> socket``. No code
here touches the socket directly, and no code above it knows about framing.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Mapping

from presentation import (
    CLIENT_TO_SERVER,
    CodecError,
    ErrorCode,
    MessageType,
    SchemaViolation,
    extract_text,
    is_valid_nickname,
    make_message,
)
from server.logger import get_logger
from server.registry import User, UserRegistry
from session import (
    PROTOCOL_VERSION,
    HandshakeError,
    SessionCore,
    SessionEnvelopeError,
    build_connect_err,
    build_connect_ok,
    parse_connect,
)
from session.heartbeat import HeartbeatPolicy
from trace import TraceEmitter
from transport import AsyncTcpChannel, ConnectionClosedError, FrameTooLargeError

__all__ = ["ChatServer", "Connection", "ServerConfig"]

_log = get_logger("server")

#: Maximum queued outbound messages per connection. When a client stops reading
#: and this fills, the connection is dropped: a peer that cannot keep up with
#: broadcast traffic must not be allowed to consume the server's memory, and it
#: must never be allowed to block the senders.
DEFAULT_SEND_QUEUE_SIZE = 256

#: Malformed frames tolerated before the connection is closed. One bad frame is
#: recoverable; a stream of them means the peer is not speaking this protocol.
DEFAULT_MALFORMED_BUDGET = 3

#: Seconds to let the writer drain what is already queued before the socket is
#: dropped. Bounded because a peer that has stopped reading is exactly the case
#: this is guarding against, and teardown must not wait on it.
DEFAULT_FLUSH_TIMEOUT = 1.0

#: How long one connection may take to finish flushing during shutdown. Set
#: above the per-connection flush timeout so that in the normal case every
#: writer finishes on its own; only a connection that fails to drain at all
#: reaches this ceiling and gets cancelled.
DEFAULT_SHUTDOWN_TIMEOUT = 2.0


class ServerConfig:
    """Everything the server needs to run, with no hardcoded address."""

    __slots__ = (
        "flush_timeout",
        "handshake_timeout",
        "heartbeat",
        "host",
        "malformed_budget",
        "max_clients",
        "port",
        "send_queue_size",
        "shutdown_timeout",
        "trace",
    )

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9009,
        *,
        max_clients: int = 64,
        send_queue_size: int = DEFAULT_SEND_QUEUE_SIZE,
        malformed_budget: int = DEFAULT_MALFORMED_BUDGET,
        handshake_timeout: float = 10.0,
        flush_timeout: float = DEFAULT_FLUSH_TIMEOUT,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
        heartbeat: HeartbeatPolicy | None = None,
        trace: TraceEmitter | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.max_clients = max_clients
        self.send_queue_size = send_queue_size
        self.malformed_budget = malformed_budget
        self.handshake_timeout = handshake_timeout
        self.flush_timeout = flush_timeout
        self.shutdown_timeout = shutdown_timeout
        self.heartbeat = heartbeat
        self.trace = trace


class Connection:
    """One client: its channel, its session state, and its outbound queue."""

    __slots__ = (
        "_writer",
        "channel",
        "core",
        "malformed",
        "peer",
        "queue",
        "registry",
        "server",
        "user",
    )

    def __init__(
        self,
        server: "ChatServer",
        channel: AsyncTcpChannel,
        *,
        registry: UserRegistry,
    ) -> None:
        self.server = server
        self.channel = channel
        self.registry = registry
        self.peer = channel.peer_name
        self.core = SessionCore(emitter=channel.emitter, heartbeat=server.config.heartbeat)
        self.user: User | None = None
        self.malformed = 0
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=server.config.send_queue_size
        )
        # Held so shutdown can wait for the flush to finish instead of guessing
        # at a sleep long enough for it. ``None`` before ``run`` starts.
        self._writer: asyncio.Task | None = None

    # -- outbound ---------------------------------------------------------

    def enqueue(self, message: Mapping[str, Any]) -> None:
        """Queue a message for delivery, dropping the client if it falls behind.

        Non-blocking by design: this runs on the broadcast path, where awaiting
        would let one slow client stall delivery to every other one.
        """
        try:
            self.queue.put_nowait(dict(message))
        except asyncio.QueueFull:
            _log.warning(
                "send queue full for %s (%s); dropping slow consumer",
                self.nickname,
                self.peer,
            )
            self.abort()

    def abort(self) -> None:
        """Drop everything queued and close the socket right now.

        Reserved for the case where nothing queued is worth delivering: a slow
        consumer whose queue overflowed. Closing the socket is also what wakes a
        reader parked in ``receive_frame`` -- the decision came from this side,
        and the reader has no other way to learn about it.

        The ordinary end of a connection does *not* go through here: it goes
        through :meth:`_teardown`, which flushes first so the peer is told why.
        """
        self.channel.abort()
        self.stop_writer()

    def stop_writer(self) -> None:
        """Queue the writer's sentinel so it exits once the queue drains.

        The sentinel always goes in, even when that means evicting a message.
        Skipping it would leave the writer parked forever in ``queue.get()``,
        and that writer task is exactly what teardown awaits. The evicted
        message is undeliverable anyway -- this only runs as a connection ends.
        """
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            self.queue.get_nowait()
            self.queue.put_nowait(None)

    @property
    def nickname(self) -> str:
        """The user's nickname, or a placeholder before the handshake."""
        return self.user.nickname if self.user is not None else "?"

    @property
    def is_authenticated(self) -> bool:
        """Whether the handshake has completed for this connection."""
        return self.user is not None and self.core.is_active

    # -- tasks ------------------------------------------------------------

    async def run(self) -> None:
        """Drive one connection from handshake to disconnect.

        Every failure mode is contained here. A connection that raises must not
        take down the accept loop or any other client, so the broad handler at
        the bottom is deliberate rather than lazy -- and it is why the cleanup
        lives in the ``finally`` block rather than on the exception path.
        """
        writer = asyncio.create_task(self._writer_loop())
        self._writer = writer
        try:
            await self._handshake()
            await self._reader_loop()
        except (ConnectionClosedError, asyncio.IncompleteReadError):
            _log.info("client %s disconnected abruptly", self.nickname)
        except ConnectionError as exc:
            # A peer that vanishes without closing sends RST, which surfaces as
            # ConnectionResetError rather than EOF. It is the same event -- the
            # client is gone -- and it should not be reported as a server fault.
            _log.info("client %s connection lost: %s", self.nickname, exc)
        except HandshakeError as exc:
            _log.info("handshake failed for %s: %s", self.peer, exc)
        except asyncio.TimeoutError:
            _log.info("handshake from %s timed out", self.peer)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one connection must not kill the server
            _log.exception("unhandled error serving %s", self.nickname)
        finally:
            await self._teardown(writer)

    async def _teardown(self, writer: asyncio.Task) -> None:
        """Flush what is already queued, then close.

        This is the reason a rejected handshake or an oversized frame reaches
        the peer at all: those messages are written into the socket *before* it
        closes. Closing first would leave the peer with a bare disconnection and
        no explanation of what it did wrong.

        The wait is bounded because the peer on the other end may be exactly the
        problem -- a client that stopped reading will park the writer inside
        ``drain()`` -- and a graceful shutdown that can hang is not graceful.
        """
        self.stop_writer()
        try:
            _done, pending = await asyncio.wait(
                {writer}, timeout=self.server.config.flush_timeout
            )
            if pending:
                _log.warning("flush timed out for %s; closing anyway", self.nickname)
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
        finally:
            await self.channel.close()
            self.server._forget(self)

    # -- handshake --------------------------------------------------------

    async def _handshake(self) -> None:
        """Read CONNECT, validate it, and answer with CONNECT_OK or CONNECT_ERR."""
        frame = await asyncio.wait_for(
            self.channel.receive_frame(), timeout=self.server.config.handshake_timeout
        )
        try:
            _, message = self.core.parse_inbound(frame)
        except (SessionEnvelopeError, CodecError, SchemaViolation) as exc:
            # A peer that cannot even produce a readable CONNECT is not going to
            # recover by being told which byte was wrong, so it is answered and
            # dropped rather than given the malformed-frame budget.
            self._reject(ErrorCode.MALFORMED, "CONNECT could not be decoded")
            raise HandshakeError(f"CONNECT could not be decoded: {exc}") from exc

        if message["type"] is not MessageType.CONNECT:
            self._reject(
                ErrorCode.UNEXPECTED_TYPE,
                f"expected CONNECT, got {message['type'].value}",
            )
            raise HandshakeError(f"expected CONNECT, got {message['type'].value}")

        try:
            nickname, version = parse_connect(message)
        except SchemaViolation as exc:
            # The payload got through the codec but does not describe a usable
            # session -- an illegal nickname, a non-integer version. The peer is
            # told which, because unlike a decode failure this is a mistake the
            # user can fix and retry.
            self._reject(ErrorCode.NICK_INVALID, "nickname is not acceptable")
            raise HandshakeError(f"CONNECT rejected: {exc}") from exc

        if version != PROTOCOL_VERSION:
            self._reject(
                ErrorCode.PROTOCOL_MISMATCH,
                f"server speaks protocol version {PROTOCOL_VERSION}",
            )
            raise HandshakeError(f"protocol mismatch: client sent version {version}")

        user = self.registry.reserve(nickname, self, peer=self.peer)
        if user is None:
            self._reject(ErrorCode.NICK_TAKEN, f"{nickname!r} is already in use")
            raise HandshakeError(f"nickname taken: {nickname!r}")

        session_id = str(uuid.uuid4())
        self.user = user
        self.registry.attach_session(user, session_id)
        self.core.open(session_id)

        _log.info("client %s connected from %s", nickname, self.peer)
        self.enqueue(build_connect_ok(session_id, nickname))
        self.enqueue(self._user_list())
        self.server._broadcast_presence(self, MessageType.USER_JOIN, nickname)

    def _reject(self, code: ErrorCode, detail: str) -> None:
        """Answer a failed handshake. Best-effort: the peer may already be gone."""
        self.enqueue(build_connect_err(code, detail))

    # -- reader -----------------------------------------------------------

    async def _reader_loop(self) -> None:
        """Decapsulate inbound PDUs and dispatch them until the peer leaves."""
        while not self.channel.is_closed:
            try:
                frame = await self.channel.receive_frame()
            except FrameTooLargeError:
                # The frame boundary is no longer trustworthy: the announced
                # size is still sitting in the stream and the next read would
                # mistake it for a length prefix. Report and stop.
                self.enqueue(
                    self._error(ErrorCode.FRAME_TOO_LARGE, "frame exceeds the maximum size")
                )
                return

            try:
                header, message = self.core.parse_inbound(frame)
            except (SessionEnvelopeError, CodecError, SchemaViolation) as exc:
                if not self._note_malformed(exc):
                    return
                continue

            if header.session_id != self.core.session_id:
                # The session header is what ties a PDU to the session that was
                # negotiated at handshake time. Accepting a frame that carries
                # somebody else's id -- or the pre-handshake placeholder --
                # would make the header decorative.
                if not self._note_malformed(
                    SessionEnvelopeError(
                        f"frame carries session {header.session_id}, "
                        f"expected {self.core.session_id}"
                    )
                ):
                    return
                continue

            if message["type"] is MessageType.DISCONNECT:
                _log.info("client %s disconnected", self.nickname)
                return

            self._dispatch(message)

    def _note_malformed(self, exc: Exception) -> bool:
        """Count a malformed frame. Returns ``False`` when the budget is spent.

        A single bad frame is recoverable -- the length prefix still delimited
        it, so the stream is still in sync. A run of them means the peer is not
        speaking this protocol, and continuing to guess at frame boundaries is
        how a parser gets desynchronised into reading garbage.
        """
        self.malformed += 1
        _log.warning(
            "malformed frame from %s (%d/%d): %s",
            self.nickname,
            self.malformed,
            self.server.config.malformed_budget,
            exc,
        )

        if self.malformed >= self.server.config.malformed_budget:
            self.enqueue(self._error(ErrorCode.MALFORMED, "too many malformed frames"))
            return False

        self.enqueue(self._error(ErrorCode.MALFORMED, "could not decode the previous frame"))
        return True

    # -- dispatch ---------------------------------------------------------

    def _dispatch(self, message: Mapping[str, Any]) -> None:
        """Route one validated message to its handler.

        Direction and authentication are filtered before the handlers: a peer
        that skipped the handshake must not be able to broadcast, and it must
        not be able to read the roster either.
        """
        message_type = message["type"]

        if message_type is MessageType.PING:
            self.enqueue(make_message(MessageType.PONG, {}, sender="server"))
            return

        if message_type not in CLIENT_TO_SERVER:
            self.enqueue(
                self._error(
                    ErrorCode.UNEXPECTED_TYPE,
                    f"clients may not send {message_type.value}",
                    field="type",
                )
            )
            return

        if not self.is_authenticated:
            self.enqueue(
                self._error(ErrorCode.NOT_AUTHENTICATED, "send CONNECT first", field="type")
            )
            return

        handler = {
            MessageType.BROADCAST: self._on_broadcast,
            MessageType.PRIVATE: self._on_private,
            MessageType.USER_LIST: self._on_user_list,
            MessageType.NICK: self._on_nick,
        }.get(message_type)

        if handler is None:
            self.enqueue(
                self._error(
                    ErrorCode.UNKNOWN_TYPE,
                    f"unsupported message type {message_type.value}",
                    field="type",
                )
            )
            return

        try:
            handler(message)
        except SchemaViolation as exc:
            # The violation names the rule it broke; only fall back to MALFORMED
            # when it does not, so a body that was merely too long is reported
            # as such rather than as unreadable bytes.
            self.enqueue(self._error(exc.code or ErrorCode.MALFORMED, str(exc)))

    def _on_broadcast(self, message: Mapping[str, Any]) -> None:
        """Relay a chat message to every connected user, including the sender.

        The sender's copy is what makes their own transcript complete; a client
        that had to render its input locally would show the message before the
        server had accepted it.
        """
        text = extract_text(message["payload"])
        self.server._broadcast(
            make_message(MessageType.BROADCAST, {"text": text}, sender=self.nickname)
        )

    def _on_private(self, message: Mapping[str, Any]) -> None:
        """Relay a chat message to exactly one named user."""
        target = message["payload"].get("to")
        if not isinstance(target, str) or not target:
            raise SchemaViolation("payload.to must be a non-empty string", field="to")

        text = extract_text(message["payload"])
        recipient = self.registry.get(target)
        if recipient is None:
            self.enqueue(
                self._error(ErrorCode.NO_SUCH_USER, f"no such user: {target!r}", field="to")
            )
            return

        if recipient is not self.user:
            recipient.session.enqueue(
                make_message(MessageType.PRIVATE, {"text": text}, sender=self.nickname)
            )

        # The sender's own copy carries ``to``, which is what lets a client
        # render it as an outgoing private message rather than an inbound one.
        # The recipient's copy deliberately omits it.
        self.enqueue(
            make_message(
                MessageType.PRIVATE, {"text": text, "to": target}, sender=self.nickname
            )
        )

    def _on_user_list(self, message: Mapping[str, Any]) -> None:
        """Answer a roster request."""
        self.enqueue(self._user_list())

    def _on_nick(self, message: Mapping[str, Any]) -> None:
        """Handle a rename request.

        The session id and sequence number are untouched: this changes the
        display name, not the session. Other users are told through a
        USER_LEAVE/USER_JOIN pair rather than a dedicated type, so a client that
        only knows the original ten message types still renders the rename
        correctly -- one user left, another arrived.
        """
        assert self.user is not None  # guaranteed by the authentication filter
        requested = message["payload"].get("nick")
        if not is_valid_nickname(requested):
            self.enqueue(
                self._error(ErrorCode.NICK_INVALID, "nickname is not acceptable", field="nick")
            )
            return

        previous = self.user.nickname
        if requested == previous:
            self.enqueue(make_message(MessageType.NICK_OK, {"nick": requested}, sender="server"))
            return

        if not self.registry.rename(self.user, requested):
            self.enqueue(
                self._error(ErrorCode.NICK_TAKEN, f"{requested!r} is already in use", field="nick")
            )
            return

        _log.info("client %s renamed to %s", previous, requested)
        self.server._broadcast_presence(self, MessageType.USER_LEAVE, previous)
        self.enqueue(make_message(MessageType.NICK_OK, {"nick": requested}, sender="server"))
        self.server._broadcast_presence(self, MessageType.USER_JOIN, requested)

    # -- helpers ----------------------------------------------------------

    def _user_list(self) -> dict[str, Any]:
        """Build a USER_LIST for this connection."""
        return make_message(
            MessageType.USER_LIST, {"users": self.registry.snapshot()}, sender="server"
        )

    @staticmethod
    def _error(code: ErrorCode, detail: str, *, field: str | None = None) -> dict[str, Any]:
        """Build an ERROR carrying a machine-readable code."""
        payload: dict[str, Any] = {"code": code, "message": detail}
        if field is not None:
            payload["field"] = field
        return make_message(MessageType.ERROR, payload, sender="server")

    async def _writer_loop(self) -> None:
        """Drain the outbound queue onto the socket, in order, until closed."""
        while True:
            message = await self.queue.get()
            if message is None:
                return

            try:
                _, payload = self.core.build_outbound(message)
                await self.channel.send_frame(payload)
            except ConnectionClosedError:
                return
            except FrameTooLargeError:
                # A server-generated message that does not fit is a bug in this
                # build, not something the peer can fix. Log it and keep the
                # connection: dropping the user would hide the real fault.
                _log.error("outbound frame too large for %s; message dropped", self.nickname)
            except Exception:  # noqa: BLE001
                _log.exception("failed to send to %s", self.nickname)
                return


class ChatServer:
    """Accepts connections and owns the roster they share."""

    def __init__(
        self,
        config: ServerConfig | None = None,
        *,
        registry: UserRegistry | None = None,
    ) -> None:
        self.config = config or ServerConfig()
        self.registry = registry or UserRegistry()
        self.connections: set[Connection] = set()
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    @property
    def port(self) -> int:
        """The port actually bound, which matters when ``port=0`` was requested."""
        if self._server is None or not self._server.sockets:
            return self.config.port
        return self._server.sockets[0].getsockname()[1]

    @property
    def client_count(self) -> int:
        """Number of users currently in the roster."""
        return len(self.registry)

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Bind and begin accepting."""
        self._closing = False
        self._server = await asyncio.start_server(
            self._on_client, self.config.host, self.config.port
        )
        _log.info("listening on %s:%d", self.config.host, self.port)

    async def serve_forever(self) -> None:
        """Accept connections until the task is cancelled.

        ``asyncio.Server.serve_forever`` is deliberately *not* used here. From
        Python 3.14, cancelling it runs ``close()``, ``close_clients()`` and
        ``await wait_closed()`` before the cancellation propagates. The middle
        one closes every accepted transport, and this task is cancelled
        *before* ``shutdown()`` runs -- so the client sockets would already be
        gone by the time the farewell is queued, the writer would fail with
        "cannot write to closing transport", and every client would see a bare
        connection reset instead of a DISCONNECT.

        Accepting is therefore started directly and the task parks until it is
        cancelled. Nothing else in this class closes a client socket; that is
        ``shutdown()``'s job, and it does it in the one order that lets the
        farewell reach the wire first.
        """
        if self._server is None:
            await self.start()
        assert self._server is not None
        await self._server.start_serving()
        # Parked until cancelled. Nothing ever sets this event: the server is
        # stopped by cancelling this task, not by signalling it.
        await asyncio.Event().wait()

    async def shutdown(self) -> None:
        """Stop accepting, tell every client, and close everything.

        The order is the whole content of this method. Accepting stops first, so
        no connection can arrive mid-teardown. Each client is then queued a
        DISCONNECT and its writer is told to finish, which lets that farewell
        reach the wire *before* the socket closes -- closing first would make it
        unwritable and turn an orderly shutdown into a wave of client-side
        "connection reset" errors.

        The wait is on the writer tasks themselves rather than on a sleep. A
        fixed delay long enough for the happy path is either too short, and
        drops the farewell, or arbitrarily long; waiting on the actual work is
        exact and still bounded, because a peer that has stopped reading is
        precisely the case this must not hang on.

        ``Server.wait_closed()`` is deliberately *not* awaited here. From Python
        3.13 it waits for every live connection handler to finish as well, and
        the handlers are parked in ``receive_frame`` waiting for a peer that has
        not gone away yet -- it would deadlock against the very connections this
        method is about to close.

        The leading yield is not decoration. ``asyncio.Server`` accepts in two
        steps: the selector reads a connection and starts ``_accept_connection2``
        as a task, and only that task builds the transport and calls the
        protocol callback. Closing the listener in the same tick therefore lands
        between the two: ``Server._attach`` runs its ``assert self._sockets is
        not None`` against a server that has just set that field to ``None``,
        the task raises, and the already-accepted socket is dropped with nobody
        holding a reference to close it. Letting one turn of the loop pass first
        lets any in-flight accept finish registering, so the connection reaches
        ``_on_client`` and is refused through the normal ``_closing`` path.
        """
        await asyncio.sleep(0)

        if self._server is not None:
            self._server.close()
            self._server = None

        # Set before the handlers are touched, so that a connection whose
        # teardown runs late does not announce a departure to a roster that is
        # already being torn down.
        self._closing = True

        connections = list(self.connections)
        _log.info("shutting down: %d client(s) connected", len(connections))

        farewell = make_message(MessageType.DISCONNECT, {}, sender="server")
        for connection in connections:
            connection.enqueue(farewell)
            connection.stop_writer()

        writers = {c._writer for c in connections if c._writer is not None}
        if writers:
            _done, pending = await asyncio.wait(writers, timeout=self.config.flush_timeout)
            if pending:
                _log.warning("%d client(s) did not finish flushing", len(pending))

        # Closing the sockets is what wakes the readers parked in
        # ``receive_frame``; they have no other way to learn the server is going.
        for connection in connections:
            connection.channel.abort()

        if self._tasks:
            _done, pending = await asyncio.wait(
                self._tasks, timeout=self.config.shutdown_timeout
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        # A connection whose handler was cancelled mid-teardown never reached
        # its own ``channel.close()``, so the transport would be reclaimed by
        # the garbage collector rather than closed. Re-closing is idempotent and
        # costs nothing on the paths that already finished.
        for connection in connections:
            await connection.channel.close()

        self._tasks.clear()
        self.connections.clear()
        self.registry = UserRegistry()
        _log.info("shutdown complete")

    # -- internals --------------------------------------------------------

    def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Accept one connection into the task set.

        A synchronous callback, so ``start_server`` cannot be stalled by a slow
        handshake -- that runs inside the task. Over the limit, the socket is
        closed immediately rather than accepting and then rejecting: there is no
        point negotiating with a peer the server cannot serve.
        """
        if self._closing:
            # ``close()`` stops the listener, but a connection already sitting
            # in the kernel's backlog can still be handed to this callback
            # afterwards. Registering it here would add a connection to a server
            # that is past the point of ever closing it, so the socket is
            # dropped instead -- and dropped outright, because a graceful close
            # is scheduled on the loop and this server may not run another
            # iteration.
            _log.info("connection refused: server is shutting down")
            writer.transport.abort()
            return

        if len(self.connections) >= self.config.max_clients:
            _log.warning("connection refused: server full (%d)", len(self.connections))
            writer.transport.abort()
            return

        channel = AsyncTcpChannel(reader, writer, emitter=self.config.trace)
        connection = Connection(self, channel, registry=self.registry)
        self.connections.add(connection)

        task = asyncio.create_task(connection.run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _forget(self, connection: Connection) -> None:
        """Remove a finished connection and announce the departure."""
        self.connections.discard(connection)

        user = connection.user
        if user is None:
            return

        # Only announce if this session still holds the nickname. A stale
        # cleanup running after the user reconnected must not evict -- or
        # announce the departure of -- the new session's occupant.
        if not self.registry.release(user):
            return

        if self._closing:
            # Everyone is leaving at once and has already been told; a USER_LEAVE
            # per connection would be a burst of noise about a roster that no
            # longer exists.
            return

        _log.info("client %s left", user.nickname)
        self._broadcast_presence(connection, MessageType.USER_LEAVE, user.nickname)

    def _broadcast(self, message: Mapping[str, Any]) -> None:
        """Queue a message for every connected user."""
        for connection in self.connections:
            if connection.is_authenticated:
                connection.enqueue(message)

    def _broadcast_presence(
        self, origin: Connection, message_type: MessageType, nickname: str
    ) -> None:
        """Announce a join or leave to everyone except the user it concerns."""
        message = make_message(message_type, {"nick": nickname}, sender="server")
        for connection in self.connections:
            if connection is not origin and connection.is_authenticated:
                connection.enqueue(message)
