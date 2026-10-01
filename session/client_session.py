"""The CLI client's session driver: handshake, send, receive, close.

Owns a :class:`~transport.channel.BlockingTcpChannel` and drives
:class:`~session.session.SessionCore` over it. The bridge service uses this same
class -- from the chat server's point of view the bridge *is* a client, and
reusing this driver is what guarantees the bridge's framing and handshake cannot
drift from the CLI's.

The class is blocking on purpose. It is used from a process whose reader runs in
its own thread while ``input()`` blocks the main one, so there is nothing to be
gained from an event loop and a good deal of clarity to be lost.
"""

from __future__ import annotations

from typing import Any

from presentation import MessageType, make_message
from session.errors import HandshakeError
from session.handshake import build_connect, parse_connect_err, parse_connect_ok
from session.heartbeat import HeartbeatPolicy
from session.session import SessionCore
from trace import TraceEmitter
from transport.channel import BlockingTcpChannel

__all__ = ["ClientSession"]


class ClientSession:
    """A client-side chat session over a blocking TCP channel."""

    __slots__ = ("_channel", "_core", "_emitter", "_nickname")

    def __init__(
        self,
        channel: BlockingTcpChannel,
        *,
        nickname: str,
        emitter: TraceEmitter | None = None,
        heartbeat: HeartbeatPolicy | None = None,
    ) -> None:
        self._channel = channel
        self._nickname = nickname
        # Inherit the channel's emitter when the caller does not supply one.
        # Otherwise a caller that configured tracing on the channel gets L4
        # events and no L5-L7 events, and a half-populated trace is worse than
        # no trace: it looks complete while hiding every layer above transport.
        self._emitter = emitter if emitter is not None else channel.emitter
        self._core = SessionCore(emitter=self._emitter, heartbeat=heartbeat)

    @property
    def core(self) -> SessionCore:
        """The underlying session state machine."""
        return self._core

    @property
    def channel(self) -> BlockingTcpChannel:
        """The underlying transport channel."""
        return self._channel

    @property
    def nickname(self) -> str:
        """The nickname this session connected under."""
        return self._nickname

    @property
    def session_id(self) -> str:
        """The server-assigned session id."""
        return self._core.session_id

    def rename(self, nickname: str) -> None:
        """Adopt a new nickname after the server has confirmed a rename.

        The session owns the display name because it stamps it onto every
        outbound frame and needs it to tell an outgoing private message from an
        incoming one. A caller that kept its own copy would have two names that
        can disagree, and the server -- which overwrites ``sender`` from the
        handshake -- would silently paper over the difference.
        """
        self._nickname = nickname

    def connect(self) -> dict[str, Any]:
        """Run the CONNECT/CONNECT_OK handshake.

        Returns:
            The CONNECT_OK message, so the caller can read the assigned nickname
            (which may differ from the requested one if the server normalised it).

        Raises:
            HandshakeError: if the server rejects the handshake, or answers with
                something other than CONNECT_OK or CONNECT_ERR.
        """
        connect = build_connect(self._nickname)
        self._send(connect)

        while True:
            _, message = self.receive()
            message_type = message["type"]

            if message_type is MessageType.CONNECT_OK:
                session_id, nickname = parse_connect_ok(message)
                self._core.open(session_id)
                self._nickname = nickname
                return message

            if message_type is MessageType.CONNECT_ERR:
                code, detail = parse_connect_err(message)
                self._core.close()
                raise HandshakeError(f"server rejected connection: {code} ({detail})")

            # USER_LIST and other informational frames may legitimately arrive
            # between CONNECT and CONNECT_OK; they are handled by the caller
            # once the handshake returns, so buffer nothing here and keep
            # reading for the frame that actually decides the outcome.
            if message_type is not MessageType.USER_LIST:
                self._core.close()
                raise HandshakeError(
                    f"expected CONNECT_OK or CONNECT_ERR, got {message_type.value}"
                )

    def send(
        self,
        message: dict[str, Any],
        *,
        summary: str | None = None,
        require_active: bool = True,
    ) -> str | None:
        """Encapsulate and transmit one message.

        Args:
            message: the envelope to send.
            summary: L7 trace summary override.
            require_active: enforce the session state machine. Only the
                handshake itself passes ``False``.

        Returns:
            The trace id correlating this message's events.

        Raises:
            SessionStateError: if ``require_active`` and the session is not active.
        """
        if require_active:
            self._core.require_active("send chat traffic")

        trace_id, payload = self._core.build_outbound(message, summary=summary)
        self._channel.send_frame(payload, trace_id=trace_id)
        return trace_id

    def receive(self, *, timeout: float | None = None) -> tuple[Any, dict[str, Any]]:
        """Receive one application-level message.

        PING is answered with PONG here and never surfaces to the caller: the
        heartbeat is a session-layer concern, and an application layer that had
        to know about it would be a leaky abstraction.

        Args:
            timeout: seconds to wait for a frame. ``None`` blocks indefinitely.

        Returns:
            ``(session_header, message)``.

        Raises:
            TransportTimeout: if ``timeout`` elapses with nothing received.
            ConnectionClosedError: if the peer closes the connection.
        """
        while True:
            frame = self._channel.receive_frame(timeout=timeout)
            header, message = self._core.parse_inbound(frame)

            if message["type"] is MessageType.PING:
                self.send(
                    make_message(MessageType.PONG, {}, sender=self._nickname),
                    summary="PONG (heartbeat reply)",
                )
                continue

            return header, message

    def close(self) -> None:
        """Send a best-effort DISCONNECT and close the channel. Idempotent.

        The DISCONNECT is attempted only on an active session, and a failure to
        deliver it is swallowed: the caller has already decided to leave, and a
        socket error while saying goodbye must not mask that decision.
        """
        if self._core.is_active:
            try:
                self._core.begin_closing()
                self.send(
                    make_message(MessageType.DISCONNECT, {}, sender=self._nickname),
                    summary="DISCONNECT",
                    require_active=False,
                )
            except Exception:
                pass

        self._core.close()
        self._channel.close()

    def _send(self, message: dict[str, Any]) -> None:
        """Transmit a message without the state-machine check (handshake path)."""
        self.send(message, require_active=False)
