"""The L5 session: identity, sequencing, state and liveness.

This is the layer that makes a connection into a *session*. It owns four things
and nothing else:

* **Identity** -- the session id the server assigns during the handshake, which
  travels in the header of every subsequent PDU.
* **Sequencing** -- a monotonic per-session counter, so a receiver can tell a
  reordered or duplicated PDU from a fresh one even though TCP already
  guarantees order. It costs 8 bytes and turns "TCP should have handled that"
  into an assertion the code can actually check.
* **State** -- the :class:`SessionState` machine that decides what may be sent
  when.
* **Liveness** -- the :class:`HeartbeatMonitor` that decides when a silent peer
  should be considered gone.

It deliberately does **not** own a socket. The transforms below turn a message
into transport payload and back; performing the actual I/O is the driver's job
(``ClientSession`` for the CLI, an async driver for the server), because that is
the only part where the two differ. Keeping the socket out means the sequencing,
state and envelope rules have exactly one implementation and are tested without
a network.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from presentation import MessageType, decode, encode
from session.envelope import UNASSIGNED_SESSION_ID, SessionHeader, decode_envelope, encode_envelope
from session.errors import SessionStateError
from session.heartbeat import HeartbeatMonitor, HeartbeatPolicy
from session.session_state import SessionState
from trace import Direction, Layer, TraceEmitter

__all__ = ["SessionCore", "summarize"]


class SessionCore:
    """Session state, sequencing and the L5/L6/L7 transforms for one connection."""

    __slots__ = (
        "_emitter",
        "_heartbeat",
        "_inbound_anomalies",
        "_last_inbound_sequence",
        "_next_sequence",
        "_state",
        "_session_id",
    )

    def __init__(
        self,
        *,
        emitter: TraceEmitter | None = None,
        heartbeat: HeartbeatPolicy | None = None,
        session_id: str = UNASSIGNED_SESSION_ID,
    ) -> None:
        self._emitter = emitter
        self._heartbeat = HeartbeatMonitor(heartbeat)
        self._session_id = session_id
        self._state = SessionState.CONNECTING
        #: Outbound sequence numbers start at 1; 0 is reserved for the CONNECT
        #: frame, which is sent before the session exists.
        self._next_sequence = 1
        self._last_inbound_sequence: int | None = None
        self._inbound_anomalies = 0

    # -- state ------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """Current lifecycle state."""
        return self._state

    @property
    def session_id(self) -> str:
        """The session id, or the placeholder before the handshake completes."""
        return self._session_id

    @property
    def is_active(self) -> bool:
        """Whether the session has completed its handshake."""
        return self._state is SessionState.ACTIVE

    @property
    def next_sequence(self) -> int:
        """The sequence number the next outbound PDU will carry."""
        return self._next_sequence

    @property
    def last_inbound_sequence(self) -> int | None:
        """Sequence number of the most recent inbound PDU, or ``None``."""
        return self._last_inbound_sequence

    @property
    def inbound_anomalies(self) -> int:
        """Count of inbound PDUs whose sequence was not the expected successor.

        TCP makes this impossible on a healthy connection, so a non-zero count
        is a genuine bug signal rather than routine network noise. It is counted
        instead of raised so one bad PDU does not destroy an otherwise healthy
        session, and reported in the trace so the anomaly is not silent.
        """
        return self._inbound_anomalies

    @property
    def heartbeat(self) -> HeartbeatMonitor:
        """The liveness monitor for this session."""
        return self._heartbeat

    def open(self, session_id: str) -> None:
        """Move the session to ``ACTIVE`` once the server has assigned an id.

        Raises:
            SessionStateError: if the session already left ``CONNECTING``, which
                would mean two handshakes ran on one connection.
        """
        if self._state is not SessionState.CONNECTING:
            raise SessionStateError(f"cannot open a session in state {self._state.value}")

        self._session_id = session_id
        self._state = SessionState.ACTIVE
        if self._emitter is not None:
            self._emitter.set_session_id(session_id)

    def close(self) -> None:
        """Mark the session closed. Idempotent."""
        self._state = SessionState.CLOSED

    def begin_closing(self) -> None:
        """Mark the session as draining, ahead of the final DISCONNECT."""
        if self._state is SessionState.ACTIVE:
            self._state = SessionState.CLOSING

    def require_active(self, operation: str) -> None:
        """Raise unless chat traffic is permitted right now."""
        if self._state is not SessionState.ACTIVE:
            raise SessionStateError(f"cannot {operation} in state {self._state.value}")

    # -- outbound: L7 -> L6 -> L5 -----------------------------------------

    def build_outbound(
        self,
        message: Mapping[str, Any],
        *,
        summary: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[str | None, bytes]:
        """Encapsulate a message down through L6 and L5.

        Emits the L7, L6 and L5 trace events in that order; the driver emits L4
        when it hands the returned bytes to the transport. That split is what
        makes the trace honest -- the L4 event is filed by the code that
        actually writes to the socket.

        Args:
            message: the envelope built by the application layer.
            summary: L7 summary. Defaults to :func:`summarize`, which names the
                operation and quotes any chat text.
            trace_id: reuse an existing trace id, or let L7 mint one.

        Returns:
            ``(trace_id, payload_for_transport)``.

        Raises:
            SessionStateError: if the session is closed.
            SchemaViolation: if the message does not match the envelope schema.
        """
        if self._state is SessionState.CLOSED:
            raise SessionStateError("cannot send on a closed session")

        resolved_id = self._emit_application(message, summary=summary, trace_id=trace_id)
        json_bytes = encode(message, emitter=self._emitter, trace_id=resolved_id)

        sequence = self._next_sequence
        envelope = encode_envelope(json_bytes, session_id=self._session_id, sequence=sequence)
        self._next_sequence += 1

        if self._emitter is not None:
            self._emitter.emit(
                layer=Layer.SESSION,
                direction=Direction.OUTBOUND,
                summary=(
                    f"session {self._short_id()} seq {sequence} "
                    f"+{len(envelope) - len(json_bytes)} B header"
                ),
                trace_id=resolved_id,
                payload=envelope,
            )

        self._heartbeat.touch_outbound()
        return resolved_id, envelope

    # -- inbound: L5 -> L6 -> L7 ------------------------------------------

    def parse_inbound(
        self,
        frame: bytes,
        *,
        trace_id: str | None = None,
    ) -> tuple[SessionHeader, dict[str, Any]]:
        """Decapsulate a transport payload up through L5 and L6.

        Args:
            frame: one complete payload from the transport layer.
            trace_id: correlates this PDU with an outbound trace, when the
                driver already knows it. Inbound PDUs have no such link -- the
                trace id is not on the wire -- so it is normally omitted and the
                L5 event mints one.

        Returns:
            ``(header, message)``.

        Raises:
            SessionEnvelopeError: if the payload is shorter than the L5 header.
            CodecError: if the payload is not valid UTF-8 JSON.
            SchemaViolation: if the JSON is not a valid envelope.
        """
        header, payload = decode_envelope(frame)

        if self._emitter is not None:
            trace_id = self._emitter.emit(
                layer=Layer.SESSION,
                direction=Direction.INBOUND,
                summary=(
                    f"session {header.session_id[:8]} seq {header.sequence} "
                    f"-{len(frame) - len(payload)} B header"
                ),
                trace_id=trace_id,
                payload=frame,
            )

        self._note_inbound_sequence(header.sequence)
        message = decode(payload, emitter=self._emitter, trace_id=trace_id)

        self._heartbeat.touch_inbound()
        return header, message

    # -- internals --------------------------------------------------------

    def _emit_application(
        self,
        message: Mapping[str, Any],
        *,
        summary: str | None,
        trace_id: str | None,
    ) -> str | None:
        """File the L7 event and return the trace id for the rest of the chain.

        Returns ``None`` when tracing is off, which is not a failure: the id is
        only ever used to correlate events, and with no events there is nothing
        to correlate.
        """
        if self._emitter is None:
            return trace_id

        text = json.dumps(_jsonable(message), ensure_ascii=False, separators=(",", ":"))
        try:
            wire = text.encode("utf-8")
        except UnicodeEncodeError:
            # A lone surrogate is representable in JSON and in a Python str but
            # not in UTF-8. Encoding the trace event must not crash before the
            # codec below reports the same condition as a protocol EncodeError.
            wire = text.encode("utf-8", "backslashreplace")
        return self._emitter.emit(
            layer=Layer.APPLICATION,
            direction=Direction.OUTBOUND,
            summary=summary or summarize(message),
            trace_id=trace_id,
            payload=wire,
            preview=text,
        )

    def _note_inbound_sequence(self, sequence: int) -> None:
        """Track inbound sequence continuity."""
        expected = 1 if self._last_inbound_sequence is None else self._last_inbound_sequence + 1
        if sequence != expected:
            self._inbound_anomalies += 1
            if self._emitter is not None:
                self._emitter.emit(
                    layer=Layer.SESSION,
                    direction=Direction.INBOUND,
                    summary=f"sequence anomaly: expected {expected}, got {sequence}",
                    payload=b"",
                    preview=f"expected {expected}, got {sequence}",
                )
        self._last_inbound_sequence = sequence

    def _short_id(self) -> str:
        """First 8 characters of the session id, for compact summaries."""
        return self._session_id[:8]


def summarize(message: Mapping[str, Any]) -> str:
    """One-line human description of a message, used as the L7 trace summary."""
    message_type = message.get("type")
    name = message_type.value if isinstance(message_type, MessageType) else str(message_type)
    sender = message.get("sender") or "?"
    payload = message.get("payload") or {}

    if name == MessageType.BROADCAST.value:
        text = str(payload.get("text", ""))
        return f'BROADCAST "{_clip(text)}" from {sender}'
    if name == MessageType.PRIVATE.value:
        text = str(payload.get("text", ""))
        return f'PRIVATE -> {payload.get("to", "?")}: "{_clip(text)}" from {sender}'
    if name == MessageType.CONNECT.value:
        return f"CONNECT as {payload.get('nick', sender)}"

    return f"{name} from {sender}"


def _clip(text: str, limit: int = 60) -> str:
    """Shorten ``text`` for a one-line summary."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _jsonable(message: Mapping[str, Any]) -> dict[str, Any]:
    """Copy ``message`` with any enum members replaced by their wire values."""
    result: dict[str, Any] = {}
    for key, value in message.items():
        result[key] = value.value if isinstance(value, MessageType) else value
    return result
