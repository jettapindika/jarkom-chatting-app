"""Connect N concurrent clients and measure what the server does under load.

The assignment asks for a simple stress run, so this deliberately stays simple:
one blocking client per thread, every client handshakes, broadcasts, and then
disconnects. It reports how many succeeded, how long the run took, and how many
broadcasts each client actually received, which is where a slow-consumer bug
would show up as a short count.

    python tools/stress_test.py --clients 50
    python tools/stress_test.py --clients 30 --host 127.0.0.1 --port 9009
"""

from __future__ import annotations

import argparse
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DEFAULT_HOST, DEFAULT_PORT, HOST_ENV, PORT_ENV, env_int, env_str  # noqa: E402
from presentation import MessageType, make_message  # noqa: E402
from session.client_session import ClientSession  # noqa: E402
from transport import BlockingTcpChannel, TransportTimeout  # noqa: E402

DEFAULT_CLIENTS = 30


class Result:
    __slots__ = ("nickname", "error", "received", "elapsed")

    def __init__(self, nickname: str) -> None:
        self.nickname = nickname
        self.error: str | None = None
        self.received = 0
        self.elapsed = 0.0


def run_client(
    host: str,
    port: int,
    index: int,
    barrier: threading.Barrier,
    hold: float,
    results: list[Result],
    results_lock: threading.Lock,
) -> None:
    result = Result(f"client{index:03d}")
    started = time.monotonic()
    session: ClientSession | None = None
    try:
        channel = BlockingTcpChannel.connect(host, port)
        session = ClientSession(channel, nickname=result.nickname)
        session.connect()

        # Everyone waits at the barrier so the broadcasts actually overlap
        # instead of trickling in one at a time.
        barrier.wait(timeout=30)

        session.send(
            make_message(MessageType.BROADCAST, {"text": f"halo dari {result.nickname}"}, sender=result.nickname)
        )

        deadline = time.monotonic() + hold
        while time.monotonic() < deadline:
            try:
                _header, message = session.receive(timeout=0.5)
            except TransportTimeout:
                continue
            if message["type"] is MessageType.BROADCAST:
                result.received += 1
    except Exception as exc:  # noqa: BLE001 - a stress run reports failures, it does not hide them
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.elapsed = time.monotonic() - started
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        with results_lock:
            results.append(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simple multi-client stress test.")
    parser.add_argument("--host", default=env_str(HOST_ENV, DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=env_int(PORT_ENV, DEFAULT_PORT))
    parser.add_argument("--clients", type=int, default=DEFAULT_CLIENTS)
    parser.add_argument("--hold", type=float, default=3.0, help="seconds each client stays connected")
    args = parser.parse_args(argv)

    if args.clients < 1:
        parser.error("--clients must be at least 1")

    # Fail fast with a clear message rather than 30 identical tracebacks.
    with socket.socket() as probe:
        probe.settimeout(5)
        try:
            probe.connect((args.host, args.port))
        except OSError as exc:
            print(f"cannot reach server at {args.host}:{args.port}: {exc}", file=sys.stderr)
            return 2

    barrier = threading.Barrier(args.clients)
    results: list[Result] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=run_client,
            args=(args.host, args.port, index, barrier, args.hold, results, lock),
            daemon=True,
        )
        for index in range(args.clients)
    ]

    print(f"connecting {args.clients} clients to {args.host}:{args.port}")
    started = time.monotonic()
    for thread in threads:
        thread.start()
        time.sleep(0.02)  # stagger the handshakes slightly; a stampede is a different test
    for thread in threads:
        thread.join(timeout=args.hold + 60)
    wall = time.monotonic() - started

    failures = [result for result in results if result.error]
    successes = [result for result in results if not result.error]
    print(f"\ncompleted in {wall:.2f}s")
    print(f"  connected successfully : {len(successes)}/{args.clients}")
    print(f"  failed                 : {len(failures)}")

    if successes:
        counts = [result.received for result in successes]
        print(f"  broadcasts received    : min {min(counts)}, median {statistics.median(counts):.0f}, max {max(counts)}")
        times = [result.elapsed for result in successes]
        print(f"  session lifetime       : min {min(times):.2f}s, max {max(times):.2f}s")

    for result in failures[:10]:
        print(f"  ! {result.nickname}: {result.error}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
