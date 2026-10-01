"""Tests for L4 framing: the length prefix and the stream reassembler.

The behaviours worth defending here are the ones a naive implementation gets
wrong: a frame split across many reads, several frames in one read, an empty
frame, and an oversized frame refused from its prefix alone.
"""

from __future__ import annotations

import json
import unittest

from transport import (
    LENGTH_PREFIX_SIZE,
    MAX_FRAME_SIZE,
    FrameDecoder,
    FrameTooLargeError,
    encode_frame,
    frame_length,
)


def _wire(payload: bytes) -> bytes:
    """Frame ``payload`` the way a peer would put it on the wire."""
    return encode_frame(payload)


class EncodeFrameTests(unittest.TestCase):
    """The encoder must produce a prefix that agrees with the payload."""

    def test_prefix_is_four_byte_big_endian_length(self) -> None:
        payload = b"hello"
        frame = encode_frame(payload)

        self.assertEqual(frame[:LENGTH_PREFIX_SIZE], b"\x00\x00\x00\x05")
        self.assertEqual(frame[LENGTH_PREFIX_SIZE:], payload)
        self.assertEqual(len(frame), LENGTH_PREFIX_SIZE + len(payload))

    def test_known_frame_matches_documented_layout(self) -> None:
        # 0x170 == 368, the example printed in docs/PROTOCOL.md.
        frame = encode_frame(b"x" * 368)
        self.assertEqual(frame[:4], b"\x00\x00\x01\x70")

    def test_empty_payload_is_encodable(self) -> None:
        frame = encode_frame(b"")
        self.assertEqual(frame, b"\x00\x00\x00\x00")

    def test_payload_at_the_limit_is_accepted(self) -> None:
        payload = b"a" * MAX_FRAME_SIZE
        self.assertEqual(len(encode_frame(payload)), MAX_FRAME_SIZE + LENGTH_PREFIX_SIZE)

    def test_payload_over_the_limit_is_refused_before_sending(self) -> None:
        with self.assertRaises(FrameTooLargeError) as caught:
            encode_frame(b"a" * (MAX_FRAME_SIZE + 1))

        self.assertEqual(caught.exception.announced_size, MAX_FRAME_SIZE + 1)
        self.assertEqual(caught.exception.max_frame_size, MAX_FRAME_SIZE)


class FrameLengthTests(unittest.TestCase):
    """Prefix decoding."""

    def test_decodes_big_endian(self) -> None:
        self.assertEqual(frame_length(b"\x00\x00\x01\x70"), 368)
        self.assertEqual(frame_length(b"\x00\x00\x00\x00"), 0)
        self.assertEqual(frame_length(b"\xff\xff\xff\xff"), 0xFFFFFFFF)

    def test_rejects_wrong_width(self) -> None:
        for prefix in (b"", b"\x00", b"\x00\x00\x00", b"\x00\x00\x00\x00\x00"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                frame_length(prefix)


class FrameDecoderTests(unittest.TestCase):
    """Reassembly of a byte stream into frames."""

    def setUp(self) -> None:
        self.decoder = FrameDecoder()

    def test_single_complete_frame(self) -> None:
        self.decoder.feed(_wire(b"payload"))
        self.assertEqual(self.decoder.pop_frame(), b"payload")
        self.assertIsNone(self.decoder.pop_frame())

    def test_frame_reassembled_from_single_byte_reads(self) -> None:
        payload = json.dumps({"type": "BROADCAST", "text": "halo dunia"}).encode()
        wire = _wire(payload)

        for index, byte in enumerate(wire):
            self.decoder.feed(bytes([byte]))
            frame = self.decoder.pop_frame()

            if index < len(wire) - 1:
                self.assertIsNone(frame, f"frame completed early, after byte {index}")
            else:
                self.assertEqual(frame, payload)

    def test_prefix_itself_split_across_reads(self) -> None:
        wire = _wire(b"abc")

        for partial in (wire[:1], wire[1:3]):
            self.decoder.feed(partial)
            self.assertIsNone(self.decoder.pop_frame())

        self.decoder.feed(wire[3:])
        self.assertEqual(self.decoder.pop_frame(), b"abc")

    def test_several_frames_in_one_read(self) -> None:
        self.decoder.feed(_wire(b"one") + _wire(b"two") + _wire(b"three"))

        self.assertEqual(list(self.decoder.frames()), [b"one", b"two", b"three"])
        self.assertIsNone(self.decoder.pop_frame())

    def test_trailing_partial_frame_is_held_back(self) -> None:
        self.decoder.feed(_wire(b"one") + _wire(b"two")[:6])

        self.assertEqual(self.decoder.pop_frame(), b"one")
        self.assertIsNone(self.decoder.pop_frame())
        self.assertGreater(self.decoder.buffered_bytes, 0)

    def test_empty_frame_is_distinct_from_no_frame(self) -> None:
        self.decoder.feed(_wire(b""))

        # b"" is a delivered empty message; None means "nothing available yet".
        # Conflating the two would hang a reader that legitimately got b"".
        self.assertEqual(self.decoder.pop_frame(), b"")
        self.assertIsNone(self.decoder.pop_frame())

    def test_payload_containing_a_newline_byte_round_trips(self) -> None:
        # The reason the protocol does not use newline delimiting: this payload
        # would split into three bogus messages under a delimiter scheme.
        payload = b'{\n  "text": "line one\\nline two"\n}'

        self.decoder.feed(_wire(payload))
        self.assertEqual(self.decoder.pop_frame(), payload)

    def test_payload_containing_a_null_byte_round_trips(self) -> None:
        payload = b"before\x00after"

        self.decoder.feed(_wire(payload))
        self.assertEqual(self.decoder.pop_frame(), payload)

    def test_utf8_multibyte_split_across_reads(self) -> None:
        payload = "日本語テキスト".encode("utf-8")
        wire = _wire(payload)

        # Cut in the middle of a three-byte character.
        cut = LENGTH_PREFIX_SIZE + 4
        self.decoder.feed(wire[:cut])
        self.assertIsNone(self.decoder.pop_frame())

        self.decoder.feed(wire[cut:])
        frame = self.decoder.pop_frame()
        self.assertEqual(frame, payload)
        self.assertEqual(frame.decode("utf-8"), "日本語テキスト")

    def test_frame_at_the_size_limit_is_accepted(self) -> None:
        payload = b"a" * MAX_FRAME_SIZE
        self.decoder.feed(_wire(payload))
        self.assertEqual(len(self.decoder.pop_frame() or b""), MAX_FRAME_SIZE)

    def test_oversized_frame_is_refused_from_the_prefix_alone(self) -> None:
        decoder = FrameDecoder(max_frame_size=1024)
        # Only the prefix is delivered; the payload never arrives.
        decoder.feed((4096).to_bytes(LENGTH_PREFIX_SIZE, "big"))

        with self.assertRaises(FrameTooLargeError) as caught:
            decoder.pop_frame()

        self.assertEqual(caught.exception.announced_size, 4096)
        self.assertEqual(caught.exception.max_frame_size, 1024)

    def test_frame_one_byte_over_the_limit_is_refused(self) -> None:
        decoder = FrameDecoder(max_frame_size=1024)
        decoder.feed((1025).to_bytes(LENGTH_PREFIX_SIZE, "big"))

        with self.assertRaises(FrameTooLargeError):
            decoder.pop_frame()

    def test_rejects_non_positive_max_frame_size(self) -> None:
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FrameDecoder(max_frame_size=value)

    def test_many_sequential_frames_all_decode(self) -> None:
        # Guards the buffer-offset bookkeeping: a compaction bug shows up here
        # as a mangled or lost frame, not as an exception.
        payloads = [f"message-{index}".encode() for index in range(500)]
        self.decoder.feed(b"".join(_wire(payload) for payload in payloads))

        self.assertEqual(list(self.decoder.frames()), payloads)
        self.assertEqual(self.decoder.buffered_bytes, 0)

    def test_decoder_still_works_after_draining_a_large_batch(self) -> None:
        self.decoder.feed(b"".join(_wire(b"x" * 64) for _ in range(200)))
        self.assertEqual(len(list(self.decoder.frames())), 200)

        self.decoder.feed(_wire(b"after"))
        self.assertEqual(self.decoder.pop_frame(), b"after")

    def test_reset_discards_a_partial_frame(self) -> None:
        self.decoder.feed(_wire(b"incomplete")[:7])
        self.decoder.reset()

        self.assertEqual(self.decoder.buffered_bytes, 0)
        self.decoder.feed(_wire(b"fresh"))
        self.assertEqual(self.decoder.pop_frame(), b"fresh")

    def test_feed_of_empty_chunk_is_a_no_op(self) -> None:
        self.decoder.feed(b"")
        self.assertEqual(self.decoder.buffered_bytes, 0)
        self.assertIsNone(self.decoder.pop_frame())


if __name__ == "__main__":
    unittest.main()
