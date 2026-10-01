"""The protocol's message type vocabulary.

Kept in its own module so that both the codec (which validates ``type``) and
the application layer (which dispatches on it) import the same enumeration
rather than agreeing on string literals by convention.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["MessageType", "SERVER_TO_CLIENT", "CLIENT_TO_SERVER"]


class MessageType(str, Enum):
    """Every message type the chat protocol defines.

    Being a ``str`` subclass means an instance serialises as its bare value and
    compares equal to the wire string, so dispatch code can stay readable.
    """

    # -- Session establishment (L5 handshake) ------------------------------
    CONNECT = "CONNECT"
    CONNECT_OK = "CONNECT_OK"
    CONNECT_ERR = "CONNECT_ERR"

    # -- Chat traffic (L7) -------------------------------------------------
    BROADCAST = "BROADCAST"
    PRIVATE = "PRIVATE"

    # -- Presence ----------------------------------------------------------
    USER_LIST = "USER_LIST"
    USER_JOIN = "USER_JOIN"
    USER_LEAVE = "USER_LEAVE"

    #: Client asks to rename; the server answers NICK_OK or ERROR. A rename is
    #: not a second handshake -- the session id and sequence number carry over,
    #: so only the display name in the registry changes.
    NICK = "NICK"
    NICK_OK = "NICK_OK"

    # -- Keepalive (L5) ----------------------------------------------------
    PING = "PING"
    PONG = "PONG"

    # -- Control -----------------------------------------------------------
    ERROR = "ERROR"
    DISCONNECT = "DISCONNECT"


#: Types a well-behaved server sends and a client must be able to handle.
SERVER_TO_CLIENT: frozenset[MessageType] = frozenset(
    {
        MessageType.CONNECT_OK,
        MessageType.CONNECT_ERR,
        MessageType.BROADCAST,
        MessageType.PRIVATE,
        MessageType.USER_LIST,
        MessageType.USER_JOIN,
        MessageType.USER_LEAVE,
        MessageType.NICK_OK,
        MessageType.PING,
        MessageType.PONG,
        MessageType.ERROR,
        MessageType.DISCONNECT,
    }
)

#: Types a well-behaved client sends and a server must be able to handle.
CLIENT_TO_SERVER: frozenset[MessageType] = frozenset(
    {
        MessageType.CONNECT,
        MessageType.BROADCAST,
        MessageType.PRIVATE,
        MessageType.USER_LIST,
        MessageType.NICK,
        MessageType.PING,
        MessageType.PONG,
        MessageType.DISCONNECT,
    }
)
