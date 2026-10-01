"""The bridge end to end: browser -> bridge -> chat server -> back.

``test_websocket.py`` proves the RFC 6455 implementation and ``test_server.py``
proves the chat server. This file proves the piece in between: that a browser
speaking WebSocket reaches the real chat server through the bridge and gets the
real answers back, and that the trace events the visualizer renders actually
arrive.

Everything runs in-process on loopback. The bridge is a separate process in
deployment, but the threads here exercise the same code paths, and an in-process
test catches a broken contract without paying for a subprocess per case.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import threading
import time
import unittest

from bridge.main import BridgeServer
from bridge.websocket import OPCODE_TEXT, build_client_frame
from server import ChatServer, ServerConfig

#: How long a test waits for something that should arrive promptly. Every
#: wait ends the moment its condition is met, so this is a ceiling, not a cost.
WAIT = 5.0


class BrowserClient:
    """The browser's half of the WebSocket protocol, hand-rolled."""

    #: Frames received while connecting, kept by the test harness.
    handshake_frames: list[dict]

    def __init__(self, port: int) -> None:
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self._sock.settimeout(0.2)
        self.handshake_frames = []
        self._sock.sendall(
            b"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        response = b""
        while b"\r\n\r\n" not in response:
            response += self._sock.recv(4096)
        if not response.startswith(b"HTTP/1.1 101"):
            raise AssertionError(f"handshake refused: {response[:80]!r}")

    def send(self, payload: dict) -> None:
        """Send one JSON message as a masked text frame."""
        self._sock.sendall(build_client_frame(OPCODE_TEXT, json.dumps(payload).encode("utf-8")))

    def send_raw(self, payload: bytes) -> None:
        """Send arbitrary bytes as a text frame, for the malformed-input cases."""
        self._sock.sendall(build_client_frame(OPCODE_TEXT, payload))

    def _recv_exactly(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self._sock.recv(count - len(data))
            if not chunk:
                raise ConnectionResetError("bridge closed the connection")
            data += chunk
        return data

    def drain(self, *, seconds: float = WAIT, until=None) -> list[dict]:
        """Collect messages until ``until`` is satisfied or the window closes.

        Waiting out a fixed window on every assertion would make the suite slow
        for no gain, so callers pass the condition they are waiting for and the
        read stops as soon as it holds.
        """
        collected: list[dict] = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            if until is not None and until(collected):
                break
            try:
                first, second = self._recv_exactly(2)
                length = second & 0x7F
                if length == 126:
                    (length,) = struct.unpack("!H", self._recv_exactly(2))
                elif length == 127:
                    (length,) = struct.unpack("!Q", self._recv_exactly(8))
                payload = self._recv_exactly(length)
            except (TimeoutError, ConnectionResetError):
                continue
            if (first & 0x0F) == OPCODE_TEXT:
                collected.append(json.loads(payload.decode("utf-8")))
        return collected

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _has_state(state: str):
    """A predicate matching a bridge state frame."""
    return lambda frames: any(
        frame.get("type") == "state" and frame.get("state") == state for frame in frames
    )


def _has_line(needle: str):
    """A predicate matching a rendered line containing ``needle``."""
    return lambda frames: any(
        frame.get("type") == "line" and needle in frame["text"] for frame in frames
    )


def _has_error(needle: str = ""):
    """A predicate matching an error frame whose text contains ``needle``."""
    return lambda frames: any(
        frame.get("type") == "error" and needle in frame["message"] for frame in frames
    )


def _has_trace(layer: int | None = None):
    """A predicate matching trace frames, optionally from one layer."""
    return lambda frames: any(
        frame.get("type") == "trace" and (layer is None or frame["event"]["layer"] == layer)
        for frame in frames
    )


def _lines(frames: list[dict]) -> list[str]:
    return [frame["text"] for frame in frames if frame.get("type") == "line"]


def _events(frames: list[dict]) -> list[dict]:
    return [frame["event"] for frame in frames if frame.get("type") == "trace"]


class BridgeTestCase(unittest.TestCase):
    """A real chat server plus a real bridge, both on private threads."""

    def setUp(self) -> None:
        self.server = ChatServer(ServerConfig(host="127.0.0.1", port=0, handshake_timeout=5.0))
        self.browsers: list[BrowserClient] = []

        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()
        self._call(self.server.start())

        self.bridge = BridgeServer(
            host="127.0.0.1",
            port=0,
            chat_host="127.0.0.1",
            chat_port=self.server.port,
            trace_stdout=False,
        )
        self.bridge.start()  # binds, so the port is known before serving
        self._bridge_thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
        self._bridge_thread.start()

    def tearDown(self) -> None:
        for browser in self.browsers:
            browser.close()
        self.bridge.shutdown()
        self._bridge_thread.join(timeout=5)
        self._call(self.server.shutdown())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._loop_thread.join(timeout=5)
        self.loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=10)

    def browser(self, nickname: str) -> BrowserClient:
        """Connect a browser and complete the bridge's chat handshake.

        The frames received during the handshake are kept on the client: the
        drain that waits for ``connected`` consumes them, and a test that wants
        to inspect them should not have to race a second read.
        """
        browser = BrowserClient(self.bridge.port)
        self.browsers.append(browser)
        browser.send({"type": "connect", "nick": nickname})
        frames = browser.drain(until=_has_state("connected"))
        self.assertTrue(
            _has_state("connected")(frames),
            f"bridge never reported a connection: {frames}",
        )
        browser.handshake_frames = frames
        return browser


class HandshakeTests(BridgeTestCase):
    def test_connecting_reports_the_server_assigned_session(self) -> None:
        browser = self.browser("webuser")
        connected = next(
            frame for frame in browser.handshake_frames if frame.get("state") == "connected"
        )

        self.assertEqual(connected["nick"], "webuser")
        self.assertTrue(connected["sessionId"], "no session id was reported")

    def test_a_taken_nickname_is_refused_with_the_reason(self) -> None:
        self.browser("bentrok")

        second = BrowserClient(self.bridge.port)
        self.browsers.append(second)
        second.send({"type": "connect", "nick": "bentrok"})
        frames = second.drain(until=_has_error())

        self.assertTrue(
            _has_error("NICK_TAKEN")(frames),
            f"a duplicate nickname was not refused with its reason: {frames}",
        )
        self.assertFalse(
            _has_state("connected")(frames), f"the second browser was let in: {frames}"
        )


class ChatFlowTests(BridgeTestCase):
    def test_a_broadcast_round_trips_through_the_bridge(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "halo dari browser"})
        frames = browser.drain(until=_has_line("halo dari browser"))

        self.assertTrue(
            _has_line("halo dari browser")(frames),
            f"the broadcast never came back: {_lines(frames)}",
        )

    def test_the_structured_message_carries_the_server_side_sender(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "halo"})
        frames = browser.drain(
            until=lambda collected: any(
                frame.get("type") == "message" and frame["message"]["type"] == "BROADCAST"
                for frame in collected
            )
        )

        broadcasts = [
            frame["message"]
            for frame in frames
            if frame.get("type") == "message" and frame["message"]["type"] == "BROADCAST"
        ]
        self.assertTrue(broadcasts, "the broadcast was not delivered as a structured message")
        self.assertEqual(broadcasts[0]["sender"], "webuser")
        self.assertEqual(broadcasts[0]["payload"]["text"], "halo")

    def test_two_browsers_see_each_others_messages(self) -> None:
        first = self.browser("satu")
        second = self.browser("dua")

        second.send({"type": "input", "text": "pesan untuk semua"})
        received = first.drain(until=_has_line("pesan untuk semua"))

        self.assertTrue(
            _has_line("pesan untuk semua")(received),
            f"the first browser missed the broadcast: {_lines(received)}",
        )

    def test_list_renders_the_roster(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "/list"})
        frames = browser.drain(until=_has_line("webuser"))

        self.assertTrue(
            _has_line("webuser")(frames),
            f"/list did not name the connected user: {_lines(frames)}",
        )

    def test_a_private_message_to_a_missing_user_reports_no_such_user(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "/msg hantu hai"})
        frames = browser.drain(until=_has_line("NO_SUCH_USER"))

        self.assertTrue(
            _has_line("NO_SUCH_USER")(frames),
            f"no NO_SUCH_USER surfaced: {_lines(frames)}",
        )

    def test_disconnect_reports_a_closed_state(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "disconnect"})
        frames = browser.drain(until=_has_state("closed"))

        self.assertTrue(_has_state("closed")(frames), f"no closed state: {frames}")


class TraceTests(BridgeTestCase):
    def test_trace_events_span_every_implemented_layer(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "halo"})
        frames = browser.drain(
            until=lambda collected: len({e["layer"] for e in _events(collected)}) == 4
        )

        events = _events(frames)
        self.assertTrue(events, "the visualizer received no trace events")
        self.assertEqual(sorted({event["layer"] for event in events}), [4, 5, 6, 7])
        self.assertEqual({event["node"] for event in events}, {"bridge"})

    def test_both_directions_are_reported(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "halo"})
        frames = browser.drain(
            until=lambda collected: {
                e["direction"] for e in _events(collected)
            }
            == {"outbound", "inbound"}
        )

        self.assertEqual({event["direction"] for event in _events(frames)}, {"outbound", "inbound"})

    def test_every_trace_event_carries_the_visualizer_schema(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "halo"})
        frames = browser.drain(until=_has_trace())

        events = _events(frames)
        self.assertTrue(events, "no trace events to inspect")

        required = {
            "traceId",
            "sessionId",
            "direction",
            "layer",
            "layerName",
            "pduType",
            "node",
            "summary",
            "payloadPreview",
            "sizeBytes",
            "timestamp",
        }
        for event in events:
            missing = required - set(event)
            self.assertFalse(missing, f"trace event is missing {missing}: {event}")

    def test_one_message_shares_one_trace_id_across_layers(self) -> None:
        browser = self.browser("webuser")
        browser.send({"type": "input", "text": "halo"})
        frames = browser.drain(
            until=lambda collected: len(
                {e["traceId"] for e in _events(collected) if e["direction"] == "outbound"}
            )
            > 0
        )

        outbound = [event for event in _events(frames) if event["direction"] == "outbound"]
        self.assertTrue(outbound, "no outbound trace events")
        self.assertEqual(
            len({event["traceId"] for event in outbound}),
            1,
            "one message should carry one trace id down the stack",
        )


class ProtocolGuardTests(BridgeTestCase):
    def test_input_before_connecting_is_refused(self) -> None:
        browser = BrowserClient(self.bridge.port)
        self.browsers.append(browser)
        browser.send({"type": "input", "text": "halo"})
        frames = browser.drain(until=_has_error())

        self.assertTrue(
            _has_error()(frames), "typing before connecting was silently ignored"
        )

    def test_a_non_json_frame_is_reported(self) -> None:
        browser = BrowserClient(self.bridge.port)
        self.browsers.append(browser)
        browser.send_raw(b"bukan json")
        frames = browser.drain(until=_has_error())

        self.assertTrue(_has_error()(frames), "a non-JSON frame produced no error")

    def test_an_unknown_message_type_is_reported(self) -> None:
        browser = BrowserClient(self.bridge.port)
        self.browsers.append(browser)
        browser.send({"type": "terbang"})
        frames = browser.drain(until=_has_error())

        self.assertTrue(_has_error()(frames), "an unknown message type produced no error")

    def test_a_json_array_is_rejected(self) -> None:
        browser = BrowserClient(self.bridge.port)
        self.browsers.append(browser)
        browser.send_raw(b"[1, 2, 3]")
        frames = browser.drain(until=_has_error())

        self.assertTrue(_has_error()(frames), "a JSON array was accepted as a command")


if __name__ == "__main__":
    unittest.main()
