"""OSI L5: session identity, sequencing, lifecycle and liveness.

The session layer turns a bare TCP connection into a named session with an
assigned id, a monotonic sequence number, an explicit state machine and a
heartbeat. Layers above it send and receive *messages*; they never see a
session header or a sequence number.
"""

from session.client_session import ClientSession
from session.envelope import (
    SESSION_HEADER_SIZE,
    UNASSIGNED_SESSION_ID,
    SessionHeader,
    decode_envelope,
    encode_envelope,
)
from session.errors import (
    HandshakeError,
    HeartbeatTimeout,
    SessionEnvelopeError,
    SessionError,
    SessionStateError,
)
from presentation.error_codes import ErrorCode
from session.handshake import (
    PROTOCOL_VERSION,
    build_connect,
    build_connect_err,
    build_connect_ok,
    parse_connect,
    parse_connect_err,
    parse_connect_ok,
)
from session.heartbeat import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_HEARTBEAT_TIMEOUT,
    HeartbeatMonitor,
    HeartbeatPolicy,
)
from session.session import SessionCore
from session.session_state import TERMINAL_STATES, SessionState

__all__ = [
    "ClientSession",
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_HEARTBEAT_TIMEOUT",
    "ErrorCode",
    "HandshakeError",
    "HeartbeatMonitor",
    "HeartbeatPolicy",
    "HeartbeatTimeout",
    "PROTOCOL_VERSION",
    "SESSION_HEADER_SIZE",
    "SessionCore",
    "SessionEnvelopeError",
    "SessionError",
    "SessionHeader",
    "SessionState",
    "SessionStateError",
    "TERMINAL_STATES",
    "UNASSIGNED_SESSION_ID",
    "build_connect",
    "build_connect_err",
    "build_connect_ok",
    "decode_envelope",
    "encode_envelope",
    "parse_connect",
    "parse_connect_err",
    "parse_connect_ok",
]
