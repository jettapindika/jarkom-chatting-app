"""Presentation-layer (OSI L6) exceptions."""

from __future__ import annotations

from typing import Any

from presentation.error_codes import ErrorCode

__all__ = ["CodecError", "EncodeError", "PresentationError", "SchemaViolation"]


class PresentationError(Exception):
    """Base class for every failure raised by the L6 presentation layer."""


class CodecError(PresentationError):
    """Bytes could not be decoded into a message, or a message into bytes."""


class EncodeError(CodecError):
    """A message could not be serialised, usually because it is not JSON-safe."""


class SchemaViolation(CodecError):
    """A decoded message is valid JSON but not a valid protocol message.

    Carries the offending field so the caller can build a precise ``ERROR``
    response instead of a generic "malformed" message, and optionally the
    protocol error code the server should answer with. The code travels on the
    exception because only the code that detected the violation knows which
    rule was broken; the server would otherwise have to re-derive it from the
    message text, which is how error codes drift apart from their meanings.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        value: Any = None,
        code: ErrorCode | None = None,
    ) -> None:
        self.field = field
        self.value = value
        self.code = code
        super().__init__(message)
