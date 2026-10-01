"""L7 client logic: what the user meant, and what they should see.

This module is the client's application layer with the I/O taken out. It turns
one parsed line of input into one action, and one inbound message into one line
of display text. Neither function touches a socket or a terminal, which is what
lets the CLI, the web bridge, and the tests share exactly the same behaviour --
and what makes the client's command language a single definition rather than two
that drift.

The split matters for the same reason it does in the server: a decision that
lives in a pure function can be tested by calling it, and a rendering rule that
lives in the renderer cannot be tested without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.commands import HELP_TEXT, Command, CommandKind
from presentation import MessageType, extract_text, is_valid_nickname, make_message
from util.timeutil import parse_iso8601

__all__ = ["Action", "ActionKind", "format_message", "plan"]

#: Prefix on every line the server generates rather than a user writing.
SYSTEM_PREFIX = "***"


class ActionKind(str, Enum):
    """What the client should do about one command."""

    SEND = "send"
    """Transmit ``Action.message`` and continue."""

    SHOW = "show"
    """Print ``Action.text`` locally and continue."""

    QUIT = "quit"
    """Leave the chat."""


@dataclass(frozen=True, slots=True)
class Action:
    """One thing to do, decided without any I/O."""

    kind: ActionKind
    message: dict[str, Any] | None = None
    text: str = ""


def plan(command: Command, *, nickname: str) -> Action:
    """Turn one parsed command into one action.

    Args:
        command: the result of :func:`app.commands.parse_input`.
        nickname: the sender's current nickname, stamped onto outgoing frames.
            The server overwrites this field from the handshake, so it is for
            the trace and the recipient's display, not for authority.

    Returns:
        The action to carry out. Nothing here can fail: a command that cannot be
        carried out comes back as :attr:`ActionKind.SHOW` with an explanation,
        because a typo should print a hint and return to the prompt.
    """
    if command.kind is CommandKind.BROADCAST:
        return Action(
            ActionKind.SEND,
            make_message(MessageType.BROADCAST, {"text": command.text}, sender=nickname),
        )

    if command.kind is CommandKind.PRIVATE:
        return Action(
            ActionKind.SEND,
            make_message(
                MessageType.PRIVATE,
                {"to": command.argument, "text": command.text},
                sender=nickname,
            ),
        )

    if command.kind is CommandKind.NICK:
        if not is_valid_nickname(command.argument):
            return Action(
                ActionKind.SHOW,
                text=f"{SYSTEM_PREFIX} nickname tidak valid: {command.argument!r}",
            )
        return Action(
            ActionKind.SEND,
            make_message(MessageType.NICK, {"nick": command.argument}, sender=nickname),
        )

    if command.kind is CommandKind.LIST:
        return Action(
            ActionKind.SEND, make_message(MessageType.USER_LIST, {}, sender=nickname)
        )

    if command.kind is CommandKind.HELP:
        return Action(ActionKind.SHOW, text=HELP_TEXT)

    if command.kind is CommandKind.QUIT:
        return Action(ActionKind.QUIT)

    return Action(
        ActionKind.SHOW,
        text=f"{SYSTEM_PREFIX} perintah tidak dikenal. Ketik /help untuk bantuan.",
    )


def format_message(
    message: Mapping[str, Any],
    *,
    nickname: str = "",
    local_time: bool = True,
) -> str | None:
    """Render one inbound message as a line for the user.

    Args:
        message: the decoded envelope, as it comes off the wire.
        nickname: this client's own nickname, used to tell an outgoing private
            message from an incoming one.
        local_time: convert the server's UTC timestamp to the machine's local
            time. On by default because the alternative -- showing a timestamp
            several hours off from the user's clock -- reads as a bug.

    Returns:
        The text to display, or ``None`` for messages that carry no user-visible
        content (handshake acknowledgements, heartbeats, the roster echoed back
        as part of the handshake).

    Never raises on a malformed payload: a client that crashes on a bad frame
    from the server is worse than one that prints a placeholder, and the frame
    has already passed schema validation by the time it arrives here.
    """
    message_type = message.get("type")
    payload = message.get("payload") or {}
    sender = message.get("sender", "")
    stamp = _stamp(message.get("timestamp"), local_time=local_time)

    if message_type is MessageType.BROADCAST:
        return f"{stamp} <{sender}> {_text(payload)}"

    if message_type is MessageType.PRIVATE:
        if payload.get("to") == nickname:
            # The sender's own copy carries ``to``; the recipient's does not.
            # That asymmetry is what lets one message type render both ways.
            return f"{stamp} \u2192 {payload['to']}: {_text(payload)}"
        return f"{stamp} <{sender}> (pribadi) {_text(payload)}"

    if message_type is MessageType.USER_JOIN:
        return f"{SYSTEM_PREFIX} {payload.get('nick', '?')} bergabung"

    if message_type is MessageType.USER_LEAVE:
        return f"{SYSTEM_PREFIX} {payload.get('nick', '?')} keluar"

    if message_type is MessageType.USER_LIST:
        return _format_user_list(payload)

    if message_type is MessageType.NICK_OK:
        return f"{SYSTEM_PREFIX} nickname diganti menjadi {payload.get('nick', '?')}"

    if message_type is MessageType.ERROR:
        code = payload.get("code", "ERROR")
        detail = payload.get("message", "")
        return f"{SYSTEM_PREFIX} error [{code}] {detail}".rstrip()

    if message_type is MessageType.DISCONNECT:
        return f"{SYSTEM_PREFIX} server menutup koneksi"

    return None


def _format_user_list(payload: Mapping[str, Any]) -> str:
    """Render the roster as a count plus one name per line."""
    users = payload.get("users") or []
    names = [entry.get("nick", "?") for entry in users]
    if not names:
        return f"{SYSTEM_PREFIX} belum ada user online"
    header = f"{SYSTEM_PREFIX} {len(names)} user online:"
    return "\n".join([header, *(f"    - {name}" for name in names)])


def _text(payload: Mapping[str, Any]) -> str:
    """Pull the chat body out, degrading to a placeholder rather than raising."""
    try:
        return extract_text(payload)
    except Exception:  # noqa: BLE001 - a bad frame must not end the session
        return "(pesan tidak dapat dibaca)"


def _stamp(timestamp: Any, *, local_time: bool) -> str:
    """Format a wire timestamp as ``HH:MM:SS`` in the reader's own clock."""
    if not isinstance(timestamp, str):
        return "--:--:--"
    try:
        moment = parse_iso8601(timestamp)
    except ValueError:
        return "--:--:--"
    if local_time:
        moment = moment.astimezone()
    return moment.strftime("%H:%M:%S")
