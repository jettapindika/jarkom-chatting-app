"""The browser bridge: WebSocket on one side, our TCP protocol on the other.

The bridge exists because a Next.js route handler cannot hold a long-lived TCP
connection. It reuses ``session`` and ``presentation`` unchanged, so the browser
client speaks the same protocol as the CLI, byte for byte.
"""

from bridge.tcp_client import ChatConnection, ChatConnectionError
from bridge.websocket import (
    CLOSE_NORMAL,
    CLOSE_PROTOCOL_ERROR,
    WebSocket,
    WebSocketClosed,
    WebSocketError,
    accept_key,
    build_client_frame,
    handshake,
)

__all__ = [
    "CLOSE_NORMAL",
    "CLOSE_PROTOCOL_ERROR",
    "ChatConnection",
    "ChatConnectionError",
    "WebSocket",
    "WebSocketClosed",
    "WebSocketError",
    "accept_key",
    "build_client_frame",
    "handshake",
]
