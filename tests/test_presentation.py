"""Tests for L6: JSON encoding, UTF-8 handling and envelope validation."""

from __future__ import annotations

import json
import unittest

from presentation import (
    MAX_TEXT_LENGTH,
    CodecError,
    MessageType,
    SchemaViolation,
    decode,
    encode,
    extract_text,
    is_valid_nickname,
    make_message,
    validate_message,
)
from util.timeutil import parse_iso8601, utc_now


def _envelope(**overrides: object) -> dict:
    """A minimal valid envelope, with fields overridable per test."""
    base = {
        "type": "BROADCAST",
        "sender": "budi",
        "payload": {"text": "halo"},
        "timestamp": "2026-10-01T06:00:00.000Z",
    }
    base.update(overrides)
    return base


def _floor_ms(moment):
    """Truncate ``moment`` to millisecond precision, matching the wire format."""
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


class EncodeDecodeTests(unittest.TestCase):
    """Round-tripping a message through the wire format."""

    def test_round_trip_preserves_every_field(self) -> None:
        original = make_message(MessageType.BROADCAST, {"text": "halo"}, sender="budi")

        decoded = decode(encode(original))

        self.assertEqual(decoded["type"], MessageType.BROADCAST)
        self.assertEqual(decoded["sender"], "budi")
        self.assertEqual(decoded["payload"], {"text": "halo"})
        self.assertEqual(decoded["timestamp"], original["timestamp"])

    def test_encoded_form_is_utf8_json(self) -> None:
        data = encode(_envelope())

        self.assertIsInstance(data, bytes)
        self.assertEqual(json.loads(data.decode("utf-8"))["type"], "BROADCAST")

    def test_non_ascii_text_survives_as_utf8(self) -> None:
        message = _envelope(payload={"text": "halo 日本語 café"})

        data = encode(message)

        # Not \u-escaped: the wire format is UTF-8, and leaving the characters
        # intact is what makes the payload legible in a Wireshark view.
        self.assertIn("日本語".encode("utf-8"), data)
        self.assertNotIn(b"\\u65e5", data)
        self.assertEqual(decode(data)["payload"]["text"], "halo 日本語 café")

    def test_encode_rejects_a_malformed_envelope(self) -> None:
        with self.assertRaises(SchemaViolation):
            encode({"type": "BROADCAST"})

    def test_encode_rejects_non_json_serialisable_payload(self) -> None:
        with self.assertRaises(CodecError):
            encode(_envelope(payload={"text": object()}))

    def test_decode_rejects_invalid_utf8(self) -> None:
        with self.assertRaises(CodecError) as caught:
            decode(b'{"type": "\xff\xfe"}')

        self.assertIn("UTF-8", str(caught.exception))

    def test_decode_rejects_invalid_json(self) -> None:
        for bad in (b"{", b"not json at all", b"", b'{"type": }'):
            with self.subTest(bad=bad), self.assertRaises(CodecError):
                decode(bad)

    def test_decode_rejects_a_json_value_that_is_not_an_object(self) -> None:
        for bad in (b"[]", b'"string"', b"42", b"null"):
            with self.subTest(bad=bad), self.assertRaises(SchemaViolation):
                decode(bad)


class ValidateMessageTests(unittest.TestCase):
    """Envelope schema enforcement."""

    def test_valid_envelope_normalises_the_type(self) -> None:
        validated = validate_message(_envelope())

        self.assertIs(validated["type"], MessageType.BROADCAST)

    def test_missing_required_field_names_the_field(self) -> None:
        for field in ("type", "sender", "payload", "timestamp"):
            with self.subTest(field=field):
                broken = _envelope()
                del broken[field]

                with self.assertRaises(SchemaViolation) as caught:
                    validate_message(broken)

                self.assertEqual(caught.exception.field, field)

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(SchemaViolation) as caught:
            validate_message(_envelope(nickname="extra"))

        self.assertEqual(caught.exception.field, "nickname")

    def test_unknown_message_type_is_rejected(self) -> None:
        with self.assertRaises(SchemaViolation) as caught:
            validate_message(_envelope(type="NOT_A_TYPE"))

        self.assertEqual(caught.exception.field, "type")

    def test_type_must_be_a_string(self) -> None:
        with self.assertRaises(SchemaViolation):
            validate_message(_envelope(type=7))

    def test_payload_must_be_an_object(self) -> None:
        for payload in ("text", 7, [], None):
            with self.subTest(payload=payload), self.assertRaises(SchemaViolation) as caught:
                validate_message(_envelope(payload=payload))
            self.assertEqual(caught.exception.field, "payload")

    def test_timestamp_must_be_iso8601_utc_with_milliseconds(self) -> None:
        for timestamp in ("2026-10-01T06:00:00Z", "yesterday", "2026-10-01 06:00:00", 123):
            with self.subTest(timestamp=timestamp), self.assertRaises(SchemaViolation) as caught:
                validate_message(_envelope(timestamp=timestamp))
            self.assertEqual(caught.exception.field, "timestamp")

    def test_message_must_be_an_object(self) -> None:
        with self.assertRaises(SchemaViolation):
            validate_message(["type", "BROADCAST"])


class MakeMessageTests(unittest.TestCase):
    """Envelope construction."""

    def test_stamps_a_server_side_utc_timestamp(self) -> None:
        before = utc_now()
        message = make_message(MessageType.USER_LIST, {"users": []})
        after = utc_now()

        self.assertTrue(message["timestamp"].endswith("Z"))
        self.assertEqual(len(message["timestamp"]), len("2026-10-01T06:00:00.000Z"))
        # Freshly stamped, not a constant or a stale default. Comparing at
        # millisecond resolution keeps this deterministic on a fast machine.
        self.assertGreaterEqual(parse_iso8601(message["timestamp"]), _floor_ms(before))
        self.assertLessEqual(parse_iso8601(message["timestamp"]), after)

    def test_accepts_a_plain_string_type(self) -> None:
        self.assertEqual(make_message("PING")["type"], "PING")

    def test_payload_defaults_to_empty_object_and_is_copied(self) -> None:
        source = {"text": "hi"}
        message = make_message(MessageType.BROADCAST, source)

        source["text"] = "mutated"
        self.assertEqual(message["payload"], {"text": "hi"})

    def test_explicit_timestamp_is_respected(self) -> None:
        message = make_message(MessageType.PING, timestamp="2026-10-01T06:00:00.000Z")

        self.assertEqual(message["timestamp"], "2026-10-01T06:00:00.000Z")

    def test_built_message_passes_validation(self) -> None:
        for message_type in MessageType:
            with self.subTest(message_type=message_type):
                validate_message(make_message(message_type, {"text": "x"}, sender="budi"))


class NicknameTests(unittest.TestCase):
    """Display-name rules, applied identically on both sides."""

    def test_accepts_ordinary_names(self) -> None:
        for nickname in ("budi", "Budi_123", "user-42", "日本語", "a"):
            with self.subTest(nickname=nickname):
                self.assertTrue(is_valid_nickname(nickname))

    def test_rejects_empty_and_overlong_names(self) -> None:
        for nickname in ("", "x" * 25):
            with self.subTest(nickname=nickname):
                self.assertFalse(is_valid_nickname(nickname))

    def test_rejects_names_with_whitespace(self) -> None:
        for nickname in ("budi santoso", " budi", "budi ", "budi\ttab", "budi\nnewline"):
            with self.subTest(nickname=nickname):
                self.assertFalse(is_valid_nickname(nickname))

    def test_rejects_control_characters(self) -> None:
        for nickname in ("budi\x00", "budi\x07", "budi\x1b[31m"):
            with self.subTest(nickname=nickname):
                self.assertFalse(is_valid_nickname(nickname))

    def test_rejects_the_command_prefix(self) -> None:
        # A nickname starting with "/" would be indistinguishable from a command.
        self.assertFalse(is_valid_nickname("/quit"))

    def test_rejects_non_strings(self) -> None:
        for nickname in (None, 7, ["budi"], {"nick": "budi"}):
            with self.subTest(nickname=nickname):
                self.assertFalse(is_valid_nickname(nickname))


class ExtractTextTests(unittest.TestCase):
    """Chat-body extraction, which is where oversized messages are caught."""

    def test_returns_the_text(self) -> None:
        self.assertEqual(extract_text({"text": "halo"}), "halo")

    def test_rejects_missing_or_empty_text(self) -> None:
        for payload in ({}, {"text": ""}, {"text": "   "}):
            with self.subTest(payload=payload), self.assertRaises(SchemaViolation):
                extract_text(payload)

    def test_rejects_non_string_text(self) -> None:
        with self.assertRaises(SchemaViolation):
            extract_text({"text": 42})

    def test_accepts_text_at_the_limit_and_rejects_one_over(self) -> None:
        self.assertEqual(len(extract_text({"text": "a" * MAX_TEXT_LENGTH})), MAX_TEXT_LENGTH)

        with self.assertRaises(SchemaViolation):
            extract_text({"text": "a" * (MAX_TEXT_LENGTH + 1)})


if __name__ == "__main__":
    unittest.main()
