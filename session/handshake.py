"""The CONNECT / CONNECT_OK handshake: message construction and parsing.

Only the message shapes live here. Driving the exchange needs a socket, so it
lives with the server and the client -- but the *contract* is defined in this
one module, which is what stops the two sides from drifting apart.

Handshake sequence::

    client                                server
      |  CONNECT {nick}          -->        |
      |                                     |  validate + reserve nickname
      |  <--  CONNECT_OK {session_id,nick}  |
      |                                     |
      |  <--  USER_LIST {users}             |  (informational, may precede OK)
      |                                     |
      |  <--  CONNECT_ERR {code,message}    |  (on rejection; then close)

Failure is reported with a ``CONNECT_ERR`` carrying a machine-readable
``code``, so the client can branch on the cause without parsing prose.
"""

from __future__ import annotations

from typing import Any, Mapping

from presentation import MessageType, SchemaViolation, make_message
from presentation.schema import is_valid_nickname
from presentation.error_codes import ErrorCode

__all__ = [
    "PROTOCOL_VERSION",
    "build_connect",
    "build_connect_err",
    "build_connect_ok",
    "parse_connect",
    "parse_connect_err",
    "parse_connect_ok",
]

#: The protocol revision this implementation speaks. A mismatch is rejected
#: during the handshake rather than discovered later as an unexplained frame.
PROTOCOL_VERSION = 1


def build_connect(nickname: str, *, version: int = PROTOCOL_VERSION) -> dict[str, Any]:
    """Build the client's opening CONNECT message.

    Args:
        nickname: the display name being requested.
        version: protocol revision, so the server can refuse an incompatible peer
            before any state is allocated for it.

    Returns:
        The CONNECT envelope.

    Raises:
        SchemaViolation: if ``nickname`` is not a legal display name. Validating
            locally means the user is told immediately instead of after a round
            trip.
    """
    if not is_valid_nickname(nickname):
        raise SchemaViolation(f"invalid nickname: {nickname!r}", field="nickname", value=nickname)
    return make_message(
        MessageType.CONNECT, {"nick": nickname, "version": version}, sender=nickname
    )


def parse_connect(message: Mapping[str, Any]) -> tuple[str, int]:
    """Extract ``(nickname, version)`` from a CONNECT message.

    Raises:
        SchemaViolation: if the message is not a CONNECT, or its payload is
            missing or malformed.
    """
    _require_type(message, MessageType.CONNECT)
    payload = message["payload"]

    nickname = payload.get("nick")
    if not is_valid_nickname(nickname):
        raise SchemaViolation(f"invalid nickname: {nickname!r}", field="nick", value=nickname)

    version = payload.get("version", PROTOCOL_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise SchemaViolation(
            f"version must be an integer, got {type(version).__name__}",
            field="version",
            value=version,
        )
    return nickname, version


def build_connect_ok(session_id: str, nickname: str) -> dict[str, Any]:
    """Build the server's acceptance of a handshake.

    The ``session_id`` here is the client's first knowledge of the id that will
    appear in every subsequent L5 header, which is why it must be assigned by
    the server and never proposed by the client.
    """
    return make_message(
        MessageType.CONNECT_OK,
        {"session_id": session_id, "nick": nickname},
        sender="server",
    )


def parse_connect_ok(message: Mapping[str, Any]) -> tuple[str, str]:
    """Extract ``(session_id, nickname)`` from a CONNECT_OK message."""
    _require_type(message, MessageType.CONNECT_OK)
    payload = message["payload"]

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise SchemaViolation("payload.session_id must be a non-empty string", field="session_id")

    nickname = payload.get("nick")
    if not isinstance(nickname, str) or not nickname:
        raise SchemaViolation("payload.nick must be a non-empty string", field="nick")

    return session_id, nickname


def build_connect_err(code: str, detail: str) -> dict[str, Any]:
    """Build the server's rejection of a handshake.

    ``code`` is an :class:`~presentation.error_codes.ErrorCode`, which is a
    ``str`` subclass, so it serialises as its bare value without unwrapping.
    """
    return make_message(
        MessageType.CONNECT_ERR,
        {"code": code, "message": detail},
        sender="server",
    )


def parse_connect_err(message: Mapping[str, Any]) -> tuple[str, str]:
    """Extract ``(code, detail)`` from a CONNECT_ERR message."""
    _require_type(message, MessageType.CONNECT_ERR)
    payload = message["payload"]

    code = payload.get("code")
    if not isinstance(code, str) or not code:
        raise SchemaViolation("payload.code must be a non-empty string", field="code")

    detail = payload.get("message", "")
    if not isinstance(detail, str):
        raise SchemaViolation("payload.message must be a string", field="message")

    return code, detail


def _require_type(message: Mapping[str, Any], expected: MessageType) -> None:
    """Assert that ``message`` is of ``expected`` type."""
    actual = message.get("type")
    if actual != expected:
        raise SchemaViolation(
            f"expected {expected.value}, got {getattr(actual, 'value', actual)}", field="type"
        )
