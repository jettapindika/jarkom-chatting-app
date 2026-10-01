"""The protocol's machine-readable error codes.

Every failure the protocol reports to a peer travels as an ``ERROR`` message --
or a ``CONNECT_ERR`` during the handshake -- carrying one of these values in
``payload.code``. The peer branches on the code; ``payload.message`` is prose
for the human and is never parsed.

They live in the presentation layer, beside :class:`~presentation.message_types.
MessageType`, because they are the same kind of thing: wire vocabulary. If the
server that emits a code and the client that reacts to it could disagree about
the spelling, the peer would silently fail to recognise a message -- the kind of
bug that surfaces during a demo and nowhere earlier.

Being a ``str`` subclass means a member serialises as its bare value, so these
can be put straight into a payload without ``.value``.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["ErrorCode"]


class ErrorCode(str, Enum):
    """Codes carried in ``payload.code`` on ERROR and CONNECT_ERR messages."""

    # -- Handshake rejections (CONNECT_ERR) --------------------------------

    #: The requested nickname is already held by an active session.
    NICK_TAKEN = "NICK_TAKEN"

    #: The requested nickname is empty, too long, or contains whitespace.
    NICK_INVALID = "NICK_INVALID"

    #: The peer speaks a different protocol revision.
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"

    #: The server has reached its configured connection limit.
    SERVER_FULL = "SERVER_FULL"

    # -- Runtime errors (ERROR) -------------------------------------------

    #: A frame's announced length exceeded ``MAX_FRAME_SIZE``. Fatal: the stream
    #: is no longer trustworthy, so the connection closes after reporting it.
    FRAME_TOO_LARGE = "FRAME_TOO_LARGE"

    #: The payload was not valid UTF-8 JSON, or was JSON that is not a protocol
    #: message. Recoverable, up to a small budget.
    MALFORMED = "MALFORMED"

    #: The ``type`` field named a message type this build does not implement.
    #: Recoverable: the frame boundary was still intact.
    UNKNOWN_TYPE = "UNKNOWN_TYPE"

    #: The type is real but this peer may not send it -- a client sending
    #: CONNECT_OK, say. Distinct from UNKNOWN_TYPE because the diagnosis
    #: ("wrong direction") is different from "we do not know this message".
    UNEXPECTED_TYPE = "UNEXPECTED_TYPE"

    #: A chat body exceeded :data:`~presentation.schema.MAX_TEXT_LENGTH`.
    TEXT_TOO_LONG = "TEXT_TOO_LONG"

    #: A private message addressed a nickname that is not online.
    NO_SUCH_USER = "NO_SUCH_USER"

    #: Chat traffic arrived before the handshake completed.
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"

    #: The server hit an unexpected fault while handling this message. The
    #: connection survives; the server log has the traceback.
    INTERNAL = "INTERNAL"
