"""OSI L6: JSON-over-UTF-8 serialisation and message-schema validation.

Everything above this layer works with dicts and :class:`MessageType` members;
nothing above it knows the wire format is JSON.
"""

from presentation.codec import decode, encode, make_message
from presentation.error_codes import ErrorCode
from presentation.errors import CodecError, EncodeError, PresentationError, SchemaViolation
from presentation.message_types import CLIENT_TO_SERVER, SERVER_TO_CLIENT, MessageType
from presentation.schema import (
    ENVELOPE_FIELDS,
    MAX_NICKNAME_LENGTH,
    MAX_TEXT_LENGTH,
    MIN_NICKNAME_LENGTH,
    REQUIRED_FIELDS,
    extract_text,
    is_valid_nickname,
    validate_message,
)

__all__ = [
    "CLIENT_TO_SERVER",
    "CodecError",
    "ENVELOPE_FIELDS",
    "EncodeError",
    "ErrorCode",
    "MAX_NICKNAME_LENGTH",
    "MAX_TEXT_LENGTH",
    "MIN_NICKNAME_LENGTH",
    "MessageType",
    "PresentationError",
    "REQUIRED_FIELDS",
    "SERVER_TO_CLIENT",
    "SchemaViolation",
    "decode",
    "encode",
    "extract_text",
    "is_valid_nickname",
    "make_message",
    "validate_message",
]
