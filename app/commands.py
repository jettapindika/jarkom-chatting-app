"""L7 command parsing: one line of user input into one intent.

The client's command language is defined here and nowhere else, so the CLI and
the web client accept exactly the same input. Anything not starting with ``/``
is a broadcast, which is the whole point of the UX: typing a sentence and
pressing enter is the common case, and it must not need a command prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["COMMAND_PREFIX", "Command", "CommandKind", "HELP_TEXT", "parse_input"]


class CommandKind(str, Enum):
    """What the user asked for."""

    BROADCAST = "broadcast"
    PRIVATE = "private"
    NICK = "nick"
    LIST = "list"
    HELP = "help"
    QUIT = "quit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    """One parsed line of input.

    ``argument`` holds the single argument a command takes (the new nickname,
    or the recipient), and ``text`` holds the message body. Splitting them here
    keeps every caller from re-implementing the same ``split(" ", 1)``.
    """

    kind: CommandKind
    argument: str = ""
    text: str = ""
    raw: str = ""

    @property
    def is_local(self) -> bool:
        """Whether the client handles this itself, without the server."""
        return self.kind in {CommandKind.HELP, CommandKind.UNKNOWN}


#: Every command starts with this character. A nickname may never contain it, so
#: a command can never be confused with a message from a user called ``/list``.
COMMAND_PREFIX = "/"

HELP_TEXT = """\
Commands:
  /nick <nama>          ganti nickname
  /list                 tampilkan user yang online
  /msg <user> <pesan>   kirim pesan pribadi
  /help                 tampilkan bantuan ini
  /quit                 keluar

Ketik pesan biasa (tanpa awalan /) untuk broadcast ke semua user.\
"""


def parse_input(line: str) -> Command:
    """Parse one line of user input.

    Args:
        line: the raw line, with or without its trailing newline.

    Returns:
        The parsed :class:`Command`. Unrecognised or malformed commands come
        back as :attr:`CommandKind.UNKNOWN` rather than raising, because a typo
        should print a hint and return to the prompt, not end the session.
    """
    text = line.strip()
    if not text:
        return Command(CommandKind.UNKNOWN, raw=line)

    if not text.startswith(COMMAND_PREFIX):
        return Command(CommandKind.BROADCAST, text=text, raw=line)

    name, _, rest = text[len(COMMAND_PREFIX) :].partition(" ")
    name = name.lower()
    rest = rest.strip()

    if name in {"quit", "exit", "q"}:
        return Command(CommandKind.QUIT, raw=line)

    if name in {"help", "h", "?"}:
        return Command(CommandKind.HELP, raw=line)

    if name in {"list", "who", "users"}:
        return Command(CommandKind.LIST, raw=line)

    if name in {"nick", "name"}:
        if not rest:
            return Command(CommandKind.UNKNOWN, raw=line)
        return Command(CommandKind.NICK, argument=rest, raw=line)

    if name in {"msg", "m", "pm"}:
        target, _, body = rest.partition(" ")
        body = body.strip()
        if not target or not body:
            return Command(CommandKind.UNKNOWN, raw=line)
        return Command(CommandKind.PRIVATE, argument=target, text=body, raw=line)

    return Command(CommandKind.UNKNOWN, raw=line)
