"""The bridge's connection to the chat server, plus the events it reports.

This is deliberately a thin wrapper: the handshake, framing, sequencing and
heartbeat all come from ``session`` and ``transport``, the same modules the CLI
client uses. The bridge adds exactly one thing -- a callback interface, so a
thread reading from TCP can hand messages to whatever is driving the browser
without knowing whether that is a WebSocket or a test harness.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from session.client_session import ClientSession
from session.heartbeat import HeartbeatPolicy
from trace import TraceEmitter
from transport import BlockingTcpChannel, ConnectionClosedError, TransportTimeout
from transport.channel import DEFAULT_CONNECT_TIMEOUT

__all__ = ["ChatConnection", "ChatConnectionError"]

#: Called with each decoded inbound message.
MessageHandler = Callable[[dict[str, Any]], None]


class ChatConnectionError(Exception):
    """The bridge could not reach or stay connected to the chat server."""


class ChatConnection:
    """A blocking client session driven from a background reader thread.

    ``ClientSession`` is blocking, and the bridge is threaded: one thread reads
    TCP, and the WebSocket side runs on its own thread. That keeps the two
    hops independent -- a slow browser cannot stall the TCP read, and a burst
    of chat traffic cannot block the socket accept loop.
    """

    __slots__ = ("_handlers", "_lock", "_running", "_session", "_thread")

    def __init__(
        self,
        host: str,
        port: int,
        nickname: str,
        *,
        emitter: TraceEmitter | None = None,
        heartbeat: HeartbeatPolicy | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._handlers: list[MessageHandler] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

        try:
            channel = BlockingTcpChannel.connect(
                host, port, emitter=emitter, connect_timeout=connect_timeout
            )
        except OSError as exc:
            raise ChatConnectionError(f"cannot reach {host}:{port}: {exc}") from exc

        self._session = ClientSession(channel, nickname=nickname, emitter=emitter, heartbeat=heartbeat)

        try:
            self._session.connect()
        except Exception as exc:
            self._session.close()
            raise ChatConnectionError(f"handshake refused: {exc}") from exc

    @property
    def session(self) -> ClientSession:
        """The underlying session, for callers that need its identity."""
        return self._session

    @property
    def nickname(self) -> str:
        """The nickname the server accepted."""
        return self._session.nickname

    @property
    def session_id(self) -> str:
        """The server-assigned session id."""
        return self._session.session_id

    def on_message(self, handler: MessageHandler) -> None:
        """Register a callback for inbound messages."""
        with self._lock:
            self._handlers.append(handler)

    def send(self, message: dict[str, Any], *, summary: str | None = None) -> str | None:
        """Send one message and return its trace id."""
        return self._session.send(message, summary=summary)

    def start(self) -> None:
        """Begin reading in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, name="bridge-tcp-reader", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        """Forward every inbound message to the handlers until the link drops."""
        while self._running:
            try:
                _header, message = self._session.receive(timeout=1.0)
            except TransportTimeout:
                continue
            except (ConnectionClosedError, OSError):
                break
            except Exception:  # noqa: BLE001 - one bad frame must not kill the bridge
                break

            with self._lock:
                handlers = list(self._handlers)
            for handler in handlers:
                try:
                    handler(message)
                except Exception:  # noqa: BLE001 - a broken consumer is not a broken link
                    continue

        self._running = False

    def close(self) -> None:
        """Stop reading and close the link. Idempotent."""
        self._running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 - closing must never raise
            pass
