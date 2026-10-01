"""Tests for the trace layer: event shape, ordering and the disabled path.

The visualizer reads exactly what these tests assert, so a change to the event
schema that is not mirrored here would show up as a blank panel in the browser
rather than as a failing test.
"""

from __future__ import annotations

import json
import unittest

from presentation import MessageType, make_message
from session import SessionCore
from trace import (
    DEFAULT_HEX_LIMIT,
    LAYER_NAMES,
    LAYER_PDU_TYPES,
    CollectingTraceSink,
    Direction,
    FanoutTraceSink,
    JsonLinesTraceSink,
    Layer,
    Node,
    NullTraceSink,
    TraceEmitter,
)
from transport import FrameDecoder, encode_frame
from util.hexdump import hex_dump
from util.timeutil import parse_iso8601


class TraceEventShapeTests(unittest.TestCase):
    """The JSON contract the visualizer consumes."""

    def setUp(self) -> None:
        self.sink = CollectingTraceSink()
        self.emitter = TraceEmitter(self.sink, node=Node.SERVER, session_id="abc-123")

    def test_to_dict_uses_the_documented_camel_case_keys(self) -> None:
        self.emitter.emit(
            layer=Layer.PRESENTATION,
            direction=Direction.OUTBOUND,
            summary="encoded BROADCAST",
            payload=b'{"type":"BROADCAST"}',
        )

        payload = self.sink.events[0].to_dict()

        self.assertEqual(
            set(payload),
            {
                "traceId",
                "sessionId",
                "direction",
                "layer",
                "layerName",
                "pduType",
                "node",
                "summary",
                "payloadPreview",
                "payloadHex",
                "sizeBytes",
                "timestamp",
            },
        )

    def test_layer_is_numeric_and_layer_name_is_text(self) -> None:
        self.emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="x")

        payload = self.sink.events[0].to_dict()

        self.assertEqual(payload["layer"], 5)
        self.assertEqual(payload["layerName"], "Session")
        self.assertIsInstance(payload["layer"], int)

    def test_transport_events_are_labelled_as_segments(self) -> None:
        self.emitter.emit(layer=Layer.TRANSPORT, direction=Direction.OUTBOUND, summary="x")

        self.assertEqual(self.sink.events[0].to_dict()["pduType"], "segment")

    def test_event_is_json_serialisable(self) -> None:
        self.emitter.emit(layer=Layer.APPLICATION, direction=Direction.OUTBOUND, summary="x")

        # Round-trips through JSON, which is how it reaches the browser.
        encoded = json.dumps(self.sink.events[0].to_dict())
        self.assertEqual(json.loads(encoded)["node"], "server")

    def test_timestamp_is_iso8601_utc_with_milliseconds(self) -> None:
        self.emitter.emit(layer=Layer.SESSION, direction=Direction.INBOUND, summary="x")

        timestamp = self.sink.events[0].to_dict()["timestamp"]

        self.assertTrue(timestamp.endswith("Z"))
        parse_iso8601(timestamp)

    def test_session_id_is_carried_and_updated_by_the_handshake(self) -> None:
        self.assertIsNone(TraceEmitter(self.sink).session_id)

        core = SessionCore(emitter=self.emitter)
        core.open("3f2504e0-4f89-11d3-9a0c-0305e82c3301")

        self.assertEqual(self.emitter.session_id, "3f2504e0-4f89-11d3-9a0c-0305e82c3301")

    def test_size_bytes_counts_the_pdu_at_that_layer(self) -> None:
        self.emitter.emit(
            layer=Layer.PRESENTATION, direction=Direction.OUTBOUND, summary="x", payload=b"12345"
        )

        self.assertEqual(self.sink.events[0].size_bytes, 5)

    def test_layer_names_and_pdu_types_cover_every_implemented_layer(self) -> None:
        for layer in Layer:
            with self.subTest(layer=layer):
                self.assertIn(layer, LAYER_NAMES)
                self.assertIn(layer, LAYER_PDU_TYPES)

    def test_only_layers_four_to_seven_exist(self) -> None:
        # L3 and below are the OS's job. An event claiming to be one of them
        # would be fabricated evidence in the visualizer.
        self.assertEqual(sorted(int(layer) for layer in Layer), [4, 5, 6, 7])


class PreviewAndHexTests(unittest.TestCase):
    """How much of a payload is retained, and how truncation is marked."""

    def setUp(self) -> None:
        self.sink = CollectingTraceSink()
        self.emitter = TraceEmitter(self.sink, node=Node.CLIENT)

    def test_preview_defaults_to_the_utf8_payload(self) -> None:
        self.emitter.emit(
            layer=Layer.PRESENTATION,
            direction=Direction.OUTBOUND,
            summary="x",
            payload='{"type":"PING"}'.encode(),
        )

        self.assertEqual(self.sink.events[0].payload_preview, '{"type":"PING"}')

    def test_invalid_utf8_does_not_raise_and_is_replaced(self) -> None:
        self.emitter.emit(
            layer=Layer.TRANSPORT, direction=Direction.INBOUND, summary="x", payload=b"\xff\xfe"
        )

        self.assertEqual(len(self.sink.events[0].payload_preview), 2)

    def test_hex_is_space_separated_lowercase(self) -> None:
        self.emitter.emit(
            layer=Layer.TRANSPORT, direction=Direction.OUTBOUND, summary="x", payload=b"\x00\xab"
        )

        self.assertEqual(self.sink.events[0].payload_hex, "00 ab")

    def test_long_payloads_are_truncated_in_both_renditions(self) -> None:
        emitter = TraceEmitter(self.sink, node=Node.CLIENT, preview_limit=16, hex_limit=8)

        emitter.emit(layer=Layer.TRANSPORT, direction=Direction.OUTBOUND, summary="x", payload=b"a" * 64)

        event = self.sink.events[0]
        self.assertIn("(+", event.payload_preview)
        self.assertIn("(+", event.payload_hex)
        # size_bytes still reports the whole PDU, so the UI can show "64 B".
        self.assertEqual(event.size_bytes, 64)

    def test_default_hex_limit_is_applied_to_a_full_size_frame(self) -> None:
        self.emitter.emit(
            layer=Layer.TRANSPORT,
            direction=Direction.OUTBOUND,
            summary="x",
            payload=b"\x00" * 4096,
        )

        self.assertLessEqual(len(self.sink.events[0].payload_hex), DEFAULT_HEX_LIMIT * 3 + 32)


class DisabledEmitterTests(unittest.TestCase):
    """With tracing off, nothing is built and nothing is published."""

    def test_disabled_emitter_publishes_nothing(self) -> None:
        sink = CollectingTraceSink()
        emitter = TraceEmitter(sink, node=Node.CLIENT, enabled=False)

        self.assertIsNone(
            emitter.emit(layer=Layer.APPLICATION, direction=Direction.OUTBOUND, summary="x")
        )
        self.assertEqual(sink.events, [])

    def test_from_env_reads_the_trace_enabled_variable(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on", " true "):
            with self.subTest(value=value):
                emitter = TraceEmitter.from_env(None, environ={"TRACE_ENABLED": value})
                self.assertTrue(emitter.enabled)

        for value in ("0", "false", "", "nope"):
            with self.subTest(value=value):
                emitter = TraceEmitter.from_env(None, environ={"TRACE_ENABLED": value})
                self.assertFalse(emitter.enabled)

    def test_from_env_is_off_when_the_variable_is_absent(self) -> None:
        self.assertFalse(TraceEmitter.from_env(None, environ={}).enabled)

    def test_explicit_enabled_argument_overrides_the_environment(self) -> None:
        emitter = TraceEmitter.from_env(None, enabled=True, environ={"TRACE_ENABLED": "0"})

        self.assertTrue(emitter.enabled)

    def test_enabled_can_be_flipped_at_runtime(self) -> None:
        sink = CollectingTraceSink()
        emitter = TraceEmitter(sink, node=Node.CLIENT, enabled=False)

        emitter.set_enabled(True)
        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="x")

        self.assertEqual(len(sink.events), 1)


class SinkTests(unittest.TestCase):
    """Sink behaviours the visualizer and the JSONL log depend on."""

    def test_collecting_sink_filters_by_trace_id(self) -> None:
        sink = CollectingTraceSink()
        emitter = TraceEmitter(sink, node=Node.CLIENT)

        first = emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="a")
        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="b")

        self.assertEqual(len(sink.for_trace(first or "")), 1)
        self.assertEqual(sink.for_trace("does-not-exist"), [])

    def test_null_sink_discards_events(self) -> None:
        sink = NullTraceSink()
        emitter = TraceEmitter(sink, node=Node.CLIENT)

        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="x")

        self.assertEqual(getattr(sink, "events", []), [])

    def test_fanout_sink_reaches_every_child(self) -> None:
        left, right = CollectingTraceSink(), CollectingTraceSink()
        emitter = TraceEmitter(FanoutTraceSink([left, right]), node=Node.BRIDGE)

        emitter.emit(layer=Layer.TRANSPORT, direction=Direction.OUTBOUND, summary="x")

        self.assertEqual(len(left.events), 1)
        self.assertEqual(len(right.events), 1)

    def test_fanout_sink_accepts_late_subscribers(self) -> None:
        fanout = FanoutTraceSink()
        emitter = TraceEmitter(fanout, node=Node.BRIDGE)
        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="missed")

        late = CollectingTraceSink()
        fanout.add(late)
        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="seen")

        self.assertEqual([event.summary for event in late.events], ["seen"])

    def test_fanout_sink_remove_is_a_no_op_for_unknown_sinks(self) -> None:
        kept = CollectingTraceSink()
        fanout = FanoutTraceSink([kept])
        emitter = TraceEmitter(fanout, node=Node.BRIDGE)

        fanout.remove(CollectingTraceSink())
        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="still here")

        self.assertEqual([event.summary for event in kept.events], ["still here"])

    def test_fanout_sink_detaches_a_removed_sink(self) -> None:
        dropped, kept = CollectingTraceSink(), CollectingTraceSink()
        fanout = FanoutTraceSink([dropped, kept])
        emitter = TraceEmitter(fanout, node=Node.BRIDGE)

        fanout.remove(dropped)
        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="x")

        self.assertEqual(dropped.events, [])
        self.assertEqual(len(kept.events), 1)

    def test_json_lines_sink_writes_one_object_per_line(self) -> None:
        import io

        stream = io.StringIO()
        emitter = TraceEmitter(JsonLinesTraceSink(stream), node=Node.SERVER)

        emitter.emit(layer=Layer.SESSION, direction=Direction.OUTBOUND, summary="first")
        emitter.emit(layer=Layer.TRANSPORT, direction=Direction.OUTBOUND, summary="second")

        lines = [line for line in stream.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["summary"], "first")
        self.assertEqual(json.loads(lines[1])["layerName"], "Transport")


class FullStackTraceOrderTests(unittest.TestCase):
    """The end-to-end claim the visualizer makes: L7 -> L6 -> L5 -> L4."""

    def test_one_outbound_message_files_l7_l6_l5_l4_in_order(self) -> None:
        sink = CollectingTraceSink()
        emitter = TraceEmitter(sink, node=Node.CLIENT)

        core = SessionCore(emitter=emitter)
        core.open("3f2504e0-4f89-11d3-9a0c-0305e82c3301")

        trace_id, payload = core.build_outbound(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )

        # The driver's job, mirroring what ClientSession.send does.
        frame = encode_frame(payload)
        emitter.emit(
            layer=Layer.TRANSPORT,
            direction=Direction.OUTBOUND,
            summary=f"{len(payload)} B payload",
            trace_id=trace_id,
            payload=frame,
        )

        self.assertEqual(
            [event.layer for event in sink.events],
            [Layer.APPLICATION, Layer.PRESENTATION, Layer.SESSION, Layer.TRANSPORT],
        )
        self.assertEqual({event.trace_id for event in sink.events}, {trace_id})
        self.assertEqual({event.direction for event in sink.events}, {Direction.OUTBOUND})

    def test_sizes_grow_by_exactly_one_header_per_layer(self) -> None:
        sink = CollectingTraceSink()
        emitter = TraceEmitter(sink, node=Node.CLIENT)
        core = SessionCore(emitter=emitter)
        core.open("3f2504e0-4f89-11d3-9a0c-0305e82c3301")

        trace_id, payload = core.build_outbound(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )
        emitter.emit(
            layer=Layer.TRANSPORT,
            direction=Direction.OUTBOUND,
            summary="framed",
            trace_id=trace_id,
            payload=encode_frame(payload),
        )

        application, presentation, session, transport = sink.events
        self.assertEqual(presentation.size_bytes, application.size_bytes)
        self.assertEqual(session.size_bytes, presentation.size_bytes + 24)
        self.assertEqual(transport.size_bytes, session.size_bytes + 4)

    def test_inbound_message_files_l4_l5_l6_in_order(self) -> None:
        sink = CollectingTraceSink()
        emitter = TraceEmitter(sink, node=Node.SERVER)
        sender = SessionCore()
        sender.open("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
        _, payload = sender.build_outbound(
            make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")
        )

        receiver = SessionCore(emitter=emitter)
        receiver.open("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
        decoder = FrameDecoder()
        decoder.feed(encode_frame(payload))
        frame = decoder.pop_frame() or b""

        emitter.emit(
            layer=Layer.TRANSPORT, direction=Direction.INBOUND, summary="framed", payload=frame
        )
        receiver.parse_inbound(frame)

        self.assertEqual(
            [event.layer for event in sink.events],
            [Layer.TRANSPORT, Layer.SESSION, Layer.PRESENTATION],
        )
        self.assertEqual({event.direction for event in sink.events}, {Direction.INBOUND})

    def test_hexdump_marks_truncation(self) -> None:
        self.assertEqual(hex_dump(b"\xde\xad\xbe\xef"), "de ad be ef")
        self.assertEqual(hex_dump(b"", limit=4), "")
        self.assertEqual(hex_dump(b"\x00" * 10, limit=2), "00 00 ...(+8 bytes)")
        self.assertEqual(hex_dump(b"\x00" * 10), "00 00 00 00 00 00 00 00 00 00")


if __name__ == "__main__":
    unittest.main()
