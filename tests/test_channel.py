"""Tests for the L4 channels against real loopback sockets.

The framing tests exercise reassembly with an in-memory decoder. These exercise
the part that only a real socket can prove: that what :meth:`send_frame` writes
is exactly what :meth:`receive_frame` reads back, byte for byte, across the
kernel, and that a peer vanishing mid-frame is reported as a truncated frame
rather than as a short one.
"""

from __future__ import annotations

import socket
import threading
import unittest

from presentation import MessageType, encode, make_message
from session import UNASSIGNED_SESSION_ID, encode_envelope
from trace import CollectingTraceSink, Direction, Layer, Node, TraceEmitter
from transport import (
    LENGTH_PREFIX_SIZE,
    MAX_FRAME_SIZE,
    BlockingTcpChannel,
    ConnectionClosedError,
    FrameTooLargeError,
    TransportTimeout,
    encode_frame,
)
from transport.channel import READ_CHUNK_SIZE


def _listening_socket() -> socket.socket:
    """Bind a loopback listener on an ephemeral port and return it."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener


class LoopbackTestCase(unittest.TestCase):
    """Base class that gives each test one connected socket pair."""

    def setUp(self) -> None:
        self.listener = _listening_socket()
        self.port = self.listener.getsockname()[1]
        self.accepted: socket.socket | None = None
        self._channels: list[BlockingTcpChannel] = []

    def tearDown(self) -> None:
        for channel in self._channels:
            channel.close()
        if self.accepted is not None:
            try:
                self.accepted.close()
            except OSError:
                pass
        self.listener.close()

    def connect(self, **kwargs) -> tuple[BlockingTcpChannel, socket.socket]:
        """Connect a channel to the listener, returning it and the peer socket."""
        ready = threading.Event()

        def accept() -> None:
            try:
                self.accepted, _ = self.listener.accept()
            finally:
                ready.set()

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port, **kwargs)
        self._channels.append(channel)
        ready.wait(timeout=5)
        thread.join(timeout=5)
        assert self.accepted is not None
        return channel, self.accepted


class RoundTripTests(LoopbackTestCase):
    """Bytes written by the channel arrive at the peer unchanged."""

    def test_send_frame_puts_a_length_prefixed_payload_on_the_wire(self) -> None:
        channel, peer = self.connect()

        channel.send_frame(b"halo")

        self.assertEqual(peer.recv(READ_CHUNK_SIZE), b"\x00\x00\x00\x04halo")

    def test_receive_frame_returns_the_payload_without_its_prefix(self) -> None:
        channel, peer = self.connect()
        peer.sendall(encode_frame(b"halo dunia"))

        self.assertEqual(channel.receive_frame(), b"halo dunia")

    def test_back_to_back_frames_are_all_received_in_order(self) -> None:
        channel, peer = self.connect()
        payloads = [f"pesan-{index}".encode() for index in range(50)]
        peer.sendall(b"".join(encode_frame(payload) for payload in payloads))

        self.assertEqual([channel.receive_frame() for _ in payloads], payloads)

    def test_payload_split_across_two_writes_is_reassembled(self) -> None:
        channel, peer = self.connect()
        frame = encode_frame(b"payload that arrives in pieces")

        peer.sendall(frame[:3])
        peer.sendall(frame[3:])

        self.assertEqual(channel.receive_frame(), b"payload that arrives in pieces")

    def test_payload_split_byte_by_byte_over_a_real_socket(self) -> None:
        channel, peer = self.connect()
        frame = encode_frame("halo 日本語".encode())

        def trickle() -> None:
            for index in range(len(frame)):
                peer.sendall(frame[index : index + 1])

        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        try:
            self.assertEqual(channel.receive_frame(), "halo 日本語".encode())
        finally:
            thread.join(timeout=5)

    def test_empty_payload_round_trips(self) -> None:
        channel, peer = self.connect()
        peer.sendall(encode_frame(b""))

        self.assertEqual(channel.receive_frame(), b"")

    def test_maximum_size_payload_round_trips(self) -> None:
        channel, peer = self.connect()
        payload = b"a" * MAX_FRAME_SIZE

        def send() -> None:
            peer.sendall(encode_frame(payload))

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        try:
            self.assertEqual(len(channel.receive_frame()), MAX_FRAME_SIZE)
        finally:
            thread.join(timeout=10)

    def test_full_stack_frame_arrives_with_json_at_offset_twenty_eight(self) -> None:
        channel, peer = self.connect()
        inner = encode(make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi"))
        payload = encode_envelope(inner, session_id=UNASSIGNED_SESSION_ID, sequence=1)

        channel.send_frame(payload)
        wire = _recv_exactly(peer, LENGTH_PREFIX_SIZE + len(payload))

        self.assertEqual(wire[:LENGTH_PREFIX_SIZE], len(payload).to_bytes(4, "big"))
        # 4 bytes L4 + 16 bytes session id + 8 bytes sequence = 28.
        self.assertEqual(wire[28:29], b"{")
        self.assertIn(b'"type":"BROADCAST"', wire)

    def test_oversized_payload_is_refused_before_anything_is_written(self) -> None:
        channel, _ = self.connect()

        with self.assertRaises(FrameTooLargeError):
            channel.send_frame(b"a" * (MAX_FRAME_SIZE + 1))

    def test_oversized_announcement_is_refused_without_buffering_the_payload(self) -> None:
        channel, peer = self.connect()
        peer.sendall((MAX_FRAME_SIZE + 1).to_bytes(LENGTH_PREFIX_SIZE, "big"))

        with self.assertRaises(FrameTooLargeError):
            channel.receive_frame()


class DisconnectionTests(LoopbackTestCase):
    """How the channel reports a peer that goes away."""

    def test_eof_between_frames_raises_connection_closed(self) -> None:
        channel, peer = self.connect()
        peer.close()

        with self.assertRaises(ConnectionClosedError):
            channel.receive_frame()

    def test_eof_mid_frame_raises_connection_closed(self) -> None:
        channel, peer = self.connect()
        peer.sendall(encode_frame(b"complete") + b"\x00\x00\x00\x10half")
        channel.receive_frame()

        # The peer promised 16 bytes and delivered 4, then vanished. Reporting
        # that as a short message would corrupt the stream silently.
        peer.close()
        with self.assertRaises(ConnectionClosedError) as caught:
            channel.receive_frame()

        self.assertIn("incomplete frame", str(caught.exception))

    def test_send_after_close_is_refused(self) -> None:
        channel, _ = self.connect()
        channel.close()

        with self.assertRaises(ConnectionClosedError):
            channel.send_frame(b"halo")

    def test_receive_after_close_is_refused(self) -> None:
        channel, _ = self.connect()
        channel.close()

        with self.assertRaises(ConnectionClosedError):
            channel.receive_frame()

    def test_close_is_idempotent(self) -> None:
        channel, _ = self.connect()

        channel.close()
        channel.close()

        self.assertTrue(channel.is_closed)

    def test_close_unblocks_a_reader_thread(self) -> None:
        channel, _ = self.connect()
        finished = threading.Event()
        raised: list[BaseException] = []

        def read() -> None:
            try:
                channel.receive_frame()
            except BaseException as exc:  # noqa: BLE001 - the failure is the assertion
                raised.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        self.assertFalse(finished.wait(timeout=0.2), "reader returned before close")

        channel.close()

        # shutdown() is what makes this return promptly; without it the reader
        # would sit on a dead descriptor until the peer's stack reset it.
        self.assertTrue(finished.wait(timeout=5), "reader did not wake after close")
        self.assertTrue(raised and isinstance(raised[0], ConnectionClosedError))

    def test_timeout_raises_transport_timeout_not_closed(self) -> None:
        channel, _ = self.connect()

        with self.assertRaises(TransportTimeout):
            channel.receive_frame(timeout=0.1)

        # A silence is not a disconnection: the session layer decides whether
        # to keep waiting or to declare the peer dead.
        self.assertFalse(channel.is_closed)

    def test_a_frame_arriving_after_a_timeout_is_still_delivered(self) -> None:
        channel, peer = self.connect()

        with self.assertRaises(TransportTimeout):
            channel.receive_frame(timeout=0.05)

        peer.sendall(encode_frame(b"late but fine"))
        self.assertEqual(channel.receive_frame(timeout=5), b"late but fine")


class TraceEmissionTests(LoopbackTestCase):
    """The L4 event is filed by the code that actually touches the socket."""

    def test_send_frame_files_an_outbound_transport_event(self) -> None:
        sink = CollectingTraceSink()
        channel, _ = self.connect(emitter=TraceEmitter(sink, node=Node.CLIENT))

        channel.send_frame(b"halo", trace_id="trace-1")

        self.assertEqual(len(sink.events), 1)
        event = sink.events[0]
        self.assertIs(event.layer, Layer.TRANSPORT)
        self.assertIs(event.direction, Direction.OUTBOUND)
        self.assertEqual(event.trace_id, "trace-1")
        self.assertEqual(event.size_bytes, LENGTH_PREFIX_SIZE + 4)

    def test_receive_frame_files_an_inbound_transport_event(self) -> None:
        sink = CollectingTraceSink()
        channel, peer = self.connect(emitter=TraceEmitter(sink, node=Node.CLIENT))
        peer.sendall(encode_frame(b"halo"))

        channel.receive_frame()

        event = sink.events[0]
        self.assertIs(event.layer, Layer.TRANSPORT)
        self.assertIs(event.direction, Direction.INBOUND)
        self.assertEqual(event.size_bytes, LENGTH_PREFIX_SIZE + 4)

    def test_transport_event_hex_covers_the_length_prefix(self) -> None:
        sink = CollectingTraceSink()
        channel, _ = self.connect(emitter=TraceEmitter(sink, node=Node.CLIENT))

        channel.send_frame(b"hi")

        # The prefix is part of what this layer put on the wire, so it belongs
        # in the layer's own hex view -- that is how the visualizer shows L4
        # adding 4 bytes that L5 did not produce.
        self.assertTrue(sink.events[0].payload_hex.startswith("00 00 00 02"))

    def test_no_events_are_filed_when_tracing_is_off(self) -> None:
        sink = CollectingTraceSink()
        channel, _ = self.connect(
            emitter=TraceEmitter(sink, node=Node.CLIENT, enabled=False)
        )

        channel.send_frame(b"halo")

        self.assertEqual(sink.events, [])

    def test_channel_works_without_an_emitter_at_all(self) -> None:
        channel, peer = self.connect()

        channel.send_frame(b"halo")
        peer.sendall(encode_frame(b"balik"))

        self.assertEqual(channel.receive_frame(), b"balik")


class AddressTests(LoopbackTestCase):
    """Address reporting, used in the trace summary and by the bridge."""

    def test_peer_and_local_names_are_host_port_pairs(self) -> None:
        channel, _ = self.connect()

        self.assertEqual(channel.peer_name, f"127.0.0.1:{self.port}")
        self.assertTrue(channel.local_name.startswith("127.0.0.1:"))

    def test_peer_name_survives_close(self) -> None:
        channel, _ = self.connect()

        channel.close()

        # Reporting "unknown" here would lose the one fact a disconnect log
        # needs most: which peer went away.
        self.assertIn("127.0.0.1", channel.peer_name)

    def test_max_frame_size_is_reported(self) -> None:
        channel, _ = self.connect(max_frame_size=4096)

        self.assertEqual(channel.max_frame_size, 4096)


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes, or raise if the peer stops early."""
    buffer = b""
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise AssertionError(f"peer closed after {len(buffer)} of {count} bytes")
        buffer += chunk
    return buffer


if __name__ == "__main__":
    unittest.main()
