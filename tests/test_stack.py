"""Cross-layer integration: the whole stack over a real loopback socket.

Every other test file exercises one layer against a stand-in for its neighbour.
This one wires the real things together -- an asyncio server driven by
:class:`AsyncTcpChannel` in a background event loop, and a blocking CLI client
driven by :class:`ClientSession` in the test thread -- and asserts on what
actually crossed the wire.

Two properties are only provable here:

* **Symmetry.** The server and the client run the *same* session and
  presentation code over two *different* transport implementations. If the
  framing, envelope layout or sequence rules had drifted between them, nothing
  else in the suite would notice; this does.
* **The layer chain.** One outbound message must file exactly one L7, L6, L5
  and L4 event, in that order, under a single trace id. That is the claim the
  web visualizer is built on, so it is asserted against a real socket rather
  than against a mocked transport.

The server here is a test fixture, not ``server/chat_server.py``: it implements
just enough of the protocol (handshake, echo) to exercise the stack. The
production server is tested by its own suite.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import unittest
import uuid

from presentation import MessageType, decode, encode, make_message
from session import (
    SESSION_HEADER_SIZE,
    UNASSIGNED_SESSION_ID,
    ClientSession,
    SessionCore,
    build_connect,
    build_connect_err,
    build_connect_ok,
    encode_envelope,
    parse_connect,
)
from trace import CollectingTraceSink, Direction, Layer, Node, TraceEmitter
from transport import AsyncTcpChannel, BlockingTcpChannel, ConnectionClosedError, encode_frame
from transport.framing import LENGTH_PREFIX_SIZE


class EchoServer:
    """A minimal async peer: handshake, then echo every BROADCAST back."""

    def __init__(self, sink: CollectingTraceSink | None = None) -> None:
        self.sink = sink
        self.received: list[dict] = []
        self.errors: list[BaseException] = []
        self.handshake_done = threading.Event()
        self.disconnected = threading.Event()

    def _emitter(self) -> TraceEmitter | None:
        if self.sink is None:
            return None
        return TraceEmitter(self.sink, node=Node.SERVER)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client until it disconnects."""
        channel = AsyncTcpChannel(reader, writer, emitter=self._emitter())
        core = SessionCore(emitter=self._emitter())
        try:
            await self._handshake(channel, core)
            self.handshake_done.set()
            await self._serve(channel, core)
        except ConnectionClosedError:
            # A client going away is the ordinary end of a chat connection.
            pass
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertions
            self.errors.append(exc)
        finally:
            await channel.close()
            self.disconnected.set()

    async def _handshake(self, channel: AsyncTcpChannel, core: SessionCore) -> None:
        """Answer CONNECT with CONNECT_OK, or CONNECT_ERR for a reserved nick."""
        frame = await channel.receive_frame()
        _, message = core.parse_inbound(frame)
        nickname, _version = parse_connect(message)

        if nickname == "taken":
            _, payload = core.build_outbound(
                build_connect_err("NICK_TAKEN", "nickname is already in use")
            )
            await channel.send_frame(payload)
            return

        core.open(str(uuid.uuid4()))
        _, payload = core.build_outbound(build_connect_ok(core.session_id, nickname))
        await channel.send_frame(payload)

    async def _serve(self, channel: AsyncTcpChannel, core: SessionCore) -> None:
        """Echo each inbound message back to the client."""
        while True:
            frame = await channel.receive_frame()
            _, message = core.parse_inbound(frame)
            self.received.append(message)

            if message["type"] is MessageType.DISCONNECT:
                return

            _, payload = core.build_outbound(
                make_message(
                    MessageType.BROADCAST,
                    {"text": f"echo: {message['payload'].get('text', '')}"},
                    sender="server",
                )
            )
            await channel.send_frame(payload)


class StackTestCase(unittest.TestCase):
    """Runs a private event loop, and therefore a real server, per test."""

    def setUp(self) -> None:
        self.server = EchoServer()
        self.client_sink = CollectingTraceSink()
        self.client: ClientSession | None = None
        self._raw: socket.socket | None = None
        self._handlers: set[asyncio.Task] = set()

        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._listener, self.port = asyncio.run_coroutine_threadsafe(
            self._listen(), self.loop
        ).result(timeout=5)

    def tearDown(self) -> None:
        if self.client is not None:
            self.client.close()
        if self._raw is not None:
            self._raw.close()
        asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()

    # -- harness ----------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _listen(self):
        listener = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
        return listener, listener.sockets[0].getsockname()[1]

    def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # The handler is tracked so teardown can cancel it: a handler blocked in
        # receive_frame would otherwise keep the loop alive forever.
        task = self.loop.create_task(self.server.handle(reader, writer))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _shutdown(self) -> None:
        # Order matters: from Python 3.13 ``Server.wait_closed`` also waits for
        # every live connection handler to finish, so awaiting it first would
        # hang forever on a handler parked in ``receive_frame``.
        for task in list(self._handlers):
            task.cancel()
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)
        self._listener.close()
        await self._listener.wait_closed()

    # -- client helpers ---------------------------------------------------

    def connect(self, nickname: str = "budi", *, trace: bool = True) -> ClientSession:
        """Open a handshaken client session against the test server."""
        emitter = TraceEmitter(self.client_sink, node=Node.CLIENT) if trace else None
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port, emitter=emitter)
        self.client = ClientSession(channel, nickname=nickname)
        self.client.connect()
        return self.client

    def raw_socket(self) -> socket.socket:
        """A bare socket, for tests that must control the exact bytes written."""
        self._raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        return self._raw


class HandshakeTests(StackTestCase):
    """CONNECT/CONNECT_OK across two different channel implementations."""

    def test_handshake_assigns_a_session_id_the_client_then_carries(self) -> None:
        client = self.connect()

        self.assertTrue(client.core.is_active)
        # A real UUID, not the pre-handshake placeholder.
        self.assertEqual(len(client.session_id), 36)
        self.assertNotEqual(client.session_id, UNASSIGNED_SESSION_ID)

    def test_server_sequence_starts_at_one_with_connect_ok(self) -> None:
        client = self.connect()

        # CONNECT was outbound 1 on the client; CONNECT_OK was inbound 1 here.
        self.assertEqual(client.core.last_inbound_sequence, 1)
        self.assertEqual(client.core.next_sequence, 2)

    def test_rejected_handshake_surfaces_the_server_error_code(self) -> None:
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port)
        client = ClientSession(channel, nickname="taken")
        self.client = client

        with self.assertRaises(Exception) as caught:
            client.connect()

        self.assertIn("NICK_TAKEN", str(caught.exception))
        self.assertFalse(client.core.is_active)

    def test_handshake_split_into_three_byte_writes_is_reassembled(self) -> None:
        sock = self.raw_socket()
        frame = encode_frame(_connect_frame("budi"))

        # Splits the length prefix itself, then the session header, then the
        # JSON -- the case a newline-delimited protocol cannot tell apart from
        # a real message.
        for offset in range(0, len(frame), 3):
            sock.sendall(frame[offset : offset + 3])

        message = decode(_recv_frame(sock)[SESSION_HEADER_SIZE:])

        self.assertIs(message["type"], MessageType.CONNECT_OK)
        self.assertEqual(message["payload"]["nick"], "budi")
        self.assertTrue(self.server.handshake_done.wait(timeout=5))
        self.assertEqual(self.server.errors, [])

    def test_two_frames_glued_into_one_write_are_both_read(self) -> None:
        sock = self.raw_socket()
        sock.sendall(encode_frame(_connect_frame("budi")) + encode_frame(_broadcast_frame("nempel")))

        _recv_frame(sock)  # CONNECT_OK
        message = decode(_recv_frame(sock)[SESSION_HEADER_SIZE:])

        self.assertEqual(message["payload"]["text"], "echo: nempel")


class MessagingTests(StackTestCase):
    """Chat traffic survives the full L7 -> L4 -> L7 round trip."""

    def test_broadcast_round_trips_through_the_server(self) -> None:
        client = self.connect()

        client.send(make_message(MessageType.BROADCAST, {"text": "halo semua"}, sender="budi"))
        _, reply = client.receive(timeout=5)

        self.assertIs(reply["type"], MessageType.BROADCAST)
        self.assertEqual(reply["payload"]["text"], "echo: halo semua")

    def test_non_ascii_text_survives_the_round_trip(self) -> None:
        client = self.connect()

        client.send(make_message(MessageType.BROADCAST, {"text": "halo 日本語 🎉"}, sender="budi"))
        _, reply = client.receive(timeout=5)

        # UTF-8 end to end: an accidental ASCII-escape or locale codec on either
        # side shows up here and nowhere else.
        self.assertEqual(reply["payload"]["text"], "echo: halo 日本語 🎉")

    def test_many_messages_stay_ordered_and_sequenced(self) -> None:
        client = self.connect()

        for index in range(25):
            client.send(
                make_message(MessageType.BROADCAST, {"text": f"pesan-{index}"}, sender="budi")
            )
        replies = [client.receive(timeout=5)[1] for _ in range(25)]

        self.assertEqual(
            [reply["payload"]["text"] for reply in replies],
            [f"echo: pesan-{index}" for index in range(25)],
        )
        # 1 CONNECT + 25 broadcasts, and the server echoed each one in order.
        self.assertEqual(client.core.next_sequence, 27)
        self.assertEqual(client.core.inbound_anomalies, 0)

    def test_maximum_length_message_is_delivered_intact(self) -> None:
        client = self.connect()
        body = "x" * 4096

        client.send(make_message(MessageType.BROADCAST, {"text": body}, sender="budi"))
        _, reply = client.receive(timeout=5)

        self.assertEqual(reply["payload"]["text"], f"echo: {body}")

    def test_server_receives_the_sender_and_text_the_client_sent(self) -> None:
        client = self.connect()

        client.send(make_message(MessageType.BROADCAST, {"text": "hai"}, sender="budi"))
        client.receive(timeout=5)

        self.assertEqual(self.server.received[0]["sender"], "budi")
        self.assertEqual(self.server.received[0]["payload"]["text"], "hai")


class DisconnectionTests(StackTestCase):
    """How each side reports the other vanishing."""

    def test_abrupt_client_death_is_not_a_server_error(self) -> None:
        client = self.connect()
        client.send(make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi"))
        client.receive(timeout=5)

        # No DISCONNECT: the socket dies under the server, which is what a
        # crashed or unplugged client looks like.
        client.channel.socket.close()

        self.assertTrue(self.server.disconnected.wait(timeout=5), "server did not notice")
        self.assertEqual(self.server.errors, [])

    def test_client_sees_a_connection_closed_when_the_server_stops(self) -> None:
        client = self.connect()
        client.send(make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi"))
        client.receive(timeout=5)

        asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(timeout=5)

        with self.assertRaises(ConnectionClosedError):
            client.receive(timeout=5)

    def test_graceful_close_sends_disconnect_then_stops(self) -> None:
        client = self.connect()

        client.close()

        self.assertTrue(self.server.disconnected.wait(timeout=5))
        self.assertEqual(self.server.received[-1]["type"], MessageType.DISCONNECT)
        self.assertTrue(client.channel.is_closed)

    def test_a_second_client_connects_after_the_first_leaves(self) -> None:
        first = self.connect("budi")
        first.close()

        second = self.connect("siti")

        self.assertTrue(second.core.is_active)
        second.send(make_message(MessageType.BROADCAST, {"text": "masih hidup"}, sender="siti"))
        _, reply = second.receive(timeout=5)
        self.assertEqual(reply["payload"]["text"], "echo: masih hidup")


class TraceChainTests(StackTestCase):
    """The claim the visualizer depends on: L7 -> L6 -> L5 -> L4, in order."""

    def test_one_outbound_message_files_four_events_in_layer_order(self) -> None:
        client = self.connect()
        self.client_sink.clear()

        trace_id = client.send(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )
        events = self.client_sink.for_trace(trace_id)

        self.assertEqual(
            [int(event.layer) for event in events],
            [7, 6, 5, 4],
            "expected Application, Presentation, Session, Transport in that order",
        )
        self.assertEqual(
            [event.direction for event in events],
            [Direction.OUTBOUND] * 4,
            "all four events describe the same outbound message",
        )

    def test_each_layer_reports_the_bytes_it_actually_handled(self) -> None:
        client = self.connect()
        self.client_sink.clear()

        trace_id = client.send(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )
        sizes = [event.size_bytes for event in self.client_sink.for_trace(trace_id)]

        # L6 re-renders the message, so its size may differ from L7's dict
        # rendering; L5 and L4 each prepend their own fixed-size header.
        self.assertEqual(sizes[2] - sizes[1], SESSION_HEADER_SIZE)
        self.assertEqual(sizes[3] - sizes[2], LENGTH_PREFIX_SIZE)

    def test_no_layer_below_four_is_ever_reported(self) -> None:
        client = self.connect()
        client.send(make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi"))
        client.receive(timeout=5)

        self.assertTrue(
            all(event.layer >= Layer.TRANSPORT for event in self.client_sink.events),
            "L3-L1 belong to the OS; faking them in our trace would be a lie",
        )

    def test_inbound_reply_gets_its_own_trace_id(self) -> None:
        client = self.connect()
        self.client_sink.clear()

        outbound_id = client.send(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )
        client.receive(timeout=5)

        inbound = [e for e in self.client_sink.events if e.direction is Direction.INBOUND]
        self.assertTrue(inbound)
        # The trace id is not on the wire, so an inbound PDU cannot be linked to
        # the outbound one; reusing the id would fabricate a correlation.
        self.assertTrue(all(event.trace_id != outbound_id for event in inbound))

    def test_session_inherits_the_emitter_already_configured_on_the_channel(self) -> None:
        client = self.connect(trace=True)
        self.client_sink.clear()

        trace_id = client.send(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )

        # The client was built without an explicit emitter, so if it did not
        # inherit the channel's, only the L4 event would exist -- a trace that
        # looks complete while silently missing every layer above transport.
        self.assertEqual(len(self.client_sink.for_trace(trace_id)), 4)

    def test_tracing_off_still_sends_and_receives_normally(self) -> None:
        client = self.connect(trace=False)

        self.assertIsNone(
            client.send(make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi"))
        )
        _, reply = client.receive(timeout=5)

        self.assertEqual(reply["payload"]["text"], "echo: halo")
        self.assertEqual(self.client_sink.events, [])


def _envelope(message: dict, *, sequence: int) -> bytes:
    """Wrap a message the way ``SessionCore`` would, for hand-built frames."""
    return encode_envelope(encode(message), session_id=UNASSIGNED_SESSION_ID, sequence=sequence)


def _connect_frame(nickname: str) -> bytes:
    """A CONNECT exactly as ``ClientSession`` emits it: unassigned id, seq 1."""
    return _envelope(build_connect(nickname), sequence=1)


def _broadcast_frame(text: str) -> bytes:
    """A BROADCAST from a client that has just connected (seq 2)."""
    return _envelope(make_message(MessageType.BROADCAST, {"text": text}, sender="budi"), sequence=2)


def _recv_frame(sock: socket.socket) -> bytes:
    """Read one complete length-prefixed frame and return its payload."""
    size = int.from_bytes(_recv_exactly(sock, LENGTH_PREFIX_SIZE), "big")
    return _recv_exactly(sock, size)


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes, or fail if the peer stops early."""
    buffer = b""
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise AssertionError(f"peer closed after {len(buffer)} of {count} bytes")
        buffer += chunk
    return buffer


if __name__ == "__main__":
    unittest.main()
