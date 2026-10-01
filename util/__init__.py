"""Cross-cutting helpers that are not part of the protocol stack.

Nothing in this package knows about sockets, framing, sessions or chat
semantics. Keeping it that way is what lets every layer import from here
without creating a sideways dependency between layers.
"""

from util.hexdump import hex_dump
from util.timeutil import parse_iso8601, to_iso8601, utc_now

__all__ = ["hex_dump", "parse_iso8601", "to_iso8601", "utc_now"]
