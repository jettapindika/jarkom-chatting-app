"""The production server, driven over a real loopback socket.

``test_stack.py`` proves the layers fit together, but it does so against an echo
fixture. This file exercises :class:`ChatServer` itself -- the roster, the
broadcast fan-out, the per-connection send queue, the error codes, and the
shutdown path -- with a real ``BlockingTcpChannel`` client on the other end.

The clients here are real blocking sockets rather than mocked channels on
purpose. The bugs this file is meant to catch live in the interaction between
the server's writer tasks and the kernel's buffers: a farewell queued after the
socket closed, a broadcast that never drains, a disconnect nobody notices. None
of those are visible through a fake channel.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import socket
import threading
import time
import unittest

from presentation import MessageType, SchemaViolation, encode, make_message
from server import ChatServer, ServerConfig
from session import (
    UNASSIGNED_SESSION_ID,
    ClientSession,
    build_connect,
    encode_envelope,
)
from transport import BlockingTcpChannel, encode_frame


class ServerTestCase(unittest.TestCase):
    """A real ``ChatServer`` on a private event loop, one per test."""

    def setUp(self) -> None:
        self.server = ChatServer(ServerConfig(host="127.0.0.1", port=0, handshake_timeout=5.0))
        self.clients: list[ClientSession] = []
        self._raw: socket.socket | None = None

        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._call(self.server.start())

    def tearDown(self) -> None:
        for client in self.clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - teardown must not mask a failure
                pass
        if self._raw is not None:
            self._raw.close()
        self._call(self.server.shutdown())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()

    # -- harness ----------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call(self, coroutine):
        """Run ``coroutine`` on the server's loop and block for its result."""
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=10)

    @property
    def port(self) -> int:
        return self.server.port

    def connect(self, nickname: str = "budi", *, drain: bool = True) -> ClientSession:
        """Open a handshaken client, optionally consuming CONNECT_OK's roster."""
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port)
        session = ClientSession(channel, nickname=nickname)
        session.connect()
        self.clients.append(session)
        if drain:
            session.receive(timeout=5)  # the USER_LIST sent with CONNECT_OK
        return session

    def raw_socket(self) -> socket.socket:
        """A bare socket, for tests that must control the exact bytes written."""
        self._raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        return self._raw

    def next_of_type(self, session: ClientSession, message_type: MessageType, *, timeout: float = 5.0):
        """Read frames until one of ``message_type`` arrives, or fail the test.

        Other users' traffic interleaves with this client's, so a test that
        asserted on the very next frame would be asserting on scheduling order
        rather than on behaviour.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(f"no {message_type.value} arrived within {timeout}s")
            message = session.receive(timeout=remaining)[1]
            if message["type"] is message_type:
                return message


class HandshakeTests(ServerTestCase):
    """CONNECT, and every way it can be refused."""

    def test_connect_ok_carries_the_nickname_and_a_session_id(self) -> None:
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port)
        client = ClientSession(channel, nickname="budi")
        self.clients.append(client)

        reply = client.connect()

        self.assertEqual(reply["payload"]["nick"], "budi")
        self.assertEqual(reply["payload"]["session_id"], client.session_id)
        self.assertEqual(len(client.session_id), 36)

    def test_roster_arrives_right_after_connect_ok(self) -> None:
        # ``connect()`` consumes CONNECT_OK itself, so the roster is the very
        # next frame on the wire.
        client = self.connect("budi", drain=False)

        roster = client.receive(timeout=5)[1]

        self.assertIs(roster["type"], MessageType.USER_LIST)
        self.assertEqual([entry["nick"] for entry in roster["payload"]["users"]], ["budi"])

    def test_duplicate_nickname_is_refused_with_nick_taken(self) -> None:
        self.connect("budi")
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port)
        second = ClientSession(channel, nickname="budi")
        self.clients.append(second)

        with self.assertRaises(Exception) as caught:
            second.connect()

        self.assertIn("NICK_TAKEN", str(caught.exception))
        # The original holder is untouched by the failed claim.
        self.assertEqual(self.server.client_count, 1)

    def test_second_client_is_told_about_the_first(self) -> None:
        self.connect("budi")
        sari = self.connect("sari", drain=False)

        roster = sari.receive(timeout=5)[1]

        self.assertIs(roster["type"], MessageType.USER_LIST)
        self.assertEqual([entry["nick"] for entry in roster["payload"]["users"]], ["budi", "sari"])

    def test_existing_client_is_told_when_another_joins(self) -> None:
        budi = self.connect("budi")

        self.connect("sari")

        self.assertEqual(self.next_of_type(budi, MessageType.USER_JOIN)["payload"]["nick"], "sari")

    def test_first_frame_that_is_not_connect_is_refused(self) -> None:
        sock = self.raw_socket()
        sock.sendall(_frame(encode(make_message(MessageType.BROADCAST, {"text": "hi"})), sequence=1))

        message = _decode(_recv_frame(sock))

        self.assertIs(message["type"], MessageType.CONNECT_ERR)
        self.assertEqual(message["payload"]["code"], "UNEXPECTED_TYPE")

    def test_garbage_first_frame_is_refused_not_crashed_on(self) -> None:
        sock = self.raw_socket()
        sock.sendall(_frame(b"not json at all", sequence=1))

        message = _decode(_recv_frame(sock))

        self.assertIs(message["type"], MessageType.CONNECT_ERR)
        self.assertEqual(message["payload"]["code"], "MALFORMED")
        # The server is still serving.
        self.assertEqual(self.connect("budi").nickname, "budi")

    def test_invalid_nickname_is_refused_before_it_reaches_the_wire(self) -> None:
        channel = BlockingTcpChannel.connect("127.0.0.1", self.port)
        client = ClientSession(channel, nickname="budi sari")
        self.clients.append(client)

        # The nickname is validated locally, so the user is told immediately
        # instead of after a round trip -- and nothing is sent.
        with self.assertRaises(SchemaViolation):
            client.connect()

    def test_server_also_rejects_an_invalid_nickname_sent_by_a_raw_client(self) -> None:
        sock = self.raw_socket()
        sock.sendall(
            _frame(
                encode(make_message(MessageType.CONNECT, {"nick": "budi sari", "version": 1})),
                sequence=1,
            )
        )

        message = _decode(_recv_frame(sock))

        # Whitespace is not a legal display name. The client-side check is a
        # convenience; this is the enforcement point.
        self.assertIs(message["type"], MessageType.CONNECT_ERR)
        self.assertEqual(message["payload"]["code"], "NICK_INVALID")


class MessagingTests(ServerTestCase):
    """Broadcast, private, and the roster request."""

    def test_broadcast_reaches_every_connected_client_including_the_sender(self) -> None:
        budi = self.connect("budi")
        sari = self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        budi.send(make_message(MessageType.BROADCAST, {"text": "halo semua"}, sender="budi"))

        for client in (budi, sari):
            message = self.next_of_type(client, MessageType.BROADCAST)
            self.assertEqual(message["payload"]["text"], "halo semua")
            self.assertEqual(message["sender"], "budi")

    def test_server_overwrites_a_forged_sender(self) -> None:
        budi = self.connect("budi")

        budi.send(make_message(MessageType.BROADCAST, {"text": "palsu"}, sender="admin"))

        # The nickname comes from the handshake, never from the frame: a client
        # cannot speak as somebody else by filling in the field.
        self.assertEqual(self.next_of_type(budi, MessageType.BROADCAST)["sender"], "budi")

    def test_non_ascii_text_survives_the_round_trip(self) -> None:
        budi = self.connect("budi")

        budi.send(make_message(MessageType.BROADCAST, {"text": "halo, apa kabar?"}, sender="budi"))

        message = self.next_of_type(budi, MessageType.BROADCAST)
        self.assertEqual(message["payload"]["text"], "halo, apa kabar?")

    def test_private_message_carries_to_for_the_sender_only(self) -> None:
        budi = self.connect("budi")
        sari = self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        sari.send(make_message(MessageType.PRIVATE, {"to": "budi", "text": "psst"}, sender="sari"))

        outbound = self.next_of_type(sari, MessageType.PRIVATE)
        inbound = self.next_of_type(budi, MessageType.PRIVATE)

        # ``to`` is what lets a client render its own copy as outgoing; the
        # recipient's copy deliberately lacks it.
        self.assertEqual(outbound["payload"], {"text": "psst", "to": "budi"})
        self.assertEqual(inbound["payload"], {"text": "psst"})

    def test_private_message_to_an_unknown_user_is_an_error_not_a_drop(self) -> None:
        sari = self.connect("sari")

        sari.send(make_message(MessageType.PRIVATE, {"to": "hantu", "text": "hi"}, sender="sari"))

        error = self.next_of_type(sari, MessageType.ERROR)
        self.assertEqual(error["payload"]["code"], "NO_SUCH_USER")
        self.assertEqual(error["payload"]["field"], "to")

    def test_private_message_to_self_is_delivered_once(self) -> None:
        budi = self.connect("budi")

        budi.send(make_message(MessageType.PRIVATE, {"to": "budi", "text": "catatan"}, sender="budi"))

        message = self.next_of_type(budi, MessageType.PRIVATE)
        self.assertEqual(message["payload"]["text"], "catatan")

    def test_user_list_reflects_the_current_roster(self) -> None:
        budi = self.connect("budi")
        self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        budi.send(make_message(MessageType.USER_LIST, {}, sender="budi"))

        roster = self.next_of_type(budi, MessageType.USER_LIST)
        self.assertEqual([entry["nick"] for entry in roster["payload"]["users"]], ["budi", "sari"])


class RenameTests(ServerTestCase):
    """``/nick`` after the handshake."""

    def test_rename_acknowledges_and_announces_to_others(self) -> None:
        budi = self.connect("budi")
        sari = self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        sari.send(make_message(MessageType.NICK, {"nick": "sari2"}, sender="sari"))

        self.assertEqual(self.next_of_type(sari, MessageType.NICK_OK)["payload"]["nick"], "sari2")
        # Announced as a leave/join pair, so a client that only understands the
        # ten original message types still renders it correctly.
        self.assertEqual(self.next_of_type(budi, MessageType.USER_LEAVE)["payload"]["nick"], "sari")
        self.assertEqual(self.next_of_type(budi, MessageType.USER_JOIN)["payload"]["nick"], "sari2")

    def test_rename_to_a_taken_nickname_is_refused(self) -> None:
        self.connect("budi")
        sari = self.connect("sari")

        sari.send(make_message(MessageType.NICK, {"nick": "budi"}, sender="sari"))

        error = self.next_of_type(sari, MessageType.ERROR)
        self.assertEqual(error["payload"]["code"], "NICK_TAKEN")
        self.assertEqual(sari.nickname, "sari")

    def test_a_renamed_nickname_blocks_a_later_handshake(self) -> None:
        # The old index entry has to move with the user, or a second connection
        # could claim the freshly renamed nickname and two live sessions would
        # answer to it.
        sari = self.connect("sari")
        sari.send(make_message(MessageType.NICK, {"nick": "sari2"}, sender="sari"))
        self.next_of_type(sari, MessageType.NICK_OK)

        channel = BlockingTcpChannel.connect("127.0.0.1", self.port)
        second = ClientSession(channel, nickname="sari2")
        self.clients.append(second)

        with self.assertRaises(Exception) as caught:
            second.connect()

        self.assertIn("NICK_TAKEN", str(caught.exception))
        self.assertEqual(self.server.client_count, 1)
        # The rename moved the reservation: the new name is held, the old one free.
        self.assertIn("sari2", self.server.registry)
        self.assertNotIn("sari", self.server.registry)

    def test_rename_keeps_the_session_alive(self) -> None:
        sari = self.connect("sari")

        sari.send(make_message(MessageType.NICK, {"nick": "sari2"}, sender="sari"))
        self.next_of_type(sari, MessageType.NICK_OK)

        # Same session id and sequence space: this is a display-name change, not
        # a second handshake.
        self.assertTrue(sari.core.is_active)
        sari.send(make_message(MessageType.BROADCAST, {"text": "masih hidup"}, sender="sari2"))
        self.assertEqual(self.next_of_type(sari, MessageType.BROADCAST)["sender"], "sari2")


class DisconnectionTests(ServerTestCase):
    """How each kind of departure is reported to the survivors."""

    def test_graceful_disconnect_announces_a_leave(self) -> None:
        budi = self.connect("budi")
        sari = self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        sari.close()

        self.assertEqual(self.next_of_type(budi, MessageType.USER_LEAVE)["payload"]["nick"], "sari")

    def test_abrupt_disconnect_announces_a_leave(self) -> None:
        budi = self.connect("budi")
        sock = self.raw_socket()
        sock.sendall(_frame(encode(build_connect("tumbal")), sequence=1))
        self.assertEqual(self.next_of_type(budi, MessageType.USER_JOIN)["payload"]["nick"], "tumbal")

        # No DISCONNECT, no shutdown: the socket is simply dropped, which is
        # what a crashed client or a pulled cable looks like.
        sock.close()
        self._raw = None

        self.assertEqual(self.next_of_type(budi, MessageType.USER_LEAVE)["payload"]["nick"], "tumbal")
        self.assertEqual(self.server.client_count, 1)

    def test_a_disconnect_frame_ends_the_connection_quietly(self) -> None:
        budi = self.connect("budi")
        sari = self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        sari.send(make_message(MessageType.DISCONNECT, {}, sender="sari"))

        self.assertEqual(self.next_of_type(budi, MessageType.USER_LEAVE)["payload"]["nick"], "sari")

    def test_a_frame_with_the_wrong_session_id_is_refused(self) -> None:
        sock = self.raw_socket()
        sock.sendall(_frame(encode(build_connect("tumbal")), sequence=1))
        _recv_frame(sock)  # CONNECT_OK
        _recv_frame(sock)  # USER_LIST

        # A well-formed envelope, but carrying the pre-handshake placeholder
        # instead of the id the server handed out. The session header is what
        # binds a PDU to a session, so this must not be treated as traffic from
        # the authenticated user.
        sock.sendall(
            _frame(
                encode(make_message(MessageType.BROADCAST, {"text": "menyusup"}, sender="tumbal")),
                sequence=2,
            )
        )

        message = _decode(_recv_frame(sock))
        self.assertIs(message["type"], MessageType.ERROR)
        self.assertEqual(message["payload"]["code"], "MALFORMED")

    def test_malformed_frames_are_budgeted_then_close_the_connection(self) -> None:
        sock = self.raw_socket()
        sock.sendall(_frame(encode(build_connect("tumbal")), sequence=1))
        _recv_frame(sock)  # CONNECT_OK
        _recv_frame(sock)  # USER_LIST

        # The session id the server assigned is required from here on, so a
        # frame carrying the placeholder is malformed but still framed: one bad
        # frame is recoverable, three is a peer that is not speaking this
        # protocol.
        for sequence in (2, 3, 4):
            sock.sendall(_frame(b"{}", sequence=sequence))
            message = _decode(_recv_frame(sock))
            self.assertIs(message["type"], MessageType.ERROR)
            self.assertEqual(message["payload"]["code"], "MALFORMED")

        # The last one closes the connection.
        sock.settimeout(5)
        self.assertEqual(sock.recv(1), b"")


class ShutdownTests(ServerTestCase):
    """The graceful path: everyone is told, nothing hangs."""

    def test_shutdown_tells_every_client_and_completes_promptly(self) -> None:
        budi = self.connect("budi")
        sari = self.connect("sari")
        self.next_of_type(budi, MessageType.USER_JOIN)

        started = time.monotonic()
        self._call(self.server.shutdown())
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, self.server.config.shutdown_timeout + 1.0)
        self.assertIs(self.next_of_type(budi, MessageType.DISCONNECT)["type"], MessageType.DISCONNECT)
        self.assertIs(self.next_of_type(sari, MessageType.DISCONNECT)["type"], MessageType.DISCONNECT)

    def test_shutdown_reaches_clients_after_the_accept_loop_is_cancelled(self) -> None:
        """The Ctrl+C path: the accept task is cancelled, *then* shutdown runs.

        ``server.main`` stops accepting by cancelling the serve task, and from
        Python 3.14 cancelling ``asyncio.Server.serve_forever`` closes every
        accepted client transport on its way out. The farewell queued
        afterwards then has nowhere to go and the client sees a bare reset
        instead of a DISCONNECT -- the difference between a server saying
        goodbye and one looking like it crashed.
        """
        serve = asyncio.run_coroutine_threadsafe(self.server.serve_forever(), self.loop)
        budi = self.connect("budi")

        self.loop.call_soon_threadsafe(serve.cancel)
        with self.assertRaises(concurrent.futures.CancelledError):
            serve.result(timeout=5)

        self._call(self.server.shutdown())

        self.assertIs(
            self.next_of_type(budi, MessageType.DISCONNECT)["type"],
            MessageType.DISCONNECT,
        )

    def test_shutdown_clears_the_roster_and_the_connections(self) -> None:
        self.connect("budi")
        self.connect("sari")

        self._call(self.server.shutdown())

        self.assertEqual(self.server.client_count, 0)
        self.assertEqual(len(self.server.connections), 0)

    def test_shutdown_with_no_clients_completes(self) -> None:
        self._call(self.server.shutdown())

        self.assertEqual(self.server.client_count, 0)

    def test_port_is_the_bound_port_not_the_configured_one(self) -> None:
        # port=0 asks the kernel to choose; ``port`` must report what it chose,
        # or every client would connect to the wrong place.
        self.assertNotEqual(self.port, 0)
        self.assertGreater(self.port, 0)


def _frame(payload: bytes, *, sequence: int) -> bytes:
    """Frame ``payload`` the way a client would, with the pre-handshake id."""
    return encode_frame(
        encode_envelope(payload, session_id=UNASSIGNED_SESSION_ID, sequence=sequence)
    )


def _recv_frame(sock: socket.socket) -> bytes:
    """Read one complete length-prefixed frame and return its payload."""
    size = int.from_bytes(_recv_exactly(sock, 4), "big")
    return _recv_exactly(sock, size)


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes, or fail if the peer stops early."""
    buffer = bytearray()
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise AssertionError(f"connection closed after {len(buffer)} of {count} bytes")
        buffer.extend(chunk)
    return bytes(buffer)


def _decode(payload: bytes) -> dict:
    """Strip the session header and decode the JSON envelope."""
    from session import SESSION_HEADER_SIZE

    from presentation import decode

    return decode(payload[SESSION_HEADER_SIZE:])


if __name__ == "__main__":
    unittest.main()
