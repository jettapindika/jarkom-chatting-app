"""Tests for the hand-written RFC 6455 server.

These drive the server the way a browser would: real sockets, real handshake
bytes, masked frames. The point is that we implement the protocol ourselves, so
the test cannot lean on a WebSocket library either.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading
import unittest

from bridge.websocket import (
    CLOSE_NORMAL,
    CLOSE_PROTOCOL_ERROR,
    MAX_FRAME_BYTES,
    OPCODE_CLOSE,
    OPCODE_CONTINUATION,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    WebSocket,
    WebSocketClosed,
    WebSocketError,
    accept_key,
    build_client_frame,
)

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


_CLIENT_KEY = "dGhlIHNhbXBsZSBub25jZQ=="


def _send_client_handshake(sock: socket.socket, key: str = _CLIENT_KEY) -> None:
    """Send the upgrade request. The response is read once the server upgrades."""
    sock.sendall(
        (
            "GET /chat HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
    )


def _read_handshake_response(sock: socket.socket) -> dict[str, str]:
    """Read and parse the server's upgrade response."""
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    head, _, _rest = response.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {"_status": lines[0]}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return headers


def _read_server_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Read one unmasked server frame."""
    first, second = _recv(sock, 2)
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _recv(sock, 2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _recv(sock, 8))
    return opcode, _recv(sock, length)


def _recv(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise AssertionError("connection closed early")
        data += chunk
    return data


class _Pair:
    """A connected server WebSocket and the raw client socket facing it.

    ``auto_read`` drains the server's inbound iterator on a background thread.
    Needed whenever the server replies without being asked -- a pong, or a close
    frame -- because ``__iter__`` is a generator and does nothing until driven.
    """

    def __init__(self, *, auto_read: bool = False) -> None:
        self.server_sock, self.client_sock = socket.socketpair()
        self.client_sock.settimeout(5.0)
        _send_client_handshake(self.client_sock)
        self.ws = WebSocket.upgrade(self.server_sock)
        self.headers = _read_handshake_response(self.client_sock)
        self.received: list[tuple[int, bytes]] = []
        self.reader_error: BaseException | None = None
        self.reader_done = threading.Event()
        self._reader: threading.Thread | None = None
        if auto_read:
            self._start_reader()

    def _start_reader(self) -> None:
        def drain() -> None:
            try:
                for item in self.ws:
                    self.received.append(item)
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                self.reader_error = exc
            finally:
                self.reader_done.set()

        self._reader = threading.Thread(target=drain, name="ws-test-reader", daemon=True)
        self._reader.start()

    def wait_for_reader(self, *, timeout: float = 5.0) -> bool:
        """Block until the reader thread stops."""
        return self.reader_done.wait(timeout)

    def close(self) -> None:
        for sock in (self.client_sock, self.server_sock):
            try:
                sock.close()
            except OSError:
                pass


class AcceptKeyTests(unittest.TestCase):
    def test_matches_the_rfc_test_vector(self) -> None:
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_is_the_sha1_of_key_plus_guid(self) -> None:
        key = "AQIDBAUGBwgJCgsMDQ4PEC=="
        expected = base64.b64encode(hashlib.sha1(key.encode() + _GUID).digest()).decode()
        self.assertEqual(accept_key(key), expected)


class HandshakeTests(unittest.TestCase):
    def test_responds_101_with_the_correct_accept_header(self) -> None:
        pair = _Pair()
        try:
            self.assertEqual(pair.headers["_status"], "HTTP/1.1 101 Switching Protocols")
            self.assertEqual(
                pair.headers["sec-websocket-accept"], accept_key("dGhlIHNhbXBsZSBub25jZQ==")
            )
            self.assertEqual(pair.headers["upgrade"].lower(), "websocket")
        finally:
            pair.close()

    def test_rejects_a_request_without_a_key(self) -> None:
        server, client = socket.socketpair()
        try:
            client.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            with self.assertRaises(WebSocketError):
                WebSocket.upgrade(server)
        finally:
            server.close()
            client.close()


class FrameTests(unittest.TestCase):
    def test_text_round_trip(self) -> None:
        pair = _Pair()
        try:
            pair.ws.send_text("halo dunia")
            opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(opcode, OPCODE_TEXT)
            self.assertEqual(payload.decode("utf-8"), "halo dunia")
        finally:
            pair.close()

    def test_non_ascii_survives_utf8_encoding(self) -> None:
        pair = _Pair()
        try:
            text = "日本語 — café — 🚀"
            pair.ws.send_text(text)
            _opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(payload.decode("utf-8"), text)
        finally:
            pair.close()

    def test_a_long_payload_uses_the_16_bit_length_form(self) -> None:
        pair = _Pair()
        try:
            pair.ws.send_text("x" * 300)
            _opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(len(payload), 300)
        finally:
            pair.close()

    def test_inbound_text_is_yielded(self) -> None:
        pair = _Pair()
        try:
            pair.client_sock.sendall(build_client_frame(OPCODE_TEXT, "dari browser".encode()))
            opcode, payload = next(iter(pair.ws))
            self.assertEqual(opcode, OPCODE_TEXT)
            self.assertEqual(payload.decode("utf-8"), "dari browser")
        finally:
            pair.close()

    def test_fragmented_message_is_reassembled(self) -> None:
        pair = _Pair()
        try:
            pair.client_sock.sendall(build_client_frame(OPCODE_TEXT, b"potongan-", fin=False))
            pair.client_sock.sendall(build_client_frame(OPCODE_CONTINUATION, b"pertama", fin=False))
            pair.client_sock.sendall(build_client_frame(OPCODE_CONTINUATION, b"-kedua", fin=True))
            opcode, payload = next(iter(pair.ws))
            self.assertEqual(opcode, OPCODE_TEXT)
            self.assertEqual(payload.decode("utf-8"), "potongan-pertama-kedua")
        finally:
            pair.close()

    def test_a_continuation_without_a_start_is_a_protocol_error(self) -> None:
        pair = _Pair()
        try:
            pair.client_sock.sendall(build_client_frame(OPCODE_CONTINUATION, b"yatim", fin=True))
            with self.assertRaises(WebSocketError):
                next(iter(pair.ws))
            opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(opcode, OPCODE_CLOSE)
            self.assertEqual(struct.unpack("!H", payload[:2])[0], CLOSE_PROTOCOL_ERROR)
        finally:
            pair.close()

    def test_an_unmasked_client_frame_is_rejected(self) -> None:
        pair = _Pair()
        try:
            # Hand-rolled unmasked frame: the RFC forbids this from a client.
            pair.client_sock.sendall(struct.pack("!BB", 0x80 | OPCODE_TEXT, 5) + b"hello")
            with self.assertRaises(WebSocketError):
                next(iter(pair.ws))
            opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(opcode, OPCODE_CLOSE)
            self.assertEqual(struct.unpack("!H", payload[:2])[0], CLOSE_PROTOCOL_ERROR)
        finally:
            pair.close()

    def test_ping_is_answered_with_a_pong(self) -> None:
        pair = _Pair(auto_read=True)
        try:
            pair.client_sock.sendall(build_client_frame(OPCODE_PING, b"hb"))
            opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(opcode, OPCODE_PONG)
            self.assertEqual(payload, b"hb")
        finally:
            pair.close()

    def test_a_ping_does_not_end_the_message_stream(self) -> None:
        pair = _Pair()
        try:
            pair.client_sock.sendall(build_client_frame(OPCODE_PING, b"hb"))
            pair.client_sock.sendall(build_client_frame(OPCODE_TEXT, b"setelah ping"))
            stream = iter(pair.ws)
            opcode, payload = next(stream)
            self.assertEqual((opcode, payload), (OPCODE_TEXT, b"setelah ping"))
        finally:
            pair.close()

    def test_close_frame_ends_iteration(self) -> None:
        pair = _Pair()
        try:
            pair.client_sock.sendall(build_client_frame(OPCODE_CLOSE, struct.pack("!H", CLOSE_NORMAL)))
            self.assertEqual(list(pair.ws), [])
            opcode, _payload = _read_server_frame(pair.client_sock)
            self.assertEqual(opcode, OPCODE_CLOSE)
        finally:
            pair.close()

    def test_oversized_frame_is_refused(self) -> None:
        pair = _Pair()
        try:
            header = struct.pack("!BBQ", 0x80 | OPCODE_TEXT, 0x80 | 127, MAX_FRAME_BYTES + 1)
            pair.client_sock.sendall(header)
            with self.assertRaises(WebSocketError):
                next(iter(pair.ws))
            opcode, payload = _read_server_frame(pair.client_sock)
            self.assertEqual(opcode, OPCODE_CLOSE)
            self.assertEqual(struct.unpack("!H", payload[:2])[0], 1009)
        finally:
            pair.close()


class LifecycleTests(unittest.TestCase):
    def test_sending_on_a_closed_socket_raises(self) -> None:
        pair = _Pair()
        try:
            pair.ws.close()
            self.assertTrue(pair.ws.closed)
            with self.assertRaises(WebSocketClosed):
                pair.ws.send_text("terlambat")
        finally:
            pair.close()

    def test_close_is_idempotent(self) -> None:
        pair = _Pair()
        try:
            pair.ws.close()
            pair.ws.close()
            self.assertTrue(pair.ws.closed)
        finally:
            pair.close()

    def test_abrupt_client_disconnect_ends_iteration(self) -> None:
        pair = _Pair()
        try:
            pair.client_sock.close()
            self.assertEqual(list(pair.ws), [])
        finally:
            pair.close()


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_senders_do_not_interleave_frames(self) -> None:
        pair = _Pair()
        try:
            payloads = [f"pesan-{index:03d}".encode() for index in range(40)]

            def send(payload: bytes) -> None:
                pair.ws.send_bytes(payload)

            threads = [threading.Thread(target=send, args=(item,)) for item in payloads]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            received = [_read_server_frame(pair.client_sock)[1] for _ in payloads]
            self.assertEqual(sorted(received), sorted(payloads))
        finally:
            pair.close()


if __name__ == "__main__":
    unittest.main()
