"""Dump the frames a client and server actually exchange on the wire.

Useful for the Wireshark part of the assignment: it shows the bytes our
application layer produces, so the capture can be read alongside the real
segments. Point it at a running server and it will connect, send a scripted
conversation, and print every frame in both directions with the three headers
pulled apart.

    python tools/frame_dump.py --port 9009 --nick dumper

Each line shows the 4-byte length prefix, the 24-byte session header, and the
JSON body, so the encapsulation is visible rather than asserted.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DEFAULT_HOST, DEFAULT_PORT, HOST_ENV, PORT_ENV, env_int, env_str  # noqa: E402
from util.hexdump import hex_dump  # noqa: E402

LENGTH_PREFIX_SIZE = 4
SESSION_HEADER_SIZE = 24


def recv_exactly(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes, or raise if the peer closed early."""
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(f"peer closed after {count - remaining} of {count} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> bytes:
    prefix = recv_exactly(sock, LENGTH_PREFIX_SIZE)
    (length,) = struct.unpack(">I", prefix)
    return prefix + recv_exactly(sock, length)


def describe(frame: bytes, *, direction: str) -> None:
    (length,) = struct.unpack(">I", frame[:LENGTH_PREFIX_SIZE])
    header = frame[LENGTH_PREFIX_SIZE : LENGTH_PREFIX_SIZE + SESSION_HEADER_SIZE]
    body = frame[LENGTH_PREFIX_SIZE + SESSION_HEADER_SIZE :]

    session_id = header[:16].hex()
    (sequence,) = struct.unpack(">Q", header[16:24])

    print(f"\n--- {direction} ({len(frame)} bytes on the wire) ---")
    print(f"L4 length prefix : {length} bytes ({frame[:4].hex(' ')})")
    print(f"L5 session id    : {session_id}")
    print(f"L5 sequence      : {sequence}")
    print(f"L6 body          : {length - SESSION_HEADER_SIZE} bytes of JSON")

    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"   body is not JSON ({exc}); raw bytes below")
    else:
        print(f"   {json.dumps(decoded, ensure_ascii=False, indent=2)}")

    print("L4 raw bytes:")
    print(hex_dump(frame, limit=96))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump chat protocol frames on the wire.")
    parser.add_argument("--host", default=env_str(HOST_ENV, DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=env_int(PORT_ENV, DEFAULT_PORT))
    parser.add_argument("--nick", default="dumper")
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="socket timeout while reading frames"
    )
    args = parser.parse_args(argv)

    from session.handshake import build_connect  # noqa: PLC0415
    from session.envelope import encode_envelope  # noqa: PLC0415
    from presentation import MessageType, make_message  # noqa: PLC0415

    with socket.create_connection((args.host, args.port), timeout=args.timeout) as sock:
        sock.settimeout(args.timeout)
        connect = build_connect(args.nick)
        frame = encode_envelope(
            json.dumps(connect, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        sock.sendall(struct.pack(">I", len(frame)) + frame)
        describe(struct.pack(">I", len(frame)) + frame, direction=f"OUTBOUND CONNECT as {args.nick}")

        reply = read_frame(sock)
        describe(reply, direction="INBOUND")

        broadcast = make_message(MessageType.BROADCAST, {"text": "hello from frame_dump"}, sender=args.nick)
        session_id = reply[LENGTH_PREFIX_SIZE : LENGTH_PREFIX_SIZE + 16].hex()
        body = json.dumps(broadcast, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame = encode_envelope(body, session_id=session_id, sequence=2)
        sock.sendall(struct.pack(">I", len(frame)) + frame)
        describe(struct.pack(">I", len(frame)) + frame, direction="OUTBOUND BROADCAST")

        while True:
            try:
                describe(read_frame(sock), direction="INBOUND")
            except (ConnectionError, socket.timeout, OSError):
                break

    print("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
