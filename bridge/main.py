"""The bridge: one browser tab on one side, one TCP chat connection on the other.

    browser  ──WebSocket──▶  bridge  ──TCP (our protocol)──▶  chat server

Why this process exists at all: a Next.js route handler cannot hold a long-lived
TCP connection. Route handlers are request-scoped, so a chat socket parked in
one would be closed the moment the request finished. The bridge is therefore a
separate long-running process, and Next.js talks to it over WebSocket.

Why it is written in Python rather than Node: it imports ``session`` and
``presentation`` directly, so the bridge speaks the *exact* protocol the CLI
client speaks. Re-implementing the codec in TypeScript would create a second
definition of the wire format, and two definitions drift.

The bridge owns no chat rules of its own. Input lines go through the same
``app.commands`` and ``app.chat_logic`` the CLI uses, so ``/nick``, ``/msg`` and
``/list`` behave identically in both clients.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chat_logic import ActionKind, format_message, plan  # noqa: E402
from app.commands import parse_input  # noqa: E402
from app.config import DEFAULT_HOST, DEFAULT_PORT, HOST_ENV, PORT_ENV, env_flag, env_int, env_str  # noqa: E402
from bridge.tcp_client import ChatConnection, ChatConnectionError  # noqa: E402
from bridge.websocket import WebSocket, WebSocketClosed, WebSocketError  # noqa: E402
from presentation import MessageType  # noqa: E402
from server.logger import configure_logging, get_logger  # noqa: E402
from session.heartbeat import HeartbeatPolicy  # noqa: E402
from trace import FanoutTraceSink, JsonLinesTraceSink, Node, TraceEmitter, TraceEvent  # noqa: E402

_log = get_logger("bridge")

DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 8787
WS_HOST_ENV = "CHAT_BRIDGE_HOST"
WS_PORT_ENV = "CHAT_BRIDGE_PORT"
HEARTBEAT_TIMEOUT_FACTOR = 3.0


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class WebSocketTraceSink:
    """Forwards layer events to one browser tab.

    The visualizer page renders whatever arrives here, so the browser sees the
    same events the CLI prints under ``--trace`` -- emitted from the same
    emitter, at the same points in the stack.
    """

    __slots__ = ("_client",)

    def __init__(self, client: "BridgeClient") -> None:
        self._client = client

    def emit(self, event: TraceEvent) -> None:
        """Send one event, ignoring a browser that has already gone away."""
        self._client.send_json({"type": "trace", "event": event.to_dict()})


class BridgeClient:
    """One browser tab: its WebSocket, and its chat connection once it connects."""

    def __init__(self, sock: socket.socket, *, host: str, port: int, trace_stdout: bool) -> None:
        self._ws = WebSocket.upgrade(sock)
        self._host = host
        self._port = port
        self._trace_stdout = trace_stdout
        self._connection: ChatConnection | None = None
        self._nickname = ""
        self._send_lock = threading.Lock()
        self._closed = False

    @property
    def peer(self) -> str:
        return self._ws.peer

    def send_json(self, payload: dict[str, Any]) -> None:
        """Send one JSON message, tolerating a browser that has disconnected."""
        if self._closed:
            return
        with self._send_lock:
            try:
                self._ws.send_text(_dumps(payload))
            except (WebSocketClosed, OSError):
                self._closed = True

    def run(self) -> None:
        """Serve this browser tab until it closes or disconnects."""
        try:
            for opcode, payload in self._ws:
                if opcode != 0x1:
                    continue
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json({"type": "error", "message": "pesan bukan JSON yang valid"})
                    continue
                if not isinstance(message, dict):
                    self.send_json({"type": "error", "message": "pesan harus berupa objek JSON"})
                    continue
                self._handle(message)
        except (WebSocketError, WebSocketClosed, OSError):
            # WebSocketClosed is not a WebSocketError: an abrupt browser
            # disconnect -- a closed tab, a dropped wifi link -- lands here,
            # and it is an ordinary end of session, not a fault.
            pass
        finally:
            self._teardown()

    def _handle(self, message: dict[str, Any]) -> None:
        kind = message.get("type")

        if kind == "connect":
            self._connect(str(message.get("nick", "")).strip())
        elif kind == "input":
            self._input(str(message.get("text", "")))
        elif kind == "disconnect":
            self._disconnect()
        else:
            self.send_json({"type": "error", "message": f"tipe pesan tidak dikenal: {kind!r}"})

    def _connect(self, nickname: str) -> None:
        if self._connection is not None:
            self.send_json({"type": "error", "message": "sudah terhubung"})
            return
        if not nickname:
            self.send_json({"type": "error", "message": "nickname tidak boleh kosong"})
            return

        self.send_json({"type": "state", "state": "connecting", "nick": nickname})

        sinks: list[Any] = [WebSocketTraceSink(self)]
        if self._trace_stdout:
            sinks.append(JsonLinesTraceSink(sys.stderr))

        emitter = TraceEmitter(
            FanoutTraceSink(sinks), node=Node.BRIDGE, enabled=True, session_id=None
        )

        heartbeat = HeartbeatPolicy(
            interval=15.0, timeout=15.0 * HEARTBEAT_TIMEOUT_FACTOR
        )

        try:
            connection = ChatConnection(
                self._host, self._port, nickname, emitter=emitter, heartbeat=heartbeat
            )
        except ChatConnectionError as exc:
            self.send_json({"type": "state", "state": "closed", "nick": nickname})
            self.send_json({"type": "error", "message": str(exc)})
            return

        self._connection = connection
        self._nickname = connection.nickname
        emitter.set_session_id(connection.session_id)

        connection.on_message(self._on_chat_message)
        connection.start()

        self.send_json(
            {
                "type": "state",
                "state": "connected",
                "nick": connection.nickname,
                "sessionId": connection.session_id,
            }
        )

    def _input(self, line: str) -> None:
        connection = self._connection
        if connection is None:
            self.send_json({"type": "error", "message": "belum terhubung"})
            return
        if not line.strip():
            return

        action = plan(parse_input(line), nickname=self._nickname)

        if action.kind is ActionKind.QUIT:
            self._disconnect()
            return
        if action.kind is ActionKind.SHOW:
            self.send_json({"type": "line", "text": action.text})
            return

        assert action.message is not None  # SEND always carries one
        try:
            connection.send(action.message, summary=f"L7 input: {line[:60]}")
        except Exception as exc:  # noqa: BLE001 - report, do not kill the tab
            self.send_json({"type": "error", "message": f"gagal mengirim: {exc}"})

    def _on_chat_message(self, message: dict[str, Any]) -> None:
        """Render one inbound chat message for the browser."""
        if message.get("type") is MessageType.NICK_OK:
            self._nickname = str(message["payload"].get("nick", self._nickname))

        rendered = format_message(message, nickname=self._nickname)
        if rendered is not None:
            self.send_json({"type": "line", "text": rendered})
        # The structured form goes along too: the visualizer wants fields, not
        # a display string.
        self.send_json({"type": "message", "message": message})

    def _disconnect(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        self.send_json({"type": "state", "state": "closed", "nick": self._nickname})

    def _teardown(self) -> None:
        self._disconnect()
        self._closed = True
        self._ws.close()


class BridgeServer:
    """Accepts browser connections and gives each one a thread."""

    def __init__(self, *, host: str, port: int, chat_host: str, chat_port: int, trace_stdout: bool) -> None:
        self._host = host
        self._port = port
        self._chat_host = chat_host
        self._chat_port = chat_port
        self._trace_stdout = trace_stdout
        self._server: socket.socket | None = None
        self._closing = threading.Event()
        self._clients: list[BridgeClient] = []

    @property
    def port(self) -> int:
        """The bound port, which matters when the requested port was 0."""
        assert self._server is not None
        return int(self._server.getsockname()[1])

    def start(self) -> None:
        """Bind the listening socket. Idempotent, and separate from the accept
        loop so a caller can read :attr:`port` before serving."""
        if self._server is not None:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self._host, self._port))
        server.listen(32)
        self._server = server

    def serve_forever(self) -> None:
        """Accept browser connections until :meth:`shutdown`."""
        self.start()
        _log.info(
            "bridge listening on %s:%s -> chat server %s:%s",
            self._host,
            self.port,
            self._chat_host,
            self._chat_port,
        )

        while not self._closing.is_set():
            try:
                sock, _addr = self._server.accept()
            except OSError:
                break
            threading.Thread(
                target=self._serve_client, args=(sock,), name="bridge-client", daemon=True
            ).start()

    def _serve_client(self, sock: socket.socket) -> None:
        try:
            client = BridgeClient(
                sock, host=self._chat_host, port=self._chat_port, trace_stdout=self._trace_stdout
            )
        except (WebSocketError, OSError) as exc:
            _log.warning("rejected a browser connection: %s", exc)
            try:
                sock.close()
            except OSError:
                pass
            return

        self._clients.append(client)
        _log.info("browser connected from %s", client.peer)
        try:
            client.run()
        finally:
            _log.info("browser disconnected from %s", client.peer)
            if client in self._clients:
                self._clients.remove(client)

    def shutdown(self) -> None:
        """Stop accepting and drop every browser connection."""
        self._closing.set()
        server = self._server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        for client in list(self._clients):
            client.send_json({"type": "state", "state": "closed", "nick": ""})


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface for the bridge process."""
    parser = argparse.ArgumentParser(
        description="Bridge between browser WebSocket clients and the chat server."
    )
    parser.add_argument(
        "--host", default=env_str(HOST_ENV, DEFAULT_HOST), help="chat server host"
    )
    parser.add_argument(
        "--port", type=int, default=env_int(PORT_ENV, DEFAULT_PORT), help="chat server port"
    )
    parser.add_argument(
        "--ws-host", default=env_str(WS_HOST_ENV, DEFAULT_WS_HOST), help="address to serve browsers on"
    )
    parser.add_argument(
        "--ws-port", type=int, default=env_int(WS_PORT_ENV, DEFAULT_WS_PORT), help="port to serve browsers on"
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        default=env_flag("TRACE_ENABLED", False),
        help="also write trace events to stderr as JSON lines",
    )
    parser.add_argument("--log-level", default="INFO", help="logging level")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    server = BridgeServer(
        host=args.ws_host,
        port=args.ws_port,
        chat_host=args.host,
        chat_port=args.port,
        trace_stdout=args.trace,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log.info("interrupted, shutting down")
    finally:
        server.shutdown()
        _log.info("bridge stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
