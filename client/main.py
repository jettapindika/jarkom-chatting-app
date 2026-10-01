"""CLI client entry point: connect, then read and write at the same time.

The two directions live on two threads. The main thread owns the keyboard and
spends its life inside ``input()``, which cannot be interrupted and cannot be
polled; the receiver thread owns the socket. Anything less than two threads means
one direction starves the other, which is exactly the failure the assignment
asks to avoid.

Ctrl+C is handled by letting ``KeyboardInterrupt`` propagate out of ``input()``
rather than by a signal handler that sets a flag. A handler that returns without
raising does not end the blocked read -- CPython retries the syscall -- so the
user would press Ctrl+C and see nothing happen until they also pressed Enter.
The exception arrives on the main thread between two reads, which is exactly
where leaving is safe, and the ``finally`` block still says goodbye.
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import Any, Sequence

from app.chat_logic import ActionKind, format_message, plan
from app.commands import parse_input
from app.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    HEARTBEAT_INTERVAL_ENV,
    HOST_ENV,
    PORT_ENV,
    env_flag,
    env_float,
    env_int,
    env_str,
)
from client.connection import ConnectFailure, open_session
from client.receiver import Receiver
from client.ui import Ui
from presentation import MessageType
from session.client_session import ClientSession
from session.errors import SessionStateError
from session.heartbeat import HeartbeatPolicy
from trace import JsonLinesTraceSink, Node, TraceEmitter
from trace.emitter import TRACE_ENABLED_ENV
from transport import ConnectionClosedError, FrameTooLargeError

__all__ = ["NICK_ENV", "build_parser", "main"]

#: Client-side liveness deadline, in multiples of the ping interval. The same
#: factor the server uses, so both ends give up on the same schedule and a demo
#: cannot end with one side still believing the other is there.
HEARTBEAT_TIMEOUT_FACTOR = 3.0

#: Nickname default, for a scripted demo that should not sit at a prompt.
NICK_ENV = "CHAT_NICK"


def build_parser() -> argparse.ArgumentParser:
    """The client's command line."""
    parser = argparse.ArgumentParser(
        prog="run_client.py",
        description="Multi-user chat client (TCP, custom application-layer protocol).",
    )
    parser.add_argument(
        "--host",
        default=env_str(HOST_ENV, DEFAULT_HOST),
        help=f"server address (default: ${HOST_ENV} or {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_int(PORT_ENV, DEFAULT_PORT),
        help=f"server port (default: ${PORT_ENV} or {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--nick",
        default=env_str(NICK_ENV, ""),
        help=f"nickname to connect with (default: ${NICK_ENV}; otherwise asked at startup)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=env_float(HEARTBEAT_INTERVAL_ENV, 15.0),
        help="seconds of silence before probing the server (default: 15)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        default=env_flag(TRACE_ENABLED_ENV),
        help=f"emit per-layer trace events as JSON lines on stderr (default: ${TRACE_ENABLED_ENV})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, connect, and run the chat loop. Returns an exit code."""
    args = build_parser().parse_args(argv)

    emitter = None
    if args.trace:
        # Trace goes to stderr, so it can be redirected into a replay file for
        # the visualizer without the conversation being mixed into it.
        emitter = TraceEmitter(JsonLinesTraceSink(sys.stderr), node=Node.CLIENT)

    ui = Ui()
    nickname = args.nick or _ask_nickname()
    if not nickname:
        ui.error("*** nickname tidak boleh kosong")
        return 2

    try:
        session = open_session(args.host, args.port, nickname, emitter=emitter)
    except ConnectFailure as exc:
        ui.error(f"*** gagal terhubung: {exc}")
        return 1

    policy = HeartbeatPolicy(
        interval=args.heartbeat_interval,
        timeout=args.heartbeat_interval * HEARTBEAT_TIMEOUT_FACTOR,
    )

    ui.banner(
        f"*** terhubung ke {args.host}:{args.port} sebagai {session.nickname} "
        f"(session {session.session_id[:8]})"
    )
    ui.banner("*** ketik /help untuk daftar perintah, /quit untuk keluar")

    return _chat_loop(session, ui, policy)


def _ask_nickname() -> str:
    """Ask for a nickname when none was given on the command line."""
    try:
        return input("Nickname: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _chat_loop(session: ClientSession, ui: Ui, policy: HeartbeatPolicy) -> int:
    """Read the keyboard and the socket until either side ends the session."""
    stop = threading.Event()

    def on_message(message: dict[str, Any]) -> None:
        """Render one inbound message. Runs on the receiver thread."""
        if message["type"] is MessageType.NICK_OK:
            # Adopt the confirmed name, so the next outbound frame and the
            # rendering of our own private messages both use it.
            session.rename(str(message["payload"].get("nick", session.nickname)))

        line = format_message(message, nickname=session.nickname)
        if line is None:
            return
        if message["type"] in _CHAT_TYPES:
            ui.chat(line)
        else:
            ui.system(line)

    def on_finish(reason: str | None) -> None:
        """Report why the session ended. Runs on the receiver thread."""
        if reason is not None:
            ui.error(f"*** {reason}")
        else:
            ui.banner("*** keluar dari chat")

    receiver = Receiver(
        session,
        on_message=on_message,
        on_finish=on_finish,
        policy=policy,
        stop_event=stop,
    )
    receiver.start()

    try:
        while not stop.is_set():
            ui.clear_line()
            try:
                line = input(ui.prompt)
            except (EOFError, KeyboardInterrupt):
                # Ctrl+D leaves a prompt; Ctrl+C discards the half-typed line.
                # Both mean "I am done", which is what /quit means too.
                break

            if not _handle(line, session, ui):
                break
    finally:
        # One close covers every exit path -- /quit, EOF, Ctrl+C, and the server
        # having gone away -- because ``ClientSession.close`` is idempotent and
        # best-effort, so an already-finished session is not an error.
        session.close()
        receiver.stop()
        receiver.join(timeout=2.0)
        ui.banner("*** selesai")

    return 0


#: Types that carry conversation, rendered as chat rather than as a notice.
_CHAT_TYPES = frozenset(
    {MessageType.BROADCAST, MessageType.PRIVATE, MessageType.USER_JOIN, MessageType.USER_LEAVE}
)

#: Failures a send can hit that mean "this message did not go out", as opposed to
#: a bug. Kept narrow so a genuine programming error still surfaces as a
#: traceback instead of being printed as a chat notice.
_SEND_FAILURES = (ConnectionClosedError, FrameTooLargeError, SessionStateError, OSError)


def _handle(line: str, session: ClientSession, ui: Ui) -> bool:
    """Act on one line of input. Returns ``False`` when the session should end."""
    action = plan(parse_input(line), nickname=session.nickname)

    if action.kind is ActionKind.QUIT:
        return False

    if action.kind is ActionKind.SHOW:
        ui.system(action.text)
        return True

    assert action.message is not None  # SEND always carries a message
    try:
        session.send(action.message)
    except _SEND_FAILURES as exc:
        ui.error(f"*** gagal mengirim: {exc}")
    return True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
