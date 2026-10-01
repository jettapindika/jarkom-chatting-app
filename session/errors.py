"""Session-layer (OSI L5) exceptions."""

from __future__ import annotations

__all__ = [
    "HandshakeError",
    "HeartbeatTimeout",
    "SessionEnvelopeError",
    "SessionError",
    "SessionStateError",
]


class SessionError(Exception):
    """Base class for every failure raised by the L5 session layer."""


class SessionEnvelopeError(SessionError):
    """The session header could not be built or parsed."""


class SessionStateError(SessionError):
    """An operation was attempted in a state that does not permit it.

    Raised when, for example, chat traffic is sent before the handshake
    completes. The state machine is enforced rather than documented, so a
    mis-ordered call fails at the caller instead of producing a frame the peer
    will reject.
    """


class HandshakeError(SessionError):
    """The CONNECT/CONNECT_OK exchange did not complete."""


class HeartbeatTimeout(SessionError):
    """The peer stopped answering PING within the configured deadline.

    Raised on the side that detects the silence, so the caller can close the
    connection and report a definite cause instead of leaving a half-dead socket
    open until TCP itself notices -- which can take hours.
    """
