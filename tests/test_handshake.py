"""Tests for the L5 handshake messages.

The handshake contract has two ends, so each builder is checked against the
matching parser rather than against a hard-coded dict -- a change to one side
that is not mirrored on the other fails here.
"""

from __future__ import annotations

import unittest

from presentation import MessageType, SchemaViolation, make_message
from session import (
    ErrorCode,
    PROTOCOL_VERSION,
    build_connect,
    build_connect_err,
    build_connect_ok,
    parse_connect,
    parse_connect_err,
    parse_connect_ok,
)


class ConnectTests(unittest.TestCase):
    """The client's opening message."""

    def test_round_trip_carries_nickname_and_version(self) -> None:
        nickname, version = parse_connect(build_connect("budi"))

        self.assertEqual(nickname, "budi")
        self.assertEqual(version, PROTOCOL_VERSION)

    def test_explicit_version_is_carried(self) -> None:
        _, version = parse_connect(build_connect("budi", version=2))

        self.assertEqual(version, 2)

    def test_uses_the_connect_message_type(self) -> None:
        self.assertEqual(build_connect("budi")["type"], MessageType.CONNECT)

    def test_rejects_an_invalid_nickname_locally(self) -> None:
        # Failing here means the user hears about it immediately, not after a
        # round trip and an unhelpful server-side rejection.
        for nickname in ("", "budi santoso", "/quit", "x" * 25):
            with self.subTest(nickname=nickname), self.assertRaises(SchemaViolation):
                build_connect(nickname)

    def test_parse_rejects_a_different_message_type(self) -> None:
        with self.assertRaises(SchemaViolation):
            parse_connect(make_message(MessageType.PING))

    def test_parse_rejects_a_missing_nickname(self) -> None:
        with self.assertRaises(SchemaViolation):
            parse_connect(make_message(MessageType.CONNECT, {"version": 1}))

    def test_parse_rejects_an_invalid_nickname(self) -> None:
        with self.assertRaises(SchemaViolation):
            parse_connect(make_message(MessageType.CONNECT, {"nick": "budi santoso"}))

    def test_parse_rejects_a_non_integer_version(self) -> None:
        for version in ("1", 1.5, None, True):
            with self.subTest(version=version), self.assertRaises(SchemaViolation):
                parse_connect(make_message(MessageType.CONNECT, {"nick": "budi", "version": version}))

    def test_parse_defaults_the_version_when_absent(self) -> None:
        _, version = parse_connect(make_message(MessageType.CONNECT, {"nick": "budi"}))

        self.assertEqual(version, PROTOCOL_VERSION)


class ConnectOkTests(unittest.TestCase):
    """The server's acceptance."""

    def test_round_trip_carries_session_id_and_nickname(self) -> None:
        session_id, nickname = parse_connect_ok(build_connect_ok("abc-123", "budi"))

        self.assertEqual(session_id, "abc-123")
        self.assertEqual(nickname, "budi")

    def test_uses_the_connect_ok_message_type(self) -> None:
        self.assertEqual(build_connect_ok("abc-123", "budi")["type"], MessageType.CONNECT_OK)

    def test_parse_rejects_a_missing_session_id(self) -> None:
        with self.assertRaises(SchemaViolation) as caught:
            parse_connect_ok(make_message(MessageType.CONNECT_OK, {"nick": "budi"}))

        self.assertEqual(caught.exception.field, "session_id")

    def test_parse_rejects_an_empty_session_id(self) -> None:
        with self.assertRaises(SchemaViolation):
            parse_connect_ok(make_message(MessageType.CONNECT_OK, {"session_id": "", "nick": "budi"}))

    def test_parse_rejects_a_missing_nickname(self) -> None:
        with self.assertRaises(SchemaViolation) as caught:
            parse_connect_ok(make_message(MessageType.CONNECT_OK, {"session_id": "abc"}))

        self.assertEqual(caught.exception.field, "nick")


class ConnectErrTests(unittest.TestCase):
    """The server's rejection, which must name a machine-readable cause."""

    def test_round_trip_carries_code_and_detail(self) -> None:
        code, detail = parse_connect_err(
            build_connect_err(ErrorCode.NICK_TAKEN, "nickname already in use")
        )

        self.assertEqual(code, "NICK_TAKEN")
        self.assertEqual(detail, "nickname already in use")

    def test_uses_the_connect_err_message_type(self) -> None:
        self.assertEqual(
            build_connect_err(ErrorCode.NICK_TAKEN, "x")["type"], MessageType.CONNECT_ERR
        )

    def test_parse_rejects_a_missing_code(self) -> None:
        with self.assertRaises(SchemaViolation) as caught:
            parse_connect_err(make_message(MessageType.CONNECT_ERR, {"message": "x"}))

        self.assertEqual(caught.exception.field, "code")

    def test_parse_tolerates_a_missing_detail(self) -> None:
        code, detail = parse_connect_err(make_message(MessageType.CONNECT_ERR, {"code": "MALFORMED"}))

        self.assertEqual(code, "MALFORMED")
        self.assertEqual(detail, "")


if __name__ == "__main__":
    unittest.main()
