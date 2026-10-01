"""Message schema: what a well-formed protocol message looks like.

Validation lives here, apart from serialisation, because the two failures are
different: a ``json.JSONDecodeError`` means the peer sent bytes we cannot read,
while a :class:`SchemaViolation` means we read them perfectly and they are not a
protocol message. The server answers the two with different error codes.

The envelope is fixed by the project specification::

    {"type": "...", "sender": "...", "payload": {...}, "timestamp": "..."}
"""

from __future__ import annotations

from typing import Any, Mapping

from presentation.error_codes import ErrorCode
from presentation.errors import SchemaViolation
from presentation.message_types import MessageType
from util.timeutil import parse_iso8601

__all__ = [
    "ENVELOPE_FIELDS",
    "MAX_NICKNAME_LENGTH",
    "MAX_TEXT_LENGTH",
    "REQUIRED_FIELDS",
    "is_valid_nickname",
    "validate_message",
]

#: Fields every message must carry.
REQUIRED_FIELDS: tuple[str, ...] = ("type", "sender", "payload", "timestamp")

#: Complete set of permitted top-level fields. Unknown fields are rejected
#: rather than ignored: a typo in a field name should fail loudly at
#: development time, not silently drop data in production.
ENVELOPE_FIELDS: frozenset[str] = frozenset(REQUIRED_FIELDS)

#: Nickname length bounds, enforced identically on client and server.
MIN_NICKNAME_LENGTH = 1
MAX_NICKNAME_LENGTH = 24

#: Maximum length of a chat message body, in characters.
MAX_TEXT_LENGTH = 4096


def validate_message(raw: Any) -> dict[str, Any]:
    """Validate a decoded JSON value against the message schema.

    Args:
        raw: the value produced by ``json.loads``.

    Returns:
        A shallow copy of ``raw`` with ``type`` normalised to a
        :class:`MessageType`.

    Raises:
        SchemaViolation: with the offending ``field`` attached, so callers can
            report precisely which field was wrong.
    """
    if not isinstance(raw, dict):
        raise SchemaViolation(f"message must be a JSON object, got {type(raw).__name__}")

    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise SchemaViolation(
            f"missing required field(s): {', '.join(missing)}", field=missing[0]
        )

    unknown = set(raw) - ENVELOPE_FIELDS
    if unknown:
        name = sorted(unknown)[0]
        raise SchemaViolation(f"unknown field: {name}", field=name)

    raw_type = raw["type"]
    if not isinstance(raw_type, str):
        raise SchemaViolation(
            f"type must be a string, got {type(raw_type).__name__}", field="type", value=raw_type
        )
    try:
        message_type = MessageType(raw_type)
    except ValueError:
        raise SchemaViolation(f"unknown message type: {raw_type}", field="type", value=raw_type) from None

    sender = raw["sender"]
    if not isinstance(sender, str):
        raise SchemaViolation(
            f"sender must be a string, got {type(sender).__name__}", field="sender", value=sender
        )

    if not isinstance(raw["payload"], dict):
        raise SchemaViolation(
            f"payload must be a JSON object, got {type(raw['payload']).__name__}",
            field="payload",
            value=raw["payload"],
        )

    timestamp = raw["timestamp"]
    if not isinstance(timestamp, str):
        raise SchemaViolation(
            f"timestamp must be a string, got {type(timestamp).__name__}",
            field="timestamp",
            value=timestamp,
        )
    try:
        parse_iso8601(timestamp)
    except ValueError:
        raise SchemaViolation(
            f"timestamp is not ISO-8601 UTC with milliseconds: {timestamp}",
            field="timestamp",
            value=timestamp,
        ) from None

    validated = dict(raw)
    validated["type"] = message_type
    return validated


def is_valid_nickname(nickname: Any) -> bool:
    """Return whether ``nickname`` is acceptable as a display name.

    Rejects whitespace, control characters and the ``/`` command prefix so a
    nickname can never be confused with a command, and can never break the
    single-line rendering of the user list.
    """
    if not isinstance(nickname, str):
        return False
    if not MIN_NICKNAME_LENGTH <= len(nickname) <= MAX_NICKNAME_LENGTH:
        return False
    if nickname.startswith("/"):
        return False
    return all(character.isprintable() and not character.isspace() for character in nickname)


def extract_text(payload: Mapping[str, Any], *, field: str = "text") -> str:
    """Pull a chat body out of ``payload``, validating its type and length.

    Raises:
        SchemaViolation: if the field is absent, not a string, empty, or over
            :data:`MAX_TEXT_LENGTH` characters.
    """
    if field not in payload:
        raise SchemaViolation(f"payload is missing '{field}'", field=field)

    value = payload[field]
    if not isinstance(value, str):
        raise SchemaViolation(
            f"payload.{field} must be a string, got {type(value).__name__}",
            field=field,
            value=value,
        )
    if not value.strip():
        raise SchemaViolation(f"payload.{field} must not be empty", field=field)
    if len(value) > MAX_TEXT_LENGTH:
        raise SchemaViolation(
            f"payload.{field} exceeds {MAX_TEXT_LENGTH} characters",
            field=field,
            value=len(value),
            code=ErrorCode.TEXT_TOO_LONG,
        )
    return value
