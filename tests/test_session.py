"""Tests for L5: the binary session header, sequencing and session lifecycle.

The session header is the layer's whole reason for existing, so most of these
tests are about the exact byte layout: 16 bytes of session id plus 8 bytes of
big-endian sequence, in front of the JSON payload.
"""

from __future__ import annotations

import json
import unittest
import uuid

from presentation import MessageType, make_message
from session import (
    SESSION_HEADER_SIZE,
    UNASSIGNED_SESSION_ID,
    SessionCore,
    SessionEnvelopeError,
    SessionHeader,
    SessionState,
    SessionStateError,
    decode_envelope,
    encode_envelope,
)
from trace import CollectingTraceSink, Layer, Node, TraceEmitter


class EnvelopeLayoutTests(unittest.TestCase):
    """Byte-level layout of the L5 header."""

    def test_header_is_exactly_twenty_four_bytes(self) -> None:
        payload = b'{"type":"PING"}'
        envelope = encode_envelope(payload, session_id=UNASSIGNED_SESSION_ID, sequence=1)

        self.assertEqual(len(envelope), SESSION_HEADER_SIZE + len(payload))

    def test_session_id_occupies_the_first_sixteen_bytes(self) -> None:
        session_id = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        envelope = encode_envelope(b"x", session_id=session_id, sequence=1)

        self.assertEqual(envelope[:16], uuid.UUID(session_id).bytes)

    def test_sequence_is_eight_byte_big_endian(self) -> None:
        envelope = encode_envelope(b"x", session_id=UNASSIGNED_SESSION_ID, sequence=1)

        self.assertEqual(envelope[16:24], b"\x00\x00\x00\x00\x00\x00\x00\x01")

    def test_json_payload_follows_the_twenty_four_byte_session_header(self) -> None:
        envelope = encode_envelope(b'{"a":1}', session_id=UNASSIGNED_SESSION_ID, sequence=9)

        # 16 bytes session id + 8 bytes sequence, then L6's JSON. On the wire
        # this becomes offset 28, because L4 prepends a 4-byte length prefix --
        # see tests/test_channel.py for that end-to-end assertion.
        self.assertEqual(envelope[SESSION_HEADER_SIZE:], b'{"a":1}')
        self.assertEqual(len(envelope) - len(b'{"a":1}'), SESSION_HEADER_SIZE)

    def test_round_trip_preserves_id_sequence_and_payload(self) -> None:
        session_id = str(uuid.uuid4())
        payload = json.dumps({"type": "BROADCAST"}, separators=(",", ":")).encode()

        header, recovered = decode_envelope(
            encode_envelope(payload, session_id=session_id, sequence=42)
        )

        self.assertEqual(header.session_id, session_id)
        self.assertEqual(header.sequence, 42)
        self.assertEqual(recovered, payload)

    def test_empty_payload_round_trips(self) -> None:
        header, payload = decode_envelope(
            encode_envelope(b"", session_id=UNASSIGNED_SESSION_ID, sequence=0)
        )

        self.assertEqual(payload, b"")
        self.assertEqual(header.sequence, 0)

    def test_maximum_sequence_value_round_trips(self) -> None:
        maximum = (1 << 64) - 1
        header, _ = decode_envelope(
            encode_envelope(b"", session_id=UNASSIGNED_SESSION_ID, sequence=maximum)
        )

        self.assertEqual(header.sequence, maximum)

    def test_sequence_above_uint64_is_refused(self) -> None:
        with self.assertRaises(SessionEnvelopeError):
            encode_envelope(b"", session_id=UNASSIGNED_SESSION_ID, sequence=1 << 64)

    def test_negative_sequence_is_refused(self) -> None:
        with self.assertRaises(SessionEnvelopeError):
            encode_envelope(b"", session_id=UNASSIGNED_SESSION_ID, sequence=-1)

    def test_malformed_session_id_is_refused(self) -> None:
        for session_id in ("not-a-uuid", "", "3f2504e0"):
            with self.subTest(session_id=session_id), self.assertRaises(SessionEnvelopeError):
                encode_envelope(b"", session_id=session_id, sequence=0)

    def test_decode_rejects_a_payload_shorter_than_the_header(self) -> None:
        for length in (0, 1, 23):
            with self.subTest(length=length), self.assertRaises(SessionEnvelopeError):
                decode_envelope(b"\x00" * length)

    def test_unassigned_id_is_all_zeroes(self) -> None:
        self.assertEqual(UNASSIGNED_SESSION_ID, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            encode_envelope(b"", session_id=UNASSIGNED_SESSION_ID, sequence=0)[:16], b"\x00" * 16
        )


class SessionStateTests(unittest.TestCase):
    """The lifecycle machine, including the transitions that must be refused."""

    def test_lifecycle_connect_open_close(self) -> None:
        core = SessionCore()
        self.assertIs(core.state, SessionState.CONNECTING)
        self.assertFalse(core.is_active)

        core.open("3f2504e0-4f89-11d3-9a0c-0305e82c3301")

        self.assertIs(core.state, SessionState.ACTIVE)
        self.assertTrue(core.is_active)
        self.assertEqual(core.session_id, "3f2504e0-4f89-11d3-9a0c-0305e82c3301")

        core.close()
        self.assertIs(core.state, SessionState.CLOSED)

    def test_cannot_open_twice(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)

        with self.assertRaises(SessionStateError):
            core.open(UNASSIGNED_SESSION_ID)

    def test_cannot_send_chat_traffic_before_the_handshake(self) -> None:
        core = SessionCore()

        with self.assertRaises(SessionStateError):
            core.require_active("send chat traffic")

    def test_cannot_send_on_a_closed_session(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)
        core.close()

        with self.assertRaises(SessionStateError):
            core.build_outbound(make_message(MessageType.PING))

    def test_close_is_idempotent(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)

        core.close()
        core.close()

        self.assertIs(core.state, SessionState.CLOSED)

    def test_begin_closing_moves_active_to_closing(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)

        core.begin_closing()

        self.assertIs(core.state, SessionState.CLOSING)


class SequencingTests(unittest.TestCase):
    """Outbound sequence numbers, and detection of inbound gaps."""

    def test_outbound_sequence_starts_at_one_and_increments(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)

        for expected in (1, 2, 3):
            _, payload = core.build_outbound(make_message(MessageType.PING))
            header, _ = decode_envelope(payload)
            self.assertEqual(header.sequence, expected)

    def test_sequence_continues_after_a_hundred_messages(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)

        for _ in range(100):
            core.build_outbound(make_message(MessageType.PING))

        self.assertEqual(core.next_sequence, 101)

    def test_outbound_payload_carries_the_session_id(self) -> None:
        session_id = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        core = SessionCore()
        core.open(session_id)

        _, payload = core.build_outbound(make_message(MessageType.PING))

        header, _ = decode_envelope(payload)
        self.assertEqual(header.session_id, session_id)

    def test_inbound_sequence_gap_is_counted_not_fatal(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)

        self.assertEqual(core.inbound_anomalies, 0)
        core.parse_inbound(_inbound(1, MessageType.PING))
        self.assertEqual(core.inbound_anomalies, 0)

        # Sequence 3 where 2 was expected: impossible on healthy TCP, so it is
        # a bug signal worth counting rather than silently accepting.
        core.parse_inbound(_inbound(3, MessageType.PING))
        self.assertEqual(core.inbound_anomalies, 1)
        self.assertEqual(core.last_inbound_sequence, 3)

    def test_inbound_gap_emits_a_trace_event(self) -> None:
        sink = CollectingTraceSink()
        core = SessionCore(emitter=TraceEmitter(sink, node=Node.SERVER))
        core.open(UNASSIGNED_SESSION_ID)

        core.parse_inbound(_inbound(5, MessageType.PING))

        summaries = [event.summary for event in sink.events]
        self.assertTrue(any("sequence anomaly" in summary for summary in summaries))


class EncapsulationTests(unittest.TestCase):
    """Each layer must add its own bytes, in order, and be recoverable."""

    def test_each_layer_adds_a_distinguishable_header(self) -> None:
        sink = CollectingTraceSink()
        core = SessionCore(emitter=TraceEmitter(sink, node=Node.CLIENT))
        core.open(UNASSIGNED_SESSION_ID)

        _, payload = core.build_outbound(make_message(MessageType.BROADCAST, {"text": "halo"}))

        # L6 produced JSON, L5 wrapped it; the JSON must be recoverable intact.
        header, inner = decode_envelope(payload)
        self.assertEqual(header.sequence, 1)
        self.assertEqual(json.loads(inner.decode("utf-8"))["type"], "BROADCAST")
        self.assertEqual(payload[:4], b"\x00" * 4)  # not a length prefix -- L4 adds that

    def test_decapsulation_recovers_the_original_message(self) -> None:
        core = SessionCore()
        core.open(UNASSIGNED_SESSION_ID)
        original = make_message(MessageType.PRIVATE, {"to": "siti", "text": "hai"}, sender="budi")

        _, payload = core.build_outbound(original)
        _, recovered = core.parse_inbound(payload)

        self.assertEqual(recovered["type"], MessageType.PRIVATE)
        self.assertEqual(recovered["payload"], {"to": "siti", "text": "hai"})
        self.assertEqual(recovered["sender"], "budi")

    def test_full_stack_round_trip_through_framing(self) -> None:
        from transport import FrameDecoder, encode_frame

        sender = SessionCore()
        sender.open(UNASSIGNED_SESSION_ID)
        _, payload = sender.build_outbound(make_message(MessageType.BROADCAST, {"text": "halo"}))

        wire = encode_frame(payload)
        decoder = FrameDecoder()
        decoder.feed(wire)
        frame = decoder.pop_frame()

        receiver = SessionCore()
        receiver.open(UNASSIGNED_SESSION_ID)
        _, message = receiver.parse_inbound(frame or b"")

        self.assertEqual(message["payload"]["text"], "halo")


class TraceOrderTests(unittest.TestCase):
    """One outbound message must file L7, L6 and L5 events in that order."""

    def test_outbound_emits_application_presentation_session_in_order(self) -> None:
        sink = CollectingTraceSink()
        core = SessionCore(emitter=TraceEmitter(sink, node=Node.CLIENT))
        core.open(UNASSIGNED_SESSION_ID)

        trace_id, _ = core.build_outbound(make_message(MessageType.BROADCAST, {"text": "halo"}))

        layers = [event.layer for event in sink.events]
        self.assertEqual(layers, [Layer.APPLICATION, Layer.PRESENTATION, Layer.SESSION])
        self.assertTrue(all(event.trace_id == trace_id for event in sink.events))

    def test_inbound_emits_session_then_presentation(self) -> None:
        sink = CollectingTraceSink()
        core = SessionCore(emitter=TraceEmitter(sink, node=Node.SERVER))
        core.open(UNASSIGNED_SESSION_ID)
        sink.clear()

        core.parse_inbound(_inbound(1, MessageType.BROADCAST))

        self.assertEqual(
            [event.layer for event in sink.events], [Layer.SESSION, Layer.PRESENTATION]
        )

    def test_no_events_below_layer_four_are_emitted(self) -> None:
        sink = CollectingTraceSink()
        core = SessionCore(emitter=TraceEmitter(sink, node=Node.CLIENT))
        core.open(UNASSIGNED_SESSION_ID)

        core.build_outbound(make_message(MessageType.BROADCAST, {"text": "halo"}))
        core.parse_inbound(_inbound(1, MessageType.BROADCAST))

        # L3 and below belong to the OS and are proven with Wireshark, not
        # emitted here; an event claiming otherwise would be fabricated.
        self.assertTrue(all(event.layer >= Layer.TRANSPORT for event in sink.events))

    def test_a_disabled_emitter_emits_nothing_and_returns_no_trace_id(self) -> None:
        core = SessionCore(emitter=TraceEmitter.from_env(None, node=Node.CLIENT, enabled=False))
        core.open(UNASSIGNED_SESSION_ID)

        trace_id, payload = core.build_outbound(make_message(MessageType.BROADCAST, {"text": "x"}))

        # Nothing was emitted, so there is no id to correlate -- and the
        # message still goes out framed and sequenced exactly as normal.
        self.assertIsNone(trace_id)
        header, inner = decode_envelope(payload)
        self.assertEqual(header.sequence, 1)
        self.assertIn(b"BROADCAST", inner)


def _inbound(sequence: int, message_type: MessageType) -> bytes:
    """Build a session envelope as an inbound PDU with the given sequence."""
    from presentation import encode

    inner = encode(make_message(message_type, {"text": "x"}, sender="budi"))
    return encode_envelope(inner, session_id=UNASSIGNED_SESSION_ID, sequence=sequence)


if __name__ == "__main__":
    unittest.main()
