"""Server entry point: parse arguments, wire the stack, run until interrupted.

Run it directly::

    python run_server.py --host 0.0.0.0 --port 9009
    python run_server.py --trace            # emit per-layer trace to stderr

No address is hardcoded anywhere below: the defaults come from
:mod:`app.config`, which reads the environment, and ``argparse`` overrides them.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import Sequence

from app.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    HEARTBEAT_INTERVAL_ENV,
    HOST_ENV,
    MAX_CLIENTS_ENV,
    PORT_ENV,
    env_flag,
    env_float,
    env_int,
    env_str,
)
from server.chat_server import ChatServer, ServerConfig
from server.logger import configure_logging
from session.heartbeat import HeartbeatPolicy
from trace import JsonLinesTraceSink, Node, TraceEmitter
from trace.emitter import TRACE_ENABLED_ENV

__all__ = ["build_parser", "main"]

#: A peer is declared dead after this many missed heartbeat intervals. Three
#: allows two consecutive PINGs to be lost before the connection is torn down,
#: which is enough tolerance for a congested link without leaving a dead peer
#: occupying a nickname for long.
HEARTBEAT_TIMEOUT_FACTOR = 3.0


def build_parser() -> argparse.ArgumentParser:
    """The server's command line."""
    parser = argparse.ArgumentParser(
        prog="run_server.py",
        description="Multi-user chat server (TCP, custom application-layer protocol).",
    )
    parser.add_argument(
        "--host",
        default=env_str(HOST_ENV, DEFAULT_HOST),
        help=f"interface to bind (default: ${HOST_ENV} or {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_int(PORT_ENV, DEFAULT_PORT),
        help=f"TCP port to listen on (default: ${PORT_ENV} or {DEFAULT_PORT}; 0 picks a free one)",
    )
    parser.add_argument(
        "--max-clients",
        type=int,
        default=env_int(MAX_CLIENTS_ENV, 64),
        help=f"refuse connections beyond this many (default: ${MAX_CLIENTS_ENV} or 64)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=env_float(HEARTBEAT_INTERVAL_ENV, 15.0),
        help="seconds of outbound silence before PING (default: 15)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        default=env_flag(TRACE_ENABLED_ENV),
        help=f"emit per-layer trace events as JSON lines on stderr (default: ${TRACE_ENABLED_ENV})",
    )
    return parser


async def _run(config: ServerConfig) -> None:
    """Start the server and run until SIGINT or SIGTERM.

    Signals are handled through the event loop rather than by ``KeyboardInterrupt``
    so the shutdown path is the same one a test exercises: the loop stays alive
    to deliver the farewells, instead of unwinding out of ``asyncio.run`` with
    every connection still open.
    """
    server = ChatServer(config)
    await server.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is None:
            continue
        try:
            loop.add_signal_handler(signal_number, stop.set)
        except NotImplementedError:  # pragma: no cover - not POSIX
            pass

    serve = asyncio.create_task(server.serve_forever())
    await stop.wait()
    serve.cancel()
    await asyncio.gather(serve, return_exceptions=True)
    await server.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the server. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    emitter = None
    if args.trace:
        # Trace goes to stderr so it can be captured into a replay file for the
        # visualizer without the operational log getting mixed in.
        emitter = TraceEmitter(JsonLinesTraceSink(sys.stderr), node=Node.SERVER)

    heartbeat = HeartbeatPolicy(
        interval=args.heartbeat_interval,
        timeout=args.heartbeat_interval * HEARTBEAT_TIMEOUT_FACTOR,
    )
    config = ServerConfig(
        host=args.host,
        port=args.port,
        max_clients=args.max_clients,
        heartbeat=heartbeat,
        trace=emitter,
    )

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:  # pragma: no cover - the signal handler normally wins
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
