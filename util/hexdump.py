"""Byte-level rendering used by trace events and the visualizer."""

from __future__ import annotations

__all__ = ["hex_dump"]


def hex_dump(data: bytes, *, limit: int | None = None) -> str:
    """Render ``data`` as space-separated lowercase hex bytes.

    Args:
        data: bytes to render.
        limit: render at most this many leading bytes. When truncation
            happens the result ends with an explicit ``...(+N bytes)`` marker
            so a reader can never mistake a clipped dump for the whole PDU.

    Returns:
        The hex string, or ``""`` for empty input.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")

    view = data if limit is None else data[:limit]
    rendered = " ".join(f"{byte:02x}" for byte in view)

    if limit is not None and len(data) > limit:
        rendered += f" ...(+{len(data) - limit} bytes)"

    return rendered
