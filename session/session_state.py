"""Session lifecycle states.

The state is explicit rather than implied by a handful of booleans, because the
transitions are what the protocol guarantees: nothing above L5 may send chat
traffic before ``ACTIVE``, and nothing may send at all after ``CLOSED``.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["TERMINAL_STATES", "SessionState"]


class SessionState(str, Enum):
    """Where a session is in its lifecycle."""

    #: TCP is up, handshake not yet completed. Only CONNECT may be sent.
    CONNECTING = "connecting"

    #: Handshake completed; chat traffic is permitted.
    ACTIVE = "active"

    #: A DISCONNECT has been sent or received; draining before teardown.
    CLOSING = "closing"

    #: Connection and session are gone. Terminal.
    CLOSED = "closed"


#: States a session never leaves.
TERMINAL_STATES: frozenset[SessionState] = frozenset({SessionState.CLOSED})
